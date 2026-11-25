"""Host controller for the isolated Hong Kong RAG candidate. Requires Linux and Compose v2."""

import argparse
import base64
import contextlib
import fcntl
import hashlib
import json
import os
import re
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import tarfile
import time
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path("/opt/supernova-hk-candidate")
SHARED_LOCK = Path("/opt/supernova-staging/.deploy-staging.lock")
HERE = Path(__file__).resolve().parent
RELEASES = ROOT / "releases"
APP = ["api", "knowledge-worker", "chat-import-worker", "console"]
DATA = ["postgres", "redis", "silo"]
VOLUMES = ["pgdata", "redisdata", "silodata"]
INFRA = {
    "HK_POSTGRES_IMAGE": "pgvector/pgvector:pg16",
    "HK_REDIS_IMAGE": "redis:7-alpine",
    "HK_SILO_IMAGE": "pgsty/silo:RELEASE.2026-11-09T00-00-00Z",
    "HK_MC_IMAGE": "minio/mc:RELEASE.2025-08-13T08-35-41Z",
}
GIB = 1024**3


def command(args, *, capture=False, **kwargs):
    # Compose must not inherit another target, env-file override, or machine's staged tokens.
    env = {
        key: os.environ[key]
        for key in ("PATH", "HOME", "LANG", "XDG_RUNTIME_DIR")
        if key in os.environ
    }
    env["DOCKER_HOST"] = "unix:///var/run/docker.sock"
    return subprocess.run(
        args,
        env=env,
        check=True,
        text=True,
        stdout=subprocess.PIPE if capture else kwargs.pop("stdout", None),
        **kwargs,
    )


def atomic(path, value):
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w") as out:
        out.write(value)
        out.flush()
        os.fsync(out.fileno())
    temporary.replace(path)
    fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def init():
    ROOT.mkdir(mode=0o700, exist_ok=True)
    if ROOT.resolve() != ROOT or ROOT.stat().st_uid != os.getuid() or ROOT.stat().st_mode & 0o077:
        raise ValueError(f"{ROOT} must be private (0700), owned by the deploy account")
    RELEASES.mkdir(mode=0o700, exist_ok=True)
    path = ROOT / "secrets.env"
    if path.exists():
        if path.is_symlink() or path.stat().st_mode & 0o077 or path.stat().st_uid != os.getuid():
            raise ValueError("Invalid candidate secrets file ownership or permissions")
        return
    generated = {
        "HK_PG_PASSWORD": secrets.token_hex(24),
        "HK_REDIS_PASSWORD": secrets.token_hex(24),
        "HK_SILO_ROOT_USER": "hkroot" + secrets.token_hex(6),
        "HK_SILO_ROOT_PASSWORD": secrets.token_hex(32),
        "HK_S3_ACCESS_KEY": "hkapp" + secrets.token_hex(6),
        "HK_S3_SECRET_KEY": secrets.token_hex(32),
        "HK_JWT_SECRET": secrets.token_hex(32),
        "HK_ENCRYPTION_KEY": base64.urlsafe_b64encode(secrets.token_bytes(32)).decode(),
        "HK_ADMIN_EMAILS": "",
        "HK_MODEL_CREDENTIALS_JSON": "'{}'",
    }
    # Only a complete file is published, even if two administrators initialize concurrently.
    temp = ROOT / (".credentials-" + secrets.token_hex(8))
    try:
        atomic(temp, "".join(f"{k}={v}\n" for k, v in generated.items()))
        try:
            os.link(temp, path)
        except FileExistsError:
            pass
    finally:
        temp.unlink(missing_ok=True)


def project_name(value):
    if not re.fullmatch(r"sn-hkc-[0-9a-f]{12}-[0-9a-f]{12}", value):
        raise ValueError("Invalid candidate project name")
    return value


def current():
    p = ROOT / "current"
    return project_name(p.read_text().strip()) if p.exists() else None


def compose(project, *args, **kwargs):
    folder = RELEASES / project_name(project)
    return command(
        [
            "docker",
            "compose",
            "-p",
            project,
            "--env-file",
            str(folder / ".env"),
            "-f",
            str(folder / "compose.yaml"),
            *args,
        ],
        **kwargs,
    )


def inspect_image(ref):
    return json.loads(command(["docker", "image", "inspect", ref], capture=True).stdout)[0]


