from __future__ import annotations

from collections.abc import Generator

import pytest
from app.chat_import import worker
from app.db.base import Base
from app.db.session import get_db
from app.main import app
from app.models.chat_import import ChatImportFile, ChatImportJob, ChatMessage, ChatSource
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool


@pytest.fixture
def session_factory():
    engine = create_engine(
        "sqlite+pysqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def _enable_sqlite_foreign_keys(connection, _record) -> None:
        cursor = connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(engine)
    factory = sessionmaker(
        bind=engine,
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
    )
    yield factory
    Base.metadata.drop_all(engine)
    engine.dispose()


@pytest.fixture
def client(session_factory) -> Generator[TestClient, None, None]:
    def override_get_db():
        with session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def register(client: TestClient, email: str) -> dict:
    response = client.post(
        "/auth/register",
        json={
            "email": email,
            "password": "correct-horse-battery",
            "display_name": "Chat Import Tester",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def headers(tokens: dict) -> dict[str, str]:
    return {"Authorization": f"Bearer {tokens['access_token']}"}


def _sample_chat_export_body() -> bytes:
    return (
        '{"messages":['
        '{"conversation_id":"chat-1","sender_id":"wxid-owner",'
        '"sender_name":"本人","content":"你好","timestamp":"2026-11-12T10:00:00Z"},'
        '{"conversation_id":"chat-1","sender_id":"wxid-customer",'
        '"sender_name":"客户A","content":"请问价格","timestamp":"2026-11-12T10:01:00Z"}'
        ']}'
    ).encode("utf-8")

def create_source(client: TestClient, auth: dict) -> dict:
    response = client.post(
        "/chat-sources",
        headers=headers(auth),
        json={"name": "微信导出记录", "export_tool": "generic-export"},
    )
    assert response.status_code == 201, response.text
    return response.json()


def upload_json(
    client: TestClient,
    auth: dict,
    source_id: str,
    *,
    body: bytes | None = None,
    idempotency_key: str | None = None,
):
    if body is None:
        body = _sample_chat_export_body()
    request_headers = headers(auth)
    if idempotency_key:
        request_headers["Idempotency-Key"] = idempotency_key
    return client.post(
        f"/chat-sources/{source_id}/imports",
        headers=request_headers,
        files=[("files", ("chat.json", body, "application/json"))],
    )


def test_upload_creates_tenant_scoped_job_and_is_idempotent(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("app.api.chat_import.put_object", lambda *_args, **_kwargs: None)
    auth = register(client, "chat-import-owner@example.com")
    source = create_source(client, auth)

    first = upload_json(
        client, auth, source["id"], idempotency_key="import-request-001"
    )
    assert first.status_code == 202, first.text
    first_job = first.json()["job"]
    assert first_job["status"] == "queued"
    assert first.json()["accepted_files"] == 1

    replay = upload_json(
        client, auth, source["id"], idempotency_key="import-request-001"
    )
    assert replay.status_code == 202
    assert replay.json()["job"]["id"] == first_job["id"]

    other = register(client, "other-chat-import@example.com")
    hidden = client.get(
        f"/chat-imports/{first_job['id']}",
        headers=headers(other),
    )
    assert hidden.status_code == 404


def test_duplicate_file_is_not_queued_twice(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("app.api.chat_import.put_object", lambda *_args, **_kwargs: None)
    auth = register(client, "chat-import-dedupe@example.com")
    source = create_source(client, auth)

    first = upload_json(client, auth, source["id"])
    assert first.status_code == 202

    duplicate = upload_json(client, auth, source["id"])
    assert duplicate.status_code == 409
    assert duplicate.json()["detail"]["code"] == "chat_import_files_already_seen"


def test_worker_normalizes_messages_and_identity_requires_user_confirmation(
    client: TestClient,
    session_factory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("app.api.chat_import.put_object", lambda *_args, **_kwargs: None)
    auth = register(client, "chat-import-worker@example.com")
    source = create_source(client, auth)
    response = upload_json(client, auth, source["id"])
    assert response.status_code == 202, response.text
    job_id = response.json()["job"]["id"]

    with session_factory() as db:
        file_record = db.scalar(select(ChatImportFile).where(ChatImportFile.job_id == job_id))
        object_key = file_record.object_key

    body = _sample_chat_export_body()
    monkeypatch.setattr(worker, "SessionLocal", session_factory)
    monkeypatch.setattr(worker, "get_object", lambda *_args, **_kwargs: body)

    claimed = worker.claim_next_chat_import_job("test-worker")
    assert str(claimed) == job_id
    worker.process_chat_import_job(claimed)

    with session_factory() as db:
        job = db.get(ChatImportJob, claimed)
        messages = db.scalars(
            select(ChatMessage).where(
                ChatMessage.tenant_id == auth["tenant"]["id"],
                ChatMessage.source_id == source["id"],
            )
        ).all()
        assert job.status == "awaiting_identity"
        assert job.parsed_messages == 2
        assert job.imported_messages == 2
        assert len(messages) == 2
        assert {item.identity_role for item in messages} == {"unknown"}
        assert file_record is not None
        assert object_key

    job_detail = client.get(
        f"/chat-imports/{job_id}",
        headers=headers(auth),
    )
    assert job_detail.status_code == 200
    assert job_detail.json()["files"][0]["status"] == "parsed"

    messages_response = client.get(
        f"/chat-sources/{source['id']}/messages",
        headers=headers(auth),
        params={"sender_ref": "wxid-owner"},
    )
    assert messages_response.status_code == 200
    assert len(messages_response.json()) == 1
    assert messages_response.json()[0]["content"] == "你好"

    preview = client.get(
        f"/chat-sources/{source['id']}/preview",
        headers=headers(auth),
    )
    assert preview.status_code == 200
    assert preview.json()["identity_confirmed"] is False
    assert {item["sender_ref"] for item in preview.json()["participants"]} == {
        "wxid-owner",
        "wxid-customer",
    }

    confirmed = client.post(
        f"/chat-sources/{source['id']}/confirm-identity",
        headers=headers(auth),
        json={"target_sender_ref": "wxid-owner"},
    )
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["target_sender_ref"] == "wxid-owner"

    with session_factory() as db:
        roles = db.scalars(
            select(ChatMessage.identity_role).where(
                ChatMessage.tenant_id == auth["tenant"]["id"],
                ChatMessage.source_id == source["id"],
            )
        ).all()
        assert roles.count("target") == 1
        assert roles.count("other") == 1
        job = db.get(ChatImportJob, claimed)
        assert job.status == "succeeded"


def test_source_delete_removes_rows_and_private_objects(
    client: TestClient,
    session_factory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("app.api.chat_import.put_object", lambda *_args, **_kwargs: None)
    deleted_keys: list[str] = []
    monkeypatch.setattr(
        "app.api.chat_import.delete_object",
        lambda key: deleted_keys.append(key),
    )
    auth = register(client, "chat-import-delete@example.com")
    source = create_source(client, auth)
    uploaded = upload_json(client, auth, source["id"])
    assert uploaded.status_code == 202

    response = client.delete(
        f"/chat-sources/{source['id']}",
        headers=headers(auth),
    )
    assert response.status_code == 202
    assert response.json()["status"] == "deleted"
    assert len(deleted_keys) == 1

    with session_factory() as db:
        assert db.get(ChatSource, source["id"]) is None
        assert db.scalar(
            select(ChatImportJob.id).where(
                ChatImportJob.tenant_id == auth["tenant"]["id"]
            )
        ) is None
        assert db.scalar(
            select(ChatImportFile.id).where(
                ChatImportFile.tenant_id == auth["tenant"]["id"]
            )
        ) is None


def test_retry_only_reopens_failed_or_cancelled_jobs(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("app.api.chat_import.put_object", lambda *_args, **_kwargs: None)
    auth = register(client, "chat-import-retry@example.com")
    source = create_source(client, auth)
    response = upload_json(client, auth, source["id"])
    job_id = response.json()["job"]["id"]

    invalid = client.post(
        f"/chat-imports/{job_id}/retry",
        headers=headers(auth),
    )
    assert invalid.status_code == 409

    cancelled = client.post(
        f"/chat-imports/{job_id}/cancel",
        headers=headers(auth),
    )
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled"

    retry = client.post(
        f"/chat-imports/{job_id}/retry",
        headers=headers(auth),
    )
    assert retry.status_code == 202
    assert retry.json()["status"] == "queued"
