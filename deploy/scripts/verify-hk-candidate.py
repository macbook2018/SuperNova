#!/usr/bin/env python3
"""Read-only GitHub deployment gate. Execute only from the reviewed default-branch checkout."""

import base64
import hashlib
import json
import os
import re
import sys
from urllib.error import HTTPError
from urllib.parse import quote, urlencode
from urllib.request import HTTPRedirectHandler, Request, build_opener

REPO = "macbook2018/SuperNova"
BASELINE = "feat/phase-6-knowledge-base-rag"
A1 = "feat/rag-65a-versioned-index"
A2 = "feat/rag-65a-structured-chunking"
BASE_FOR = {A1: BASELINE, A2: A1}
CI_PATH = ".github/workflows/ci.yml"
# Reviewed Phase 6 CI plus the A1 version publishing / rollback Compose smoke test.
# Changes to this file require a separate reviewed pin update on the control branch.
CI_SHA256 = {
    A1: "4872cba7f590c6f3ccebbd8f7bfe90c6935da6c4eae7f0c48a747948bc596878",
    A2: "16808f261878ce8300150bc962dadd5b1d296be73765bd7ab61ec06f039e7041",
}
REQUIRED_JOBS = {
    "Console typecheck and production build": {
        "Typecheck and build console",
    },
    "API lint, tests and migrations": {"Ruff", "Tests", "Database migrations"},
    "Docker Compose build and smoke test": {
        "Start stack",
        "Run database migrations",
        "Verify API health",
        "Verify knowledge version publishing and rollback through HTTP",
        "Verify Chinese console",
    },
}


class Rejected(Exception):
    pass


def require(condition, message):
    if not condition:
        raise Rejected(message)


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise Rejected("GitHub API redirect rejected")


