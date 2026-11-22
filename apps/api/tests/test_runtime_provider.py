from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

import pytest

from app.runtime import provider as runtime_provider
from docker.errors import NotFound


class FakeNetwork:
    def __init__(self, name: str, labels: dict[str, str]) -> None:
        self.name = name
        self.attrs = {"Labels": labels, "Containers": {}}

    def reload(self) -> None:
        return None

    def connect(self, container, aliases=None) -> None:
        self.attrs["Containers"][container.id] = {"Name": container.name}


class FakeNetworks:
    def get(self, _name: str):
        raise NotFound("missing")

    def create(self, name: str, **kwargs):
        return FakeNetwork(name, kwargs["labels"])


class FakeContainers:
    def __init__(self) -> None:
        self.created: dict | None = None
        self.api = type("ApiContainer", (), {"id": "api-container", "name": "api"})()

    def get(self, _name: str):
        raise NotFound("missing")

    def list(self, *, filters):
        assert filters == {"label": "com.docker.compose.service=api"}
        return [self.api]

    def create(self, **kwargs):
        self.created = kwargs
        return object()


class FakeVolumes:
    def get(self, _name: str):
        raise NotFound("missing")

    def create(self, **kwargs):
        return kwargs


class FakeDockerClient:
    def __init__(self) -> None:
        self.containers = FakeContainers()
        self.networks = FakeNetworks()
        self.volumes = FakeVolumes()


def test_runtime_provider_rejects_floating_latest_tag(monkeypatch) -> None:
    monkeypatch.setattr(
        runtime_provider.docker,
        "from_env",
        lambda **_kwargs: pytest.fail("Docker must not be contacted for an invalid image"),
    )
    with pytest.raises(ValueError, match="pinned tag"):
        runtime_provider.DockerRuntimeProvider(
            image="example/astrbot:latest",
            runtime_root="/tmp/runtime",
        )


def test_docker_provider_writes_credentials_outside_runtime_config(
    tmp_path: Path, monkeypatch
) -> None:
    client = FakeDockerClient()
    monkeypatch.setattr(runtime_provider.docker, "from_env", lambda **_kwargs: client)
    provider = runtime_provider.DockerRuntimeProvider(
        image="example/astrbot:4.29.4-pinned",
        runtime_root=str(tmp_path),
    )
    tenant_id = uuid4()
    bot_id = uuid4()
    secret = "short-lived-runtime-token"

    resource_id = provider.provision(
        tenant_id=tenant_id,
        bot_instance_id=bot_id,
        runtime_config={
            "config": {
                "schema_version": 1,
                "gateway": {
                    "base_url": "http://api:8000/internal/runtime/v1",
                    "credential_ref": "/etc/supernova/runtime-credentials.json",
                },
            },
            "credentials": {"chat.completions": secret},
        },
    )

    config_dir = tmp_path / tenant_id.hex / bot_id.hex / "config"
    runtime_config = json.loads((config_dir / "runtime.json").read_text(encoding="utf-8"))
    runtime_credentials = json.loads(
        (config_dir / "runtime-credentials.json").read_text(encoding="utf-8")
    )

    assert resource_id == f"supernova-astrbot-{bot_id.hex}"
    assert "credentials" not in runtime_config["gateway"]
    assert runtime_config["gateway"]["credential_ref"] == (
        "/etc/supernova/runtime-credentials.json"
    )
    assert runtime_credentials["chat.completions"] == secret
    assert (config_dir / "runtime.json").stat().st_mode & 0o777 == 0o600
    assert (config_dir / "runtime-credentials.json").stat().st_mode & 0o777 == 0o600
    assert client.containers.created is not None
    assert client.containers.created["read_only"] is True
    assert client.containers.created["cap_drop"] == ["ALL"]
    assert client.containers.created["pids_limit"] == 128
    assert client.containers.created["volumes"][str(config_dir)]["mode"] == "ro"
