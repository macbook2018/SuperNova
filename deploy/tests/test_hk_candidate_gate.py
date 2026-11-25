"""Offline metadata tests. No GitHub credentials or remote deployment are used."""

import base64
import hashlib
import importlib.util
import os
import unittest
from pathlib import Path
from unittest.mock import patch

PATH = Path(__file__).resolve().parents[1] / "scripts/verify-hk-candidate.py"
spec = importlib.util.spec_from_file_location("candidate_gate", PATH)
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)

A1_SHA, A2_SHA, BASE_SHA, CONTROL_SHA = (ch * 40 for ch in "1234")
STAGING_JOB = "Transfer and deploy staging images"


def pr(number, branch, sha, base, parent):
    return {
        "number": number,
        "state": "open",
        "draft": False,
        "user": {"id": 1},
        "head": {"ref": branch, "sha": sha, "repo": {"full_name": gate.REPO}},
        "base": {"ref": base, "sha": parent, "repo": {"full_name": gate.REPO}},
    }


def review(sha):
    return {
        "user": {"login": "reviewer", "id": 2, "type": "User"},
        "id": 23,
        "state": "APPROVED",
        "commit_id": sha,
        "submitted_at": "2026-01-01T00:00:00Z",
    }


class Metadata:
    def __init__(self):
        self.prs = {
            32: pr(32, gate.A1, A1_SHA, gate.BASELINE, BASE_SHA),
            33: pr(33, gate.A2, A2_SHA, gate.A1, A1_SHA),
        }
        self.tips = {
            "main": CONTROL_SHA,
            gate.BASELINE: BASE_SHA,
            gate.A1: A1_SHA,
            gate.A2: A2_SHA,
        }
        self.reviews = {32: [review(A1_SHA)], 33: [review(A2_SHA)]}
        self.workflows = {
            A1_SHA: b"reviewed A1 workflow",
            A2_SHA: b"reviewed A2 workflow",
        }
        self.ancestry = "ahead"
        self.permissions = {
            "reviewer": "write",
            "dispatcher": "admin",
            "rerunner": "maintain",
        }
        self.event = "push"
        self.stage_result = "skipped"
        self.skipped_step = ""

    def get(self, path, **query):
        if path == "":
            return {"default_branch": "main"}
        if path.startswith("/pulls/"):
            return self.prs[int(path.split("/")[2])]
        if path.startswith("/git/ref/heads/"):
            branch = path.removeprefix("/git/ref/heads/")
            return {
                "ref": f"refs/heads/{branch}",
                "object": {"type": "commit", "sha": self.tips[branch]},
            }
        if path.startswith("/compare/"):
            return {
                "status": self.ancestry,
                "behind_by": 0,
                "merge_base_commit": {"sha": path.split("/")[-1].split("...")[0]},
            }
        if path.startswith("/collaborators/"):
            return {"permission": self.permissions[path.split("/")[2]]}
        if path.startswith("/contents/"):
            return {
                "type": "file",
                "encoding": "base64",
                "content": base64.b64encode(self.workflows[query["ref"]]).decode(),
            }
        if path == "/actions/workflows/ci.yml":
            return {"id": 7, "state": "active", "path": gate.CI_PATH}
        if path == "/actions/workflows/7/runs":
            run_id = {A1_SHA: 101, A2_SHA: 102}[query["head_sha"]]
            return {
                "workflow_runs": [
                    {
                        "id": run_id,
                        "run_attempt": 1,
                        "workflow_id": 7,
                        "path": gate.CI_PATH,
                        "name": "CI",
                        "event": self.event,
                        "head_sha": query["head_sha"],
                        "head_branch": query["branch"],
                        "repository": {"full_name": gate.REPO},
                        "head_repository": {"full_name": gate.REPO},
                        "status": "completed",
                        "conclusion": "success",
                    }
                ]
            }
        raise AssertionError(path)

    def rows(self, path, key=None, **query):
        if path == "/pulls":
            return [self.prs[32]]
        if path.endswith("/reviews"):
            return self.reviews[int(path.split("/")[2])]
        if path.endswith("/attempts/1/jobs"):
            run_id = int(path.split("/")[3])
            fields = {
                "run_id": run_id,
                "head_sha": {101: A1_SHA, 102: A2_SHA}[run_id],
                "status": "completed",
            }
            jobs = [
                dict(
                    fields,
                    name=name,
                    conclusion="success",
                    steps=[
                        {
                            "name": step,
                            "status": "completed",
                            "conclusion": "skipped" if self.skipped_step == step else "success",
                        }
                        for step in steps
                    ],
                )
                for name, steps in gate.REQUIRED_JOBS.items()
            ]
            return [
                *jobs,
                dict(fields, name=STAGING_JOB, conclusion=self.stage_result, steps=[]),
            ]
        raise AssertionError(path)


