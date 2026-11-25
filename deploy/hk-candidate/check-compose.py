"""Render two disposable HK projects without a daemon; assert their actual resource boundaries."""

import argparse
import json
import os
import subprocess
import tempfile
from pathlib import Path

import controller

HERE = Path(__file__).resolve().parent


def require(condition, message):
    if not condition:
        raise ValueError(message)


def render(cli, project, envfile):
    cmd = [
        *cli,
        "-p",
        project,
        "--env-file",
        str(envfile),
        "-f",
        str(HERE / "compose.yaml"),
        "--profile",
        "init",
        "config",
        "--format",
        "json",
    ]
    return json.loads(
        subprocess.check_output(
            cmd, text=True, env={"PATH": os.environ["PATH"], "HOME": str(envfile.parent)}
        )
    )


def check(cfg, project):
    sv = cfg["services"]
    require(
        set(sv) == set(controller.APP + controller.DATA + ["storage-init"]), "Unexpected service"
    )
    require(
        cfg["name"] == project
        and all(
            v["name"] == f"{project}_{key}" and not v.get("external")
            for key, v in cfg["volumes"].items()
        ),
        "Shared volume",
    )
    require(set(cfg["volumes"]) == set(controller.VOLUMES), "Unexpected volume")
    for name, service in sv.items():
        require(
            not any(
                service.get(k)
                for k in (
                    "privileged",
                    "devices",
                    "network_mode",
                    "extra_hosts",
                    "container_name",
                    "build",
                    "pid",
                    "ipc",
                    "volumes_from",
                    "cap_add",
                )
            ),
            f"Unsafe {name}",
        )
        require(
            all(
                v["type"] == "volume" and v["source"] in controller.VOLUMES
                for v in service.get("volumes", [])
            ),
            f"Unexpected mount in {name}",
        )
        ports = [(p["host_ip"], str(p["published"]), p["target"]) for p in service.get("ports", [])]
        expected = {
            "api": [("127.0.0.1", "18182", 8000)],
            "console": [("127.0.0.1", "18183", 8080)],
        }
        require(ports == expected.get(name, []), f"Unexpected exposure in {name}")
        require(service.get("mem_limit") and service.get("cpus"), f"No resource limit for {name}")
    require(cfg["networks"]["backend"]["internal"] is True, "Data network must be internal")
    require(
        all(set(sv[s]["networks"]) == {"backend"} for s in controller.DATA + ["storage-init"]),
        "Storage outside internal network",
    )
    for name in ("api", "knowledge-worker", "chat-import-worker"):
        env = sv[name]["environment"]
        require(
            env["SUPERNOVA_S3_ENDPOINT_URL"] == "http://silo:9000"
            and env["SUPERNOVA_S3_BUCKET"] == "hk-rag-candidate",
            "Unexpected object store",
        )
        require(
            set(env.values()).isdisjoint({"root-only-user", "root-only-password"}),
            "Leaked Silo root",
        )
        require(
            env["SUPERNOVA_S3_ACCESS_KEY"] == "bucket-only-user"
            and env["SUPERNOVA_S3_SECRET_KEY"] == "bucket-only-password",
            "Missing bucket scope",
        )
        require(
            env["SUPERNOVA_BILLING_SUBSCRIPTION_ENFORCEMENT_ENABLED"] == "false", "Billing enabled"
        )
        require(
            sv[name]["pull_policy"] == "never" and sv[name]["image"] == "hk-dry/api:test",
            "Candidate image not pinned locally",
        )
    require(
        sv["console"]["pull_policy"] == "never" and sv["console"]["image"] == "hk-dry/console:test",
        "Unexpected Console image",
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--compose-bin", help="standalone Docker Compose v2 binary")
    args = parser.parse_args()
    cli = [args.compose_bin] if args.compose_bin else ["docker", "compose"]
    samples = {
        "HK_API_IMAGE": "hk-dry/api:test",
        "HK_CONSOLE_IMAGE": "hk-dry/console:test",
        "HK_PG_PASSWORD": "test-pg",
        "HK_REDIS_PASSWORD": "test-redis",
        "HK_JWT_SECRET": "test-jwt",
        "HK_ENCRYPTION_KEY": "test-encryption",
        "HK_S3_ACCESS_KEY": "bucket-only-user",
        "HK_S3_SECRET_KEY": "bucket-only-password",
        "HK_SILO_ROOT_USER": "root-only-user",
        "HK_SILO_ROOT_PASSWORD": "root-only-password",
    }
    with tempfile.TemporaryDirectory() as directory:
        envfile = Path(directory) / "candidate.env"
        envfile.write_text("".join(f"{k}={v}\n" for k, v in samples.items()))
        configs = [render(cli, project, envfile) for project in ("hk-dry-a", "hk-dry-b")]
        for cfg in configs:
            check(cfg, cfg["name"])
        for resource in ("volumes", "networks"):
            a, b = [{v["name"] for v in cfg[resource].values()} for cfg in configs]
            require(a.isdisjoint(b), f"Parallel candidate projects share {resource}")
    print("Candidate Compose isolation checks passed")


if __name__ == "__main__":
    main()
