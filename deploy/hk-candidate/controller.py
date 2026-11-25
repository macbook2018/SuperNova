"""Host controller for the isolated Hong Kong RAG candidate. Requires Linux and Compose v2."""

import argparse
import base64
import contextlib
import fcntl
import hashlib
import io
import json
import os
import re
import secrets
import shutil
import signal
import socket
import stat
import subprocess
import sys
import tarfile
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path

DEDICATED_ROOT = Path("/opt/supernova-hk-candidate")
STAGING_CANDIDATE_ROOT = Path("/opt/supernova-staging/hk-rag-candidate")
ROOT = DEDICATED_ROOT
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


def private_opener(path, flags):
    # A parent default ACL can override umask. Request the private mode at creation.
    fd = os.open(path, flags | os.O_NOFOLLOW, 0o600)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_nlink != 1:
            raise ValueError("Expected a regular, privately owned candidate file")
        os.fchmod(fd, 0o600)
        return fd
    except BaseException:
        os.close(fd)
        raise


def private_directory(path, *, exist_ok=False):
    path.mkdir(mode=0o700, exist_ok=exist_ok)
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        info = os.fstat(fd)
        if info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise ValueError(f"{path} must be private (0700), owned by the deploy account")
        os.fchmod(fd, 0o700)
    finally:
        os.close(fd)


def atomic(path, value):
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}-")
    try:
        with os.fdopen(fd, "w") as out:
            os.fchmod(out.fileno(), 0o600)
            out.write(value)
            out.flush()
            os.fsync(out.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        Path(temporary).unlink(missing_ok=True)


def check_secrets(path):
    info = path.lstat()
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_uid != os.getuid()
        or stat.S_IMODE(info.st_mode) != 0o600
    ):
        raise ValueError("Invalid candidate secrets file ownership or permissions")


def candidate_root(profile):
    # Previous deployments of either SSH profile used DEDICATED_ROOT. Keep their history.
    try:
        STAGING_CANDIDATE_ROOT.lstat()
    except FileNotFoundError:
        pass
    except PermissionError:
        if profile == "existing-staging":
            raise
        # Dedicated identities do not need access to the staging user's directory.
    else:
        if profile != "existing-staging" or os.path.lexists(DEDICATED_ROOT):
            raise ValueError("Candidate root conflict: check the SSH profile and both fixed roots")
        return STAGING_CANDIDATE_ROOT
    if profile == "existing-staging" and not os.path.lexists(DEDICATED_ROOT):
        return STAGING_CANDIDATE_ROOT
    return DEDICATED_ROOT


def init():
    if ROOT.resolve() != ROOT:
        raise ValueError("Candidate directory must use its fixed host path")
    private_directory(ROOT, exist_ok=True)
    private_directory(RELEASES, exist_ok=True)
    path = ROOT / "secrets.env"
    try:
        check_secrets(path)
    except FileNotFoundError:
        pass
    else:
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
            check_secrets(path)
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


def json_member(bundle, path):
    member = bundle.getmember(path)
    if not member.isfile() or member.size > 16 * 1024 * 1024:
        raise ValueError("Invalid image archive metadata")
    return bundle.extractfile(member).read()


def docker_config(bundle, row, tag, sha):
    tags = [ref.removeprefix("docker.io/") for ref in row.get("RepoTags") or []]
    if tags != [tag]:
        raise ValueError("Unexpected image tag in Docker archive")
    raw = json_member(bundle, row["Config"])
    config = json.loads(raw)
    if (config.get("os"), config.get("architecture")) != ("linux", "amd64"):
        raise ValueError("Expected a single Linux amd64 image")
    if config.get("config", {}).get("Labels", {}).get("org.opencontainers.image.revision") != sha:
        raise ValueError("Commit label does not match the reviewed SHA")
    layers = row["Layers"]
    if not isinstance(layers, list) or len(config["rootfs"]["diff_ids"]) != len(layers):
        raise ValueError("Image layers do not match its configuration")
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def add_bytes(bundle, name, data):
    entry = tarfile.TarInfo(name)
    entry.size = len(data)
    bundle.addfile(entry, io.BytesIO(data))