class CandidateGateTest(unittest.TestCase):
    def setUp(self):
        self.api = Metadata()
        pins = {
            stage: hashlib.sha256(self.api.workflows[sha]).hexdigest()
            for stage, sha in [(gate.A1, A1_SHA), (gate.A2, A2_SHA)]
        }
        pin_patch = patch.object(gate, "CI_SHA256", pins)
        pin_patch.start()
        self.addCleanup(pin_patch.stop)

    def check(self, *, signoff=""):
        gate.check_pr(self.api, 33, A2_SHA, signoff=signoff)

    def test_accepts_approved_stack(self):
        self.check()

    def test_draft_stack_requires_exact_signoff_for_both_heads(self):
        for item in self.api.prs.values():
            item["draft"] = True
        self.api.reviews = {32: [], 33: []}
        for wrong in (
            "",
            f"REVIEWED {A2_SHA}",
            f"REVIEWED {A2_SHA} WITH A1 {BASE_SHA}",
        ):
            with self.subTest(wrong=wrong), self.assertRaises(gate.Rejected):
                self.check(signoff=wrong)
        self.check(signoff=f"REVIEWED {A2_SHA} WITH A1 {A1_SHA}")
        gate.check_pr(self.api, 32, A1_SHA, signoff=f"REVIEWED {A1_SHA}")

    def test_rejects_invalid_signoff_even_with_independent_approvals(self):
        with self.assertRaises(gate.Rejected):
            self.check(signoff="REVIEWED latest")

    def test_stale_parent_approval_rejected(self):
        self.api.reviews[32][0]["commit_id"] = BASE_SHA
        with self.assertRaises(gate.Rejected):
            self.check()

    def test_negative_parent_review_blocks_explicit_signoff(self):
        self.api.reviews[32][0]["state"] = "CHANGES_REQUESTED"
        with self.assertRaises(gate.Rejected):
            self.check(signoff=f"REVIEWED {A2_SHA} WITH A1 {A1_SHA}")

    def test_parent_must_include_current_baseline(self):
        self.api.ancestry = "diverged"
        with self.assertRaises(gate.Rejected):
            gate.check_pr(self.api, 32, A1_SHA)

    def test_rejects_fork_parent_or_moved_tip(self):
        self.api.prs[32]["head"]["repo"]["full_name"] = "fork/SuperNova"
        with self.assertRaises(gate.Rejected):
            self.check()
        self.api.prs[32]["head"]["repo"]["full_name"] = gate.REPO
        self.api.tips[gate.A2] = BASE_SHA
        with self.assertRaises(gate.Rejected):
            self.check()

    def test_stage_workflow_hashes_are_not_interchangeable(self):
        self.api.workflows[A2_SHA] = self.api.workflows[A1_SHA]
        with self.assertRaises(gate.Rejected):
            self.check()

    def test_rejects_forged_success_skips_and_baseline_deploy(self):
        for attr, value in (
            ("event", "pull_request"),
            ("skipped_step", "Tests"),
            ("stage_result", "success"),
        ):
            with (
                self.subTest(attr=attr),
                patch.object(self.api, attr, value),
                self.assertRaises(gate.Rejected),
            ):
                self.check()

    def test_manual_dispatch_and_both_actor_permissions(self):
        env = {
            "GITHUB_EVENT_NAME": "workflow_dispatch",
            "GITHUB_REPOSITORY": gate.REPO,
            "GITHUB_REF": "refs/heads/main",
            "GITHUB_SHA": CONTROL_SHA,
            "GITHUB_ACTOR": "dispatcher",
            "GITHUB_TRIGGERING_ACTOR": "rerunner",
            "CANDIDATE_SHA": A1_SHA,
            "PR_NUMBER": "32",
            "GH_TOKEN": "offline-fixture",
            "SIGNOFF": f"REVIEWED {A1_SHA}",
        }
        with (
            patch.dict(os.environ, env, clear=True),
            patch.object(gate, "GitHub", return_value=self.api),
        ):
            gate.main()
            for login in ("dispatcher", "rerunner"):
                with (
                    patch.dict(self.api.permissions, {login: "read"}),
                    self.assertRaises(gate.Rejected),
                ):
                    gate.main()
            with (
                patch.dict(os.environ, {"GITHUB_EVENT_NAME": "pull_request"}),
                self.assertRaises(gate.Rejected),
            ):
                gate.main()


if __name__ == "__main__":
    unittest.main()