def archive_check(archive, sha, checksum, app_ids):
    if not re.fullmatch(r"[0-9a-f]{40}", sha) or not re.fullmatch(r"[0-9a-f]{64}", checksum):
        raise ValueError("Expected full commit SHA and SHA-256 archive checksum")
    if any(not re.fullmatch(r"sha256:[0-9a-f]{64}", i) for i in app_ids.values()):
        raise ValueError("Invalid image configuration digest")
    digest = hashlib.sha256()
    with archive.open("rb") as stream:
        for part in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            digest.update(part)
    if digest.hexdigest() != checksum:
        raise ValueError("Image archive checksum mismatch")
    targets = {f"supernova-hk-candidate/{role}:{sha}": digest for role, digest in app_ids.items()}
    tags = {}
    with tarfile.open(archive, mode="r:") as bundle:
        manifest = json.load(bundle.extractfile("manifest.json"))
        if len(manifest) != 2:
            raise ValueError("Expected exactly the two candidate images")
        for item in manifest:
            conf = bundle.extractfile(item["Config"]).read(16 * 1024 * 1024)
            image_digest = "sha256:" + hashlib.sha256(conf).hexdigest()
            if (
                json.loads(conf)
                .get("config", {})
                .get("Labels", {})
                .get("org.opencontainers.image.revision")
                != sha
            ):
                raise ValueError("Commit label does not match the reviewed SHA")
            if len(item["RepoTags"]) != 1:
                raise ValueError("Unexpected extra image tag")
            tags[item["RepoTags"][0]] = image_digest
    if tags != targets:
        raise ValueError("Image names or configuration digests do not match the reviewed images")


def pin_infra(previous):
    if previous:
        return json.loads((RELEASES / previous / "manifest.json").read_text())["infra"]
    result = {}
    arch = command(
        ["docker", "version", "--format", "{{.Server.Arch}}"], capture=True
    ).stdout.strip()
    for key, ref in INFRA.items():
        repository = ref.rsplit(":", 1)[0]
        canonical = repository if "/" in repository else "library/" + repository
        aliases = {repository, canonical, "docker.io/" + canonical, "index.docker.io/" + canonical}
        local = command(["docker", "image", "ls", "-q", ref], capture=True).stdout.strip()
        hashes = inspect_image(ref).get("RepoDigests") or [] if local else []
        existing = [
            d.split("@")[1]
            for d in hashes
            if d.split("@")[0] in aliases and re.fullmatch(r".+@sha256:[0-9a-f]{64}", d)
        ]
        if existing:
            digest = existing[0]
        else:
            # Resolve the registry reference without writing a mutable shared Docker tag.
            response = json.loads(
                command(["docker", "manifest", "inspect", "--verbose", ref], capture=True).stdout
            )
            rows = response if isinstance(response, list) else [response]
            matches = [
                row["Descriptor"]["digest"]
                for row in rows
                if row["Descriptor"].get("platform", {}).get("os") == "linux"
                and row["Descriptor"]["platform"].get("architecture") == arch
            ]
            if len(matches) != 1 or not re.fullmatch(r"sha256:[0-9a-f]{64}", matches[0]):
                raise ValueError(f"No unique Linux {arch} registry digest for {ref}")
            digest = matches[0]
        immutable = f"{repository}@{digest}"
        if not existing:
            command(["docker", "pull", immutable])
        result[key] = immutable
    return result


def verify_local_images(project):
    manifest = json.loads((RELEASES / project / "manifest.json").read_text())
    for role, image_id in manifest["app_ids"].items():
        inspected = inspect_image(manifest["image_tags"][role])
        if (
            inspected["Id"] != image_id
            or inspected["Config"]["Labels"].get("org.opencontainers.image.revision")
            != manifest["sha"]
        ):
            raise ValueError("Saved candidate image does not match its approved SHA/digest")
    return manifest


def volume(project, name):
    ref = project_name(project) + "_" + name
    labels = (
        json.loads(
            command(
                ["docker", "volume", "inspect", "--format", "{{json .Labels}}", ref], capture=True
            ).stdout
        )
        or {}
    )
    if (
        labels.get("com.docker.compose.project") != project
        or labels.get("com.docker.compose.volume") != name
    ):
        raise ValueError("Volume ownership does not match this candidate")
    return ref


def volume_helper(image, mounts, script, *, capture=False):
    return command(
        [
            "docker",
            "run",
            "--rm",
            "--network",
            "none",
            "--read-only",
            *mounts,
            "--entrypoint",
            "/bin/sh",
            image,
            "-ec",
            script,
        ],
        capture=capture,
    )


def enough_capacity(previous, transfer_size, disk_root):
    # Includes process limits plus OS headroom. Swap is excluded from the available memory.
    mem = (
        int(
            next(
                s.split()[1]
                for s in Path("/proc/meminfo").read_text().splitlines()
                if s.startswith("MemAvailable:")
            )
        )
        * 1024
    )
    if len(os.sched_getaffinity(0)) < 2 or mem < 1600 * 1024**2:
        raise ValueError("Candidate needs two CPUs and at least 1600 MiB of available RAM")
    used = 0
    if previous:
        helper = verify_local_images(previous)["infra"]["HK_POSTGRES_IMAGE"]
        for key in VOLUMES:
            ref = volume(previous, key)
            stats = volume_helper(
                helper, ["-v", ref + ":/source:ro"], "du -sk /source", capture=True
            )
            used += int(stats.stdout.split()[0]) * 1024
    required = 2 * used + 2 * transfer_size + 2 * GIB
    if shutil.disk_usage(disk_root).free < required or shutil.disk_usage(ROOT).free < used + GIB:
        raise ValueError(
            f"Insufficient space for candidate backup and images (Docker needs {required} bytes)"
        )
    if not previous:
        for port in (18182, 18183):
            with socket.socket() as sock:
                sock.bind(("127.0.0.1", port))