class GitHub:
    def __init__(self, token):
        self.token = token
        self.opener = build_opener(NoRedirect())

    def get(self, path, **params):
        url = f"https://api.github.com/repos/{REPO}{path}"
        if params:
            url += "?" + urlencode(params)
        request = Request(
            url,
            headers={
                "Authorization": f"Bearer {self.token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )
        with self.opener.open(request, timeout=30) as response:
            return json.load(response)

    def rows(self, path, key=None, **params):
        for page in range(1, 101):
            result = self.get(path, **params, per_page=100, page=page)
            rows = result[key] if key else result
            yield from rows
            if len(rows) < 100:
                return
        raise Rejected("GitHub pagination limit exceeded")


def branch_tip(api, branch):
    ref = api.get("/git/ref/heads/" + quote(branch, safe="/"))
    require(
        ref["ref"] == f"refs/heads/{branch}" and ref["object"]["type"] == "commit",
        "Invalid Git ref",
    )
    return ref["object"]["sha"]


def can_write(api, login):
    require(re.fullmatch("[a-zA-Z0-9][a-zA-Z0-9-]{0,38}", login), "Invalid GitHub account")
    permission = api.get(f"/collaborators/{quote(login, safe='')}/permission")["permission"]
    return permission in {"admin", "maintain", "write"}


def check_review(api, pr, sha, attested):
    latest = {}
    reviews = sorted(
        api.rows(f"/pulls/{pr['number']}/reviews"),
        key=lambda r: (r.get("submitted_at") or "", r["id"]),
    )
    for review in reviews:
        user = review["user"]
        if user["id"] == pr["user"]["id"]:
            continue
        if review["state"] in {"APPROVED", "CHANGES_REQUESTED", "DISMISSED"}:
            latest[user["id"]] = review
    approved = False
    for review in latest.values():
        if review["state"] == "DISMISSED":
            continue
        require(review["state"] != "CHANGES_REQUESTED", "A reviewer has requested changes")
        if review["user"]["type"] == "User" and review.get("commit_id") == sha:
            approved |= can_write(api, review["user"]["login"])
    require(
        approved or attested,
        "Missing independent approval or exact-SHA maintainer signoff",
    )


def check_ci(api, branch, sha):
    config = api.get(f"/contents/{CI_PATH}", ref=sha)
    require(
        config["type"] == "file" and config["encoding"] == "base64",
        "Invalid CI workflow",
    )
    contents = base64.b64decode("".join(config["content"].split()), validate=True)
    require(
        hashlib.sha256(contents).hexdigest() == CI_SHA256[branch],
        "CI workflow does not match the reviewed control-branch pin",
    )
    workflow = api.get("/actions/workflows/ci.yml")
    require(
        workflow["path"] == CI_PATH and workflow["state"] == "active",
        "CI workflow unavailable",
    )
    workflow_id = int(workflow["id"])
    runs = api.get(
        f"/actions/workflows/{workflow_id}/runs",
        branch=branch,
        event="push",
        head_sha=sha,
        per_page=1,
    )["workflow_runs"]
    require(len(runs) == 1, "Missing candidate push CI run")
    run = runs[0]
    require(
        run["workflow_id"] == workflow_id
        and run["path"] == CI_PATH
        and run["event"] == "push"
        and run["name"] == "CI"
        and run["head_sha"] == sha
        and run["head_branch"] == branch
        and run["repository"]["full_name"] == REPO
        and run["head_repository"]["full_name"] == REPO
        and run["status"] == "completed"
        and run["conclusion"] == "success",
        "Candidate push CI has not completed successfully",
    )
    jobs = list(
        api.rows(
            f"/actions/runs/{int(run['id'])}/attempts/{int(run['run_attempt'])}/jobs",
            "jobs",
        )
    )
    for name, required_steps in REQUIRED_JOBS.items():
        selected = [job for job in jobs if job["name"] == name]
        require(len(selected) == 1, f"Missing or ambiguous required CI job: {name}")
        job = selected[0]
        require(
            job["run_id"] == run["id"]
            and job["head_sha"] == sha
            and job["status"] == "completed"
            and job["conclusion"] == "success",
            f"Required CI job did not succeed: {name}",
        )
        passed = {
            step["name"]
            for step in job["steps"]
            if step["status"] == "completed" and step["conclusion"] == "success"
        }
        require(
            required_steps <= passed,
            f"Expected CI steps were skipped or failed: {name}",
        )
    staging = [job for job in jobs if job["name"] == "Transfer and deploy staging images"]
    require(
        len(staging) == 1
        and staging[0]["run_id"] == run["id"]
        and staging[0]["head_sha"] == sha
        and staging[0]["status"] == "completed"
        and staging[0]["conclusion"] == "skipped",
        "Baseline staging deployment was not skipped for this candidate",
    )
    return run["id"]


def check_pr(api, number, expected_sha, *, stage=None, signoff=""):
    pr = api.get(f"/pulls/{number}")
    require(pr["number"] == number and pr["state"] == "open", "Candidate PR must be open")
    branch = pr["head"]["ref"]
    require(
        branch in BASE_FOR and (stage is None or branch == stage),
        "Unexpected head branch",
    )
    base = BASE_FOR[branch]
    require(
        pr["base"]["ref"] == base
        and pr["base"]["repo"]["full_name"] == REPO
        and pr["head"]["repo"]["full_name"] == REPO,
        "Unexpected PR repository or base branch",
    )
    require(
        pr["head"]["sha"] == expected_sha == branch_tip(api, branch),
        "Candidate head changed",
    )
    base_sha = branch_tip(api, base)
    require(pr["base"]["sha"] == base_sha, "Candidate base changed")
    comparison = api.get(f"/compare/{base_sha}...{expected_sha}", per_page=1)
    require(
        comparison["status"] == "ahead"
        and comparison["behind_by"] == 0
        and comparison["merge_base_commit"]["sha"] == base_sha,
        "Candidate does not include the current base branch tip",
    )
    expected_signoff = f"REVIEWED {expected_sha}"
    if branch == A2:
        expected_signoff += f" WITH A1 {base_sha}"
    if signoff:
        require(
            signoff == expected_signoff,
            "Maintainer signoff does not match the current exact commit(s)",
        )
    require(signoff or pr["draft"] is False, "Draft PR requires explicit maintainer signoff")
    if branch == A2:
        parents = list(api.rows("/pulls", state="open", head=f"macbook2018:{A1}", base=BASELINE))
        require(
            len(parents) == 1,
            "A2 needs exactly one open A1 PR into the Phase 6 baseline",
        )
        check_pr(
            api,
            int(parents[0]["number"]),
            base_sha,
            stage=A1,
            signoff=f"REVIEWED {base_sha}" if signoff else "",
        )
    check_review(api, pr, expected_sha, bool(signoff))
    run_id = check_ci(api, branch, expected_sha)
    print(f"Verified PR #{number} at {expected_sha} (push CI run {run_id})")


def main():
    require(
        os.environ["GITHUB_EVENT_NAME"] == "workflow_dispatch",
        "Manual dispatch required",
    )
    require(
        os.environ["GITHUB_REPOSITORY"] == REPO and os.environ["GITHUB_REF"] == "refs/heads/main",
        "Dispatch must run from the trusted default branch",
    )
    candidate = os.environ["CANDIDATE_SHA"]
    number = os.environ["PR_NUMBER"]
    require(re.fullmatch("[0-9a-f]{40}", candidate), "Supply a full lowercase commit SHA")
    require(re.fullmatch("[1-9][0-9]{0,9}", number), "Invalid PR number")
    api = GitHub(os.environ["GH_TOKEN"])
    require(
        api.get("")["default_branch"] == "main"
        and branch_tip(api, "main") == os.environ["GITHUB_SHA"],
        "Default control branch has moved; dispatch its current revision",
    )
    for actor in {os.environ["GITHUB_ACTOR"], os.environ["GITHUB_TRIGGERING_ACTOR"]}:
        require(
            can_write(api, actor),
            "Dispatch and rerun actors must both have repository write access",
        )
    check_pr(api, int(number), candidate, signoff=os.environ.get("SIGNOFF", ""))


if __name__ == "__main__":
    try:
        main()
    except HTTPError as exc:
        sys.exit(f"Candidate gate refused: GitHub API returned HTTP {exc.code}")
    except (Rejected, KeyError, TypeError, ValueError, OSError) as exc:
        sys.exit(f"Candidate gate refused: {exc}")