def merge_images(directory, sha):
    """Write just Docker's two tagged manifests and referenced files; exclude OCI sidecars."""
    manifest = []
    ids = {}
    output = directory / "images.tar"
    with tarfile.open(output, "w") as result:
        for role in ("api", "console"):
            tag = f"supernova-hk-candidate/{role}:{sha}"
            with tarfile.open(directory / f"{role}.tar", "r:") as source:
                rows = json.loads(json_member(source, "manifest.json"))
                if len(rows) != 1:
                    raise ValueError("Expected a single image per Docker exporter")
                row = rows[0]
                ids[role] = docker_config(source, row, tag, sha)
                config_path = ids[role].removeprefix("sha256:") + ".json"
                add_bytes(result, config_path, json_member(source, row["Config"]))
                layers = []
                for i, path in enumerate(row["Layers"]):
                    member = source.getmember(path)
                    if not member.isfile():
                        raise ValueError("Invalid layer in Docker archive")
                    # Use safe names independent of paths in the exporter archive.
                    entry = tarfile.TarInfo(f"{role}/{i}/layer.tar")
                    entry.size = member.size
                    result.addfile(entry, source.extractfile(member))
                    layers.append(entry.name)
                manifest.append({"Config": config_path, "RepoTags": [tag], "Layers": layers})
        add_bytes(result, "manifest.json", json.dumps(manifest).encode())
    return ids


def build_bundle(context, sha, directory):
    if not re.fullmatch(r"[0-9a-f]{40}", sha):
        raise ValueError("Expected reviewed commit SHA")
    directory.mkdir(parents=True, exist_ok=True)
    builder = "hk-build-" + secrets.token_hex(8)
    command(
        [
            "docker",
            "buildx",
            "create",
            "--name",
            builder,
            "--driver",
            "docker-container",
        ]
    )
    try:
        for role in ("api", "console"):
            args = [
                "docker",
                "buildx",
                "build",
                "--builder",
                builder,
                "--platform",
                "linux/amd64",
                "--provenance=false",
                "--output",
                f"type=docker,dest={directory}/{role}.tar",
                "--label",
                f"org.opencontainers.image.revision={sha}",
                "--tag",
                f"supernova-hk-candidate/{role}:{sha}",
            ]
            if role == "console":
                args.extend(["--build-arg", "VITE_API_BASE_URL=http://127.0.0.1:18182"])
            command([*args, "-f", str(context / "apps" / role / "Dockerfile"), str(context)])
        ids = merge_images(directory, sha)
    finally:
        command(["docker", "buildx", "rm", builder])
    for role in ids:
        (directory / f"{role}.tar").unlink()
    archive = directory / "images.tar"
    with archive.open("rb") as stream:
        checksum = hashlib.file_digest(stream, "sha256").hexdigest()
    identities = {
        "archive_hash": checksum,
        "api_id": ids["api"],
        "console_id": ids["console"],
    }
    (directory / "identities.json").write_text(json.dumps(identities))


def archive_check(archive, sha, checksum, app_ids):
    if not re.fullmatch(r"[0-9a-f]{40}", sha) or not re.fullmatch(r"[0-9a-f]{64}", checksum):
        raise ValueError("Expected full commit SHA and SHA-256 archive checksum")
    if set(app_ids) != {"api", "console"} or any(
        not re.fullmatch(r"sha256:[0-9a-f]{64}", i) for i in app_ids.values()
    ):
        raise ValueError("Invalid image configuration digest")
    with archive.open("rb") as stream:
        if hashlib.file_digest(stream, "sha256").hexdigest() != checksum:
            raise ValueError("Image archive checksum mismatch")
    expected = {f"supernova-hk-candidate/{role}:{sha}": digest for role, digest in app_ids.items()}
    with tarfile.open(archive, mode="r:") as bundle:
        rows = json.loads(json_member(bundle, "manifest.json"))
        allowed = {"manifest.json"}
        actual = {}
        for row in rows:
            refs = row.get("RepoTags") or []
            if len(refs) != 1 or refs[0] not in expected or refs[0] in actual:
                raise ValueError("Unexpected image tag in candidate archive")
            actual[refs[0]] = docker_config(bundle, row, refs[0], sha)
            allowed.update([row["Config"], *row["Layers"]])
        members = bundle.getmembers()
        if (
            actual != expected
            or len(members) != len(allowed)
            or {m.name for m in members} != allowed
            or not all(
                m.isfile()
                and m.name in allowed
                and re.fullmatch(
                    r"manifest\.json|[a-f0-9]{64}\.json|(?:api|console)/[0-9]+/layer\.tar",
                    m.name,
                )
                for m in members
            )
        ):
            raise ValueError("Unexpected files or image digests in candidate archive")