def activate(project):
    verify_local_images(project)
    compose(
        project,
        "up",
        "-d",
        "--no-recreate",
        "--pull",
        "never",
        "--wait",
        "--wait-timeout",
        "240",
        *DATA,
    )
    compose(
        project,
        "up",
        "-d",
        "--no-recreate",
        "--pull",
        "never",
        "--wait",
        "--wait-timeout",
        "240",
        *APP,
    )
    for url in ("http://127.0.0.1:18182/health/ready", "http://127.0.0.1:18183/healthz"):
        command(
            [
                "curl",
                "--noproxy",
                "*",
                "--fail",
                "--silent",
                "--show-error",
                "--max-time",
                "8",
                url,
            ],
            stdout=subprocess.DEVNULL,
        )


def stop(project):
    # Explicit service order quiesces all writers before stopping the stores.
    oneoffs = command(
        [
            "docker",
            "ps",
            "-q",
            "--filter",
            f"label=com.docker.compose.project={project_name(project)}",
            "--filter",
            "label=com.docker.compose.oneoff=True",
        ],
        capture=True,
    ).stdout.splitlines()
    if oneoffs:
        command(["docker", "stop", "--time", "60", *oneoffs])
    compose(project, "stop", "--timeout", "60", *APP)
    compose(project, "stop", "--timeout", "60", *DATA)


def recover():
    journal = ROOT / "pending.json"
    if not journal.exists():
        return
    intent = json.loads(journal.read_text())
    before, after = intent["before"], intent["after"]
    if current() != after:
        stop(project_name(after))
        if before and intent["was_running"]:
            activate(project_name(before))
    journal.unlink()


def switch(before, after, apply):
    # The previous slot is a complete checkpoint, including its exact env and three data volumes.
    was_running = before is not None and not (RELEASES / before / "stopped").exists()
    if before and was_running:
        ids = compose(before, "ps", "-q", *APP, capture=True).stdout.splitlines()
        if len(ids) != len(APP):
            raise ValueError("Existing candidate is partially stopped; inspect it before switching")
    atomic(
        ROOT / "pending.json", json.dumps(dict(before=before, after=after, was_running=was_running))
    )
    try:
        if before:
            stop(before)
        apply()
        (RELEASES / after / "stopped").unlink(missing_ok=True)
        atomic(ROOT / "current", after + "\n")
        (ROOT / "pending.json").unlink()
    except BaseException:
        recover()
        raise


