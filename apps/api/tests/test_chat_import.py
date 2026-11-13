from __future__ import annotations

from collections.abc import Generator
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from app.chat_import import worker
from app.db.base import Base
from app.db.session import get_db
from app.main import app
from app.models.chat_import import (
    ChatImportFile,
    ChatImportJob,
    ChatImportSenderSummary,
    ChatSource,
)
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
        '"sender_name":"本人","content":"你好","message_type":"text",'
        '"timestamp":"2026-11-12T10:00:00Z"},'
        '{"conversation_id":"chat-1","sender_id":"wxid-customer",'
        '"sender_name":"客户A","content":"请问价格","message_type":"text",'
        '"timestamp":"2026-11-12T10:01:00Z"},'
        '{"conversation_id":"chat-1","sender_id":"wxid-customer",'
        '"sender_name":"客户A","content":"[图片]","message_type":"image",'
        '"timestamp":"2026-11-12T10:02:00Z"},'
        '{"conversation_id":"chat-1","sender_id":"wxid-owner",'
        '"sender_name":"本人","content":"[语音]","message_type":"voice",'
        '"timestamp":"2026-11-12T10:03:00Z"}'
        ']}'
    ).encode()


def create_source(client: TestClient, auth: dict, name: str = "微信账号 A") -> dict:
    response = client.post(
        "/chat-sources",
        headers=headers(auth),
        json={"name": name, "platform": "wechat", "export_tool": "generic-export"},
    )
    assert response.status_code == 201, response.text
    return response.json()


def upload_json(
    client: TestClient,
    auth: dict,
    source_account_id: str,
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
        f"/chat-sources/{source_account_id}/imports",
        headers=request_headers,
        files=[("files", ("chat.json", body, "application/json"))],
    )


def test_source_account_id_is_server_generated_and_accounts_are_independent(
    client: TestClient,
) -> None:
    auth = register(client, "source-account-owner@example.com")
    first = create_source(client, auth, "微信账号 A")
    second = create_source(client, auth, "微信账号 B")

    assert first["source_account_id"] == first["id"]
    assert second["source_account_id"] == second["id"]
    assert first["source_account_id"] != second["source_account_id"]


