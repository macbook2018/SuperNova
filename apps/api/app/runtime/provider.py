from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Protocol
from uuid import UUID

import docker
from docker.errors import NotFound


class RuntimeProvider(Protocol):
    def provision(self, *, tenant_id: UUID, bot_instance_id: UUID, runtime_config: dict) -> str: ...
    def start(self, resource_id: str) -> None: ...
    def stop(self, resource_id: str) -> None: ...
    def restart(self, resource_id: str) -> None: ...
    def delete(self, resource_id: str, *, delete_volumes: bool = True) -> None: ...
    def inspect(self, resource_id: str) -> dict: ...
    def logs(
        self, resource_id: str, *, cursor: str | None = None, limit: int = 100
    ) -> list[str]: ...


class DockerRuntimeProvider:
    """Docker provider. Only the dedicated Worker Manager may instantiate this class."""

    def __init__(self, *, image: str, runtime_root: str, memory_limit: str = "512m",
                 cpu_limit: float = 1.0, pids_limit: int = 128) -> None:
        if not image or image.endswith(":latest") or image == "latest":
            raise ValueError("AstrBot runtime image must use an immutable pinned tag")
        if not re.fullmatch(r"[a-zA-Z0-9_.:/-]+", image):
            raise ValueError("Invalid runtime image reference")
        self.client = docker.from_env(timeout=30)
        self.image = image
        self.runtime_root = Path(runtime_root).resolve()
        self.memory_limit = memory_limit
        self.cpu_limit = cpu_limit
        self.pids_limit = pids_limit

    @staticmethod
    def _name(bot_instance_id: UUID) -> str:
        return f"supernova-astrbot-{bot_instance_id.hex}"

    @staticmethod
    def _network_name(bot_instance_id: UUID) -> str:
        return f"snv-runtime-{bot_instance_id.hex[:20]}"

    @staticmethod
    def _volume_name(bot_instance_id: UUID) -> str:
        return f"snv-data-{bot_instance_id.hex}"

    def provision(self, *, tenant_id: UUID, bot_instance_id: UUID, runtime_config: dict) -> str:
        name = self._name(bot_instance_id)
        instance_root = self.runtime_root / tenant_id.hex / bot_instance_id.hex
        instance_root.mkdir(parents=True, exist_ok=True, mode=0o700)
        instance_root.chmod(0o700)
        config_dir = instance_root / "config"
        config_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        (instance_root / "data").mkdir(parents=True, exist_ok=True, mode=0o700)
        config_payload = runtime_config.get("config", runtime_config)
        credentials_payload = runtime_config.get("credentials", {})
        for filename, payload in (
            ("runtime.json", config_payload),
            ("runtime-credentials.json", credentials_payload),
        ):
            target = config_dir / filename
            temporary = config_dir / f".{filename}.tmp"
            temporary.write_text(
                json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                encoding="utf-8",
            )
            temporary.chmod(0o600)
            temporary.replace(target)
            target.chmod(0o600)

        labels = {
            "com.supernova.managed": "true",
            "com.supernova.tenant_id": str(tenant_id),
            "com.supernova.bot_instance_id": str(bot_instance_id),
        }
        try:
            container = self.client.containers.get(name)
            if any(container.labels.get(key) != value for key, value in labels.items()):
                raise RuntimeError("runtime_resource_label_mismatch")
            return name
        except NotFound:
            pass

        network_name = self._network_name(bot_instance_id)
        try:
            network = self.client.networks.get(network_name)
            if any(network.attrs.get("Labels", {}).get(k) != v for k, v in labels.items()):
                raise RuntimeError("runtime_network_label_mismatch")
        except NotFound:
            network = self.client.networks.create(
                network_name, driver="bridge", labels=labels, check_duplicate=True
            )

        api_candidates = self.client.containers.list(
            filters={"label": "com.docker.compose.service=api"}
        )
        if len(api_candidates) != 1:
            raise RuntimeError("runtime_gateway_container_not_unique")
        api_container = api_candidates[0]
        network.reload()
        connected_ids = set((network.attrs.get("Containers") or {}).keys())
        if api_container.id not in connected_ids:
            network.connect(api_container, aliases=["api"])

        volume = self._volume_name(bot_instance_id)
        try:
            self.client.volumes.get(volume)
        except NotFound:
            self.client.volumes.create(name=volume, labels=labels)
        self.client.containers.create(
            image=self.image,
            name=name,
            detach=True,
            init=True,
            labels=labels,
            network=network.name,
            environment={"SUPERNOVA_RUNTIME_CONFIG": "/etc/supernova/runtime.json"},
            volumes={
                str(config_dir): {"bind": "/etc/supernova", "mode": "ro"},
                volume: {"bind": "/var/lib/astrbot", "mode": "rw"},
            },
            mem_limit=self.memory_limit,
            nano_cpus=int(self.cpu_limit * 1_000_000_000),
            pids_limit=self.pids_limit,
            read_only=True,
            cap_drop=["ALL"],
            security_opt=["no-new-privileges:true"],
            tmpfs={"/tmp": "rw,noexec,nosuid,size=64m"},
            restart_policy={"Name": "unless-stopped"},
        )
        return name

    def _container(self, resource_id: str):
        if not resource_id.startswith("supernova-astrbot-"):
            raise ValueError("runtime_resource_id_invalid")
        return self.client.containers.get(resource_id)

    def start(self, resource_id: str) -> None:
        container = self._container(resource_id)
        if container.status != "running":
            container.start()

    def stop(self, resource_id: str) -> None:
        try:
            container = self._container(resource_id)
        except NotFound:
            return
        if container.status == "running":
            container.stop(timeout=20)

    def restart(self, resource_id: str) -> None:
        self._container(resource_id).restart(timeout=20)

    def delete(self, resource_id: str, *, delete_volumes: bool = True) -> None:
        if not resource_id.startswith("supernova-astrbot-"):
            raise ValueError("runtime_resource_id_invalid")
        bot_hex = resource_id.removeprefix("supernova-astrbot-")
        if not re.fullmatch(r"[0-9a-f]{32}", bot_hex):
            raise ValueError("runtime_resource_id_invalid")
        try:
            container = self.client.containers.get(resource_id)
            if container.labels.get("com.supernova.managed") != "true":
                raise RuntimeError("runtime_resource_not_managed")
            tenant_hex = container.labels.get("com.supernova.tenant_id", "").replace("-", "")
            container.remove(force=True, v=True)
            if re.fullmatch(r"[0-9a-f]{32}", tenant_hex):
                import shutil
                shutil.rmtree(self.runtime_root / tenant_hex / bot_hex, ignore_errors=True)
        except NotFound:
            pass
        try:
            self.client.networks.get(f"snv-runtime-{bot_hex[:20]}").remove()
        except NotFound:
            pass
        if delete_volumes:
            try:
                self.client.volumes.get(f"snv-data-{bot_hex}").remove(force=True)
            except NotFound:
                pass

    def inspect(self, resource_id: str) -> dict:
        container = self._container(resource_id)
        container.reload()
        state = container.attrs.get("State", {})
        return {
            "resource_id": resource_id,
            "status": container.status,
            "running": bool(state.get("Running")),
            "exit_code": state.get("ExitCode"),
            "oom_killed": bool(state.get("OOMKilled")),
            "error": (state.get("Error") or "")[:256],
        }

    def logs(self, resource_id: str, *, cursor: str | None = None, limit: int = 100) -> list[str]:
        if not 1 <= limit <= 500:
            raise ValueError("runtime_log_limit_invalid")
        output = self._container(resource_id).logs(
            stdout=True, stderr=True, tail=limit,
            since=int(cursor) if cursor and cursor.isdigit() else None,
            timestamps=True,
        )
        return output.decode("utf-8", errors="replace").splitlines()
