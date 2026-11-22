from collections.abc import Generator
from uuid import uuid4

import pytest
from app.db.base import Base
from app.db.session import get_db
from app.main import app
from app.schemas.bot_runtime import RuntimeEventEnvelope
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool


@pytest.fixture
def client() -> Generator[TestClient, None, None]:
    engine = create_engine(
        "sqlite+pysqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    testing_session = sessionmaker(
        bind=engine,
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
    )

    def override_get_db():
        with testing_session() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()
    Base.metadata.drop_all(engine)
    engine.dispose()


def register(client: TestClient, email: str) -> dict:
    response = client.post(
        "/auth/register",
        json={
            "email": email,
            "password": "correct-horse-battery",
            "display_name": "Runtime Test",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def headers(auth: dict, idempotency_key: str | None = None) -> dict[str, str]:
    result = {"Authorization": f"Bearer {auth['access_token']}"}
    if idempotency_key:
        result["Idempotency-Key"] = idempotency_key
    return result


def create_persona(client: TestClient, auth: dict) -> dict:
    provider = client.post(
        "/model-providers",
        headers=headers(auth),
        json={
            "name": "Runtime Test Provider",
            "provider_type": "deepseek",
            "base_url": "https://api.deepseek.com",
            "api_key": "sk-runtime-test-provider",
        },
    )
    assert provider.status_code == 201, provider.text

    persona = client.post(
        "/personas",
        headers=headers(auth),
        json={
            "name": "Runtime Persona",
            "slug": "runtime-persona",
            "description": "Runtime integration test",
            "system_prompt": "You are a concise assistant.",
            "model_source": "byok",
            "provider_id": provider.json()["id"],
            "model_name": "deepseek-chat",
            "temperature": 0.4,
            "top_p": 0.9,
            "max_tokens": 1024,
            "enabled": True,
        },
    )
    assert persona.status_code == 201, persona.text
    return persona.json()


def create_bot(client: TestClient, auth: dict, persona_id: str) -> dict:
    response = client.post(
        "/bot-instances",
        headers=headers(auth),
        json={"name": "Support Bot", "persona_id": persona_id},
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_bot_instances_are_tenant_scoped(client: TestClient) -> None:
    first = register(client, "runtime-owner-one@example.com")
    persona = create_persona(client, first)
    bot = create_bot(client, first, persona["id"])

    second = register(client, "runtime-owner-two@example.com")
    hidden = client.get(f"/bot-instances/{bot['id']}", headers=headers(second))
    assert hidden.status_code == 404

    cross_tenant_create = client.post(
        "/bot-instances",
        headers=headers(second),
        json={"name": "Foreign Persona Bot", "persona_id": persona["id"]},
    )
    assert cross_tenant_create.status_code == 404


def test_start_command_is_idempotent_and_deduplicated(client: TestClient) -> None:
    auth = register(client, "runtime-idempotency@example.com")
    persona = create_persona(client, auth)
    bot = create_bot(client, auth, persona["id"])

    first = client.post(
        f"/bot-instances/{bot['id']}/start",
        headers=headers(auth, "start-bot-once"),
    )
    second = client.post(
        f"/bot-instances/{bot['id']}/start",
        headers=headers(auth, "start-bot-once"),
    )
    third = client.post(
        f"/bot-instances/{bot['id']}/start",
        headers=headers(auth, "start-bot-retry"),
    )

    assert first.status_code == 202, first.text
    assert second.status_code == 202, second.text
    assert third.status_code == 202, third.text
    assert first.json()["command"]["id"] == second.json()["command"]["id"]
    assert first.json()["command"]["id"] == third.json()["command"]["id"]
    assert first.json()["desired_state"] == "running"


def test_persona_cannot_be_changed_while_runtime_is_running(client: TestClient) -> None:
    auth = register(client, "runtime-patch@example.com")
    persona = create_persona(client, auth)
    bot = create_bot(client, auth, persona["id"])

    # A queued start changes desired_state immediately; config edits are rejected
    # until Worker Manager has reconciled the instance to stopped.
    client.post(f"/bot-instances/{bot['id']}/start", headers=headers(auth))
    response = client.patch(
        f"/bot-instances/{bot['id']}",
        headers=headers(auth),
        json={"name": "Renamed Bot"},
    )
    assert response.status_code == 409


def test_runtime_event_schema_rejects_chat_content() -> None:
    with pytest.raises(ValidationError):
        RuntimeEventEnvelope(
            event_id=uuid4(),
            event_type="runtime.log",
            tenant_id=uuid4(),
            bot_instance_id=uuid4(),
            platform="system",
            correlation_id=uuid4(),
            occurred_at="2026-11-22T04:00:00Z",
            payload={"content": "this is a chat message and must not be persisted"},
        )