def test_upload_is_tenant_scoped_and_idempotent(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("app.api.chat_import.put_object", lambda *_args, **_kwargs: None)
    auth = register(client, "chat-import-owner@example.com")
    source = create_source(client, auth)

    first = upload_json(
        client, auth, source["source_account_id"], idempotency_key="import-request-001"
    )
    assert first.status_code == 202, first.text
    first_job = first.json()["job"]
    assert first_job["status"] == "queued"
    assert first_job["source_account_id"] == source["source_account_id"]
    assert first.json()["accepted_files"] == 1

    replay = upload_json(
        client, auth, source["source_account_id"], idempotency_key="import-request-001"
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

    first = upload_json(client, auth, source["source_account_id"])
    assert first.status_code == 202

    duplicate = upload_json(client, auth, source["source_account_id"])
    assert duplicate.status_code == 409
    assert duplicate.json()["detail"]["code"] == "chat_import_files_already_seen"


def test_worker_persists_sender_counts_only_and_ignores_media(
    client: TestClient,
    session_factory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stored_objects: dict[str, bytes] = {}
    monkeypatch.setattr(
        "app.api.chat_import.put_object",
        lambda key, body, _mime: stored_objects.__setitem__(key, body),
    )
    auth = register(client, "chat-import-worker@example.com")
    source = create_source(client, auth)
    response = upload_json(client, auth, source["source_account_id"])
    assert response.status_code == 202, response.text

    job_id = response.json()["job"]["id"]
    job_uuid = UUID(job_id)
    tenant_uuid = UUID(auth["tenant"]["id"])
    source_uuid = UUID(source["source_account_id"])

    monkeypatch.setattr(worker, "SessionLocal", session_factory)
    monkeypatch.setattr(
        worker,
        "get_object",
        lambda key, **_kwargs: stored_objects[key],
    )
    monkeypatch.setattr(worker, "delete_object", lambda _key: None)

    claimed = worker.claim_next_chat_import_job("test-worker")
    assert str(claimed) == job_id
    worker.process_chat_import_job(claimed)

    with session_factory() as db:
        job = db.get(ChatImportJob, claimed)
        summaries = db.scalars(
            select(ChatImportSenderSummary).where(
                ChatImportSenderSummary.tenant_id == tenant_uuid,
                ChatImportSenderSummary.source_id == source_uuid,
            )
        ).all()
        assert job.status == "awaiting_identity"
        assert job.parsed_text_messages == 2
        assert job.ignored_non_text_messages == 2
        assert job.unique_senders == 2
        assert {item.sender_ref for item in summaries} == {"wxid-owner", "wxid-customer"}
        assert sum(item.message_count for item in summaries) == 2
        assert "chat_messages" not in Base.metadata.tables
        assert "chat_conversations" not in Base.metadata.tables
        assert "chat_participants" not in Base.metadata.tables
        assert all(
            "content" not in table.columns
            for table in Base.metadata.tables.values()
            if table.name.startswith("chat_")
        )

    job_detail = client.get(f"/chat-imports/{job_id}", headers=headers(auth))
    assert job_detail.status_code == 200
    assert "files" not in job_detail.json()
    assert job_detail.json()["parsed_text_messages"] == 2

    messages_response = client.get(
        f"/chat-sources/{source['source_account_id']}/messages",
        headers=headers(auth),
    )
    assert messages_response.status_code == 404

    preview = client.get(
        f"/chat-sources/{source['source_account_id']}/preview",
        headers=headers(auth),
    )
    assert preview.status_code == 200
    assert preview.json()["identity_confirmed"] is False
    assert "conversations" not in preview.json()
    assert {item["sender_ref"] for item in preview.json()["senders"]} == {
        "wxid-owner",
        "wxid-customer",
    }


def test_owner_confirmation_is_scoped_to_one_source_account(
    client: TestClient,
    session_factory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stored_objects: dict[str, bytes] = {}
    monkeypatch.setattr(
        "app.api.chat_import.put_object",
        lambda key, body, _mime: stored_objects.__setitem__(key, body),
    )
    auth = register(client, "chat-import-account-isolation@example.com")
    first = create_source(client, auth, "账号一")
    second = create_source(client, auth, "账号二")

    monkeypatch.setattr(worker, "SessionLocal", session_factory)
    monkeypatch.setattr(
        worker,
        "get_object",
        lambda key, **_kwargs: stored_objects[key],
    )
    monkeypatch.setattr(worker, "delete_object", lambda _key: None)

    first_upload = upload_json(client, auth, first["source_account_id"])
    first_job_id = first_upload.json()["job"]["id"]
    claimed = worker.claim_next_chat_import_job("test-worker")
    worker.process_chat_import_job(claimed)
    assert str(claimed) == first_job_id

    confirmed = client.post(
        f"/chat-sources/{first['source_account_id']}/confirm-identity",
        headers=headers(auth),
        json={"target_sender_ref": "wxid-owner"},
    )
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["target_sender_ref"] == "wxid-owner"

    with session_factory() as db:
        first_source = db.get(ChatSource, UUID(first["source_account_id"]))
        second_source = db.get(ChatSource, UUID(second["source_account_id"]))
        first_job = db.get(ChatImportJob, UUID(first_job_id))
        assert first_source.identity_confirmed_at is not None
        assert second_source.identity_confirmed_at is None
        assert first_job.status == "ready_for_analysis"
        assert db.scalar(
            select(ChatImportSenderSummary.id).where(
                ChatImportSenderSummary.source_id == UUID(first["source_account_id"])
            )
        ) is None


def test_cancel_deletes_temporary_upload_and_sender_summary(
    client: TestClient,
    session_factory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stored_objects: dict[str, bytes] = {}
    deleted_keys: list[str] = []
    monkeypatch.setattr(
        "app.api.chat_import.put_object",
        lambda key, body, _mime: stored_objects.__setitem__(key, body),
    )
    monkeypatch.setattr(
        "app.api.chat_import.delete_object",
        lambda key: deleted_keys.append(key),
    )
    auth = register(client, "chat-import-cancel@example.com")
    source = create_source(client, auth)
    uploaded = upload_json(client, auth, source["source_account_id"])
    job_id = uploaded.json()["job"]["id"]

    response = client.post(
        f"/chat-imports/{job_id}/cancel",
        headers=headers(auth),
    )
    assert response.status_code == 200
    assert response.json()["status"] == "cancelled"
    assert len(deleted_keys) == 1

    with session_factory() as db:
        assert db.scalar(
            select(ChatImportFile.id).where(ChatImportFile.job_id == UUID(job_id))
        ) is None


def test_expired_temporary_upload_is_deleted_and_job_fails(
    client: TestClient,
    session_factory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stored_objects: dict[str, bytes] = {}
    deleted_keys: list[str] = []
    monkeypatch.setattr(
        "app.api.chat_import.put_object",
        lambda key, body, _mime: stored_objects.__setitem__(key, body),
    )
    monkeypatch.setattr(
        worker,
        "delete_object",
        lambda key: deleted_keys.append(key),
    )
    monkeypatch.setattr(worker, "SessionLocal", session_factory)

    auth = register(client, "chat-import-expiry@example.com")
    source = create_source(client, auth)
    uploaded = upload_json(client, auth, source["source_account_id"])
    job_id = UUID(uploaded.json()["job"]["id"])

    with session_factory() as db:
        file_record = db.scalar(
            select(ChatImportFile).where(ChatImportFile.job_id == job_id)
        )
        file_record.expires_at = datetime.now(UTC) - timedelta(minutes=1)
        db.commit()

    assert worker.cleanup_expired_chat_import_uploads() == 1
    with session_factory() as db:
        job = db.get(ChatImportJob, job_id)
        assert job.status == "failed"
        assert "过期" in job.error_summary
        assert db.scalar(
            select(ChatImportFile.id).where(ChatImportFile.job_id == job_id)
        ) is None
    assert len(deleted_keys) == 1


def test_source_delete_removes_jobs_and_private_objects(
    client: TestClient,
    session_factory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    deleted_keys: list[str] = []
    monkeypatch.setattr("app.api.chat_import.put_object", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        "app.api.chat_import.delete_object",
        lambda key: deleted_keys.append(key),
    )
    auth = register(client, "chat-import-delete@example.com")
    source = create_source(client, auth)
    uploaded = upload_json(client, auth, source["source_account_id"])
    assert uploaded.status_code == 202

    response = client.delete(
        f"/chat-sources/{source['source_account_id']}",
        headers=headers(auth),
    )
    assert response.status_code == 202
    assert response.json()["status"] == "deleted"
    assert len(deleted_keys) == 1

    tenant_uuid = UUID(auth["tenant"]["id"])
    source_uuid = UUID(source["source_account_id"])
    with session_factory() as db:
        assert db.get(ChatSource, source_uuid) is None
        assert db.scalar(
            select(ChatImportJob.id).where(ChatImportJob.tenant_id == tenant_uuid)
        ) is None
        assert db.scalar(
            select(ChatImportFile.id).where(ChatImportFile.tenant_id == tenant_uuid)
        ) is None