def deploy(sha, checksum, api_id, console_id):
    archive = HERE / "images.tar"
    app_ids = {"api": api_id, "console": console_id}
    archive_check(archive, sha, checksum, app_ids)
    before = current()
    enough_capacity(
        before,
        archive.stat().st_size,
        command(["docker", "info", "--format", "{{.DockerRootDir}}"], capture=True).stdout.strip(),
    )
    command(["docker", "load", "--input", str(archive)])
    infra = pin_infra(before)
    tags = {}
    for role, expected in app_ids.items():
        src = f"supernova-hk-candidate/{role}:{sha}"
        if inspect_image(src)["Id"] != expected:
            raise ValueError("Loaded image does not match the approved build")
        tags[role] = f"supernova-hk-sealed/{role}:" + expected.removeprefix("sha256:")
        command(["docker", "tag", expected, tags[role]])
    after = "sn-hkc-" + sha[:12] + "-" + secrets.token_hex(6)
    folder = RELEASES / after
    folder.mkdir()
    for file in ("compose.yaml", "hk-rag-candidate-host.sh", "controller.py"):
        shutil.copy2(HERE / file, folder / file)
    conf = (ROOT / "secrets.env").read_text()
    lines = {
        line.split("=", 1)[0] for line in conf.splitlines() if line and not line.startswith("#")
    }
    valid = {
        "HK_PG_PASSWORD",
        "HK_REDIS_PASSWORD",
        "HK_SILO_ROOT_USER",
        "HK_SILO_ROOT_PASSWORD",
        "HK_S3_ACCESS_KEY",
        "HK_S3_SECRET_KEY",
        "HK_JWT_SECRET",
        "HK_ENCRYPTION_KEY",
        "HK_ADMIN_EMAILS",
        "HK_MODEL_CREDENTIALS_JSON",
    }
    if lines != valid:
        raise ValueError("Unexpected or missing key in the dedicated candidate secrets file")
    if before:
        old = (RELEASES / before / ".env").read_text().splitlines()
        immutable = valid - {"HK_ADMIN_EMAILS", "HK_MODEL_CREDENTIALS_JSON"}
        expected = {line for line in old if line.split("=", 1)[0] in immutable}
        actual = {line for line in conf.splitlines() if line.split("=", 1)[0] in immutable}
        if expected != actual:
            raise ValueError(
                "Storage, database and encryption credentials do not match the candidate checkpoint"
            )

    variables = {**infra, "HK_API_IMAGE": tags["api"], "HK_CONSOLE_IMAGE": tags["console"]}
    atomic(
        folder / ".env", conf.rstrip() + "\n" + "".join(f"{k}={v}\n" for k, v in variables.items())
    )
    atomic(
        folder / "manifest.json",
        json.dumps(
            {
                "sha": sha,
                "archive_sha256": checksum,
                "app_ids": app_ids,
                "image_tags": tags,
                "infra": infra,
                "previous": before,
                "created": datetime.now(UTC).isoformat(),
            },
            indent=2,
        ),
    )
    compose(after, "--profile", "init", "config", "--quiet")
    compose(after, "create", "--pull", "never", *DATA, *APP)

    def start_new():
        if before:
            for name in VOLUMES:
                src, dst = volume(before, name), volume(after, name)
                volume_helper(
                    infra["HK_POSTGRES_IMAGE"],
                    ["-v", src + ":/source:ro", "-v", dst + ":/target"],
                    'test -z "$(ls -A /target)" && cp -a /source/. /target/',
                )
        compose(after, "up", "-d", "--pull", "never", "--wait", "--wait-timeout", "240", *DATA)
        with (folder / "pre-migration.dump").open("xb") as backup:
            compose(
                after,
                "exec",
                "-T",
                "postgres",
                "pg_dump",
                "-Fc",
                "-U",
                "hk_candidate",
                "hk_candidate",
                stdout=backup,
            )
        with (folder / "pre-migration.dump").open("rb") as backup:
            compose(
                after,
                "exec",
                "-T",
                "postgres",
                "pg_restore",
                "--list",
                stdin=backup,
                stdout=subprocess.DEVNULL,
            )
        if not before:
            compose(after, "run", "--rm", "--no-deps", "storage-init")
        compose(after, "run", "--rm", "--no-deps", "api", "alembic", "upgrade", "head")
        # Exercise the bucket-only application identity on an isolated, ephemeral object.
        compose(
            after,
            "run",
            "--rm",
            "--no-deps",
            "api",
            "python",
            "-c",
            (
                "from uuid import uuid4; "
                "from app.knowledge.storage import put_object, get_object, delete_object; "
                "k='candidate-probe/'+uuid4().hex; put_object(k,b'ready','text/plain'); "
                "assert get_object(k,10)==b'ready'; delete_object(k)"
            ),
        )
        activate(after)

    switch(before, after, start_new)
    print(f"Active Hong Kong candidate: {after} at {sha}")


def acquire(file, timeout=1200):
    deadline = time.monotonic() + timeout
    while True:
        try:
            fcntl.flock(file, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return
        except BlockingIOError:
            if time.monotonic() >= deadline:
                raise TimeoutError("Timed out waiting for the staging deployment lock") from None
            time.sleep(1)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    actions = p.add_subparsers(dest="action", required=True)
    d = actions.add_parser("deploy")
    for arg in ("sha", "tar_checksum", "api_image_id", "console_image_id"):
        d.add_argument(arg)
    for name in ("init", "rollback", "stop"):
        actions.add_parser(name)
    args = p.parse_args()
    os.umask(0o077)
    init()
    if args.action == "init":
        return
    with contextlib.ExitStack() as stack:
        own = stack.enter_context((ROOT / ".candidate.lock").open("a"))
        acquire(own)
        if SHARED_LOCK.parent.exists():
            shared = stack.enter_context(SHARED_LOCK.open("rb"))
            acquire(shared)
        recover()
        if args.action == "deploy":
            deploy(args.sha, args.tar_checksum, args.api_image_id, args.console_image_id)
        elif args.action == "stop":
            active = current()
            if active:
                stop(active)
                atomic(RELEASES / active / "stopped", "operator\n")
        else:
            active = current()
            if not active:
                raise ValueError("No current candidate")
            previous = verify_local_images(active)["previous"]
            if not previous:
                raise ValueError("No previous candidate checkpoint")
            switch(active, project_name(previous), lambda: activate(previous))


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, lambda *_: sys.exit("Interrupted"))
    try:
        main()
    except (ValueError, OSError, subprocess.CalledProcessError) as exc:
        sys.exit(f"Candidate controller failed: {exc}")