def runtime_id(tag, sha, expected_config, verified_id=None):
    inspected = inspect_image(tag)
    actual = inspected["Id"]
    if (
        not re.fullmatch(r"sha256:[0-9a-f]{64}", actual)
        or inspected.get("Os") != "linux"
        or inspected.get("Architecture") != "amd64"
        or inspected["Config"]["Labels"].get("org.opencontainers.image.revision") != sha
    ):
        raise ValueError("Loaded candidate platform, revision or runtime digest does not match")
    if verified_id is not None:
        if actual != verified_id:
            raise ValueError("Saved candidate runtime digest has changed")
    elif actual != expected_config:
        # The containerd image store can expose an image manifest digest instead of a config ID.
        # Bind that local digest to the exported configuration once, before persisting the slot.
        with tempfile.TemporaryFile() as stream:
            command(["docker", "image", "save", tag], stdout=stream)
            stream.seek(0)
            with tarfile.open(fileobj=stream, mode="r:") as bundle:
                rows = json.loads(json_member(bundle, "manifest.json"))
                if len(rows) != 1 or docker_config(bundle, rows[0], tag, sha) != expected_config:
                    raise ValueError("Loaded image configuration digest does not match")
    return actual


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
        aliases = {
            repository,
            canonical,
            "docker.io/" + canonical,
            "index.docker.io/" + canonical,
        }
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
        runtime_id(
            manifest["image_tags"][role],
            manifest["sha"],
            image_id,
            manifest.get("runtime_ids", {}).get(role),
        )
    return manifest


def volume(project, name):
    ref = project_name(project) + "_" + name
    labels = (
        json.loads(
            command(
                ["docker", "volume", "inspect", "--format", "{{json .Labels}}", ref],
                capture=True,
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
    for url in (
        "http://127.0.0.1:18182/health/ready",
        "http://127.0.0.1:18183/healthz",
    ):
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
        ROOT / "pending.json",
        json.dumps(dict(before=before, after=after, was_running=was_running)),
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
    runtime_ids = {}
    for role, expected in app_ids.items():
        src = f"supernova-hk-candidate/{role}:{sha}"
        tags[role] = f"supernova-hk-sealed/{role}:" + expected.removeprefix("sha256:")
        command(["docker", "tag", src, tags[role]])
        runtime_ids[role] = runtime_id(tags[role], sha, expected)
    after = "sn-hkc-" + sha[:12] + "-" + secrets.token_hex(6)
    folder = RELEASES / after
    private_directory(folder)
    for file in ("compose.yaml", "hk-rag-candidate-host.sh", "controller.py"):
        destination = folder / file
        shutil.copyfile(HERE / file, destination)
        destination.chmod(0o700 if file.endswith(".sh") else 0o600)
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

    variables = {
        **infra,
        "HK_API_IMAGE": tags["api"],
        "HK_CONSOLE_IMAGE": tags["console"],
    }
    atomic(
        folder / ".env",
        conf.rstrip() + "\n" + "".join(f"{k}={v}\n" for k, v in variables.items()),
    )
    atomic(
        folder / "manifest.json",
        json.dumps(
            {
                "sha": sha,
                "archive_sha256": checksum,
                "app_ids": app_ids,
                "runtime_ids": runtime_ids,
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
        compose(
            after,
            "up",
            "-d",
            "--pull",
            "never",
            "--wait",
            "--wait-timeout",
            "240",
            *DATA,
        )
        with open(folder / "pre-migration.dump", "xb", opener=private_opener) as backup:
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
    global ROOT, RELEASES
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--ssh-profile", choices=("candidate", "existing-staging"), default="candidate")
    actions = p.add_subparsers(dest="action", required=True)
    d = actions.add_parser("deploy")
    for arg in ("sha", "tar_checksum", "api_image_id", "console_image_id"):
        d.add_argument(arg)
    for name in ("init", "rollback", "stop"):
        actions.add_parser(name)
    build = actions.add_parser("build-bundle")
    build.add_argument("context", type=Path)
    build.add_argument("sha")
    build.add_argument("directory", type=Path)
    args = p.parse_args()
    if args.action == "build-bundle":
        build_bundle(args.context.resolve(), args.sha, args.directory.resolve())
        return
    ROOT = candidate_root(args.ssh_profile)
    RELEASES = ROOT / "releases"
    os.umask(0o077)
    init()
    if args.action == "init":
        return
    with contextlib.ExitStack() as stack:
        own = stack.enter_context(open(ROOT / ".candidate.lock", "a", opener=private_opener))
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
