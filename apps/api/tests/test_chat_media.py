from __future__ import annotations

import io
import json
import zipfile
from datetime import UTC, datetime
from typing import Generator
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.chat_import import worker
from app.db.base import Base
from app.db.session import get_db
from app.main import app
from app.models.chat_import import (
    ChatConversation,
    ChatImportFile,
    ChatImportJob,
    ChatMediaAsset,
    ChatMediaExtractionJob,
    ChatMessage,
    ChatParticipant,
)


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
            "display_name": "Media Tester",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def headers(tokens: dict) -> dict[str, str]:
    return {"Authorization": f"Bearer {tokens['access_token']}"}


def create_source(client: TestClient, auth: dict) -> dict:
    response = client.post(
        "/chat-sources",
        headers=headers(auth),
        json={"name": "WeKeep 媒体测试", "export_tool": "WeKeep"},
    )
    assert response.status_code == 201, response.text
    return response.json()


def wekeep_archive() -> bytes:
    manifest = {
        "application": "WeKeep",
        "version": "1.0.1",
        "session": "wxid_customer",
        "name": "客户会话",
        "exported_at": "2026-11-13T14:07:48",
        "time_zone": "Asia/Shanghai",
        "count": 2,
        "chunks": [{"file": "c000001.js", "count": 2}],
        "resource_summary": {
            "available": 2,
            "missing": 6,
            "encrypted": 0,
            "unsupported": 0,
            "corrupt": 0,
            "skipped": 0,
        },
    }
    html = (
        "<!doctype html><html><body><script>const manifest="
        + json.dumps(manifest, ensure_ascii=False)
        + ";let page=0;</script></body></html>"
    )
    report = [
        {
            "message_id": "msg-image",
            "kind": "image",
            "status": "missing",
            "notice": "本机仅有缩略图，原图未缓存。",
            "resources": [
                {
                    "kind": "image",
                    "path": "resources/image/thumb.jpg",
                    "status": "available",
                    "role": "thumbnail",
                    "quality": "thumbnail",
                }
            ],
            "image_diagnostics": {"format": "v2", "aes_verified": True},
        },
        {
            "message_id": "msg-voice",
            "kind": "voice",
            "status": "missing",
            "notice": "语音原文件尚未导出。",
            "resources": [],
            "audio_diagnostics": {},
        },
    ]
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("客户会话/index.html", html)
        archive.writestr("客户会话/c000001.js", "window.__wxChunk=[];")
        archive.writestr(
            "客户会话/resource-report.jsonl",
            "\n".join(json.dumps(row, ensure_ascii=False) for row in report),
        )
        archive.writestr("客户会话/resources/image/thumb.jpg", b"thumbnail-bytes")
        archive.writestr("客户会话/avatars/owner.jpg", b"owner-avatar")
        archive.writestr("客户会话/avatars/customer.jpg", b"customer-avatar")
    return buffer.getvalue()


def seed_parsed_wekeep_file(session_factory, auth: dict, source: dict) -> tuple:
    tenant_id = UUID(auth["tenant"]["id"])
    source_id = UUID(source["id"])
    job_id = uuid4()
    file_id = uuid4()
    conversation_id = uuid4()
    owner_id = uuid4()
    customer_id = uuid4()
    now = datetime.now(UTC)

    owner_metadata = {
        "parser": "wekeep-export-v1",
        "wekeep": {
            "kind": "image",
            "resource_status": "missing",
            "resource_notice": "本机仅有缩略图，原图未缓存。",
            "resources": [
                {
                    "kind": "image",
                    "path": "resources/image/thumb.jpg",
                    "status": "available",
                    "role": "thumbnail",
                    "quality": "thumbnail",
                }
            ],
            "avatar": "avatars/owner.jpg",
        },
    }
    customer_metadata = {
        "parser": "wekeep-export-v1",
        "wekeep": {
            "kind": "voice",
            "resource_status": "missing",
            "resource_notice": "语音原文件尚未导出。",
            "resources": [],
            "avatar": "avatars/customer.jpg",
        },
    }

    with session_factory() as db:
        db.add(
            ChatImportJob(
                id=job_id,
                tenant_id=tenant_id,
                source_id=source_id,
                status="succeeded",
                progress=100,
                total_files=1,
                processed_files=1,
                parsed_messages=2,
                imported_messages=2,
                max_attempts=5,
            )
        )
        db.flush()
        db.add(
            ChatImportFile(
                id=file_id,
                tenant_id=tenant_id,
                source_id=source_id,
                job_id=job_id,
                original_filename="wekeep-export.zip",
                extension=".zip",
                mime_type="application/zip",
                file_size_bytes=100,
                sha256="a" * 64,
                object_key="tenant/source/import/wekeep-export.zip",
                status="parsed",
                parsed_message_count=2,
                metadata_json={"source_filename": "wekeep-export.zip"},
            )
        )
        db.add(
            ChatConversation(
                id=conversation_id,
                tenant_id=tenant_id,
                source_id=source_id,
                external_conversation_id="wxid_customer",
                title="客户会话",
                conversation_type="direct",
                first_message_at=now,
                last_message_at=now,
                message_count=2,
            )
        )
        db.add_all(
            [
                ChatParticipant(
                    id=owner_id,
                    tenant_id=tenant_id,
                    source_id=source_id,
                    conversation_id=conversation_id,
                    sender_ref="wxid-owner",
                    sender_name="本人",
                    identity_role="unknown",
                    message_count=1,
                ),
                ChatParticipant(
                    id=customer_id,
                    tenant_id=tenant_id,
                    source_id=source_id,
                    conversation_id=conversation_id,
                    sender_ref="wxid-customer",
                    sender_name="客户A",
                    identity_role="unknown",
                    message_count=1,
                ),
            ]
        )
        db.add_all(
            [
                ChatMessage(
                    id=uuid4(),
                    tenant_id=tenant_id,
                    source_id=source_id,
                    conversation_id=conversation_id,
                    import_file_id=file_id,
                    external_message_id="msg-image",
                    fingerprint="1" * 64,
                    sender_ref="wxid-owner",
                    sender_name="本人",
                    content="[图片]",
                    message_type="image",
                    timestamp=now,
                    identity_role="unknown",
                    metadata_json=owner_metadata,
                ),
                ChatMessage(
                    id=uuid4(),
                    tenant_id=tenant_id,
                    source_id=source_id,
                    conversation_id=conversation_id,
                    import_file_id=file_id,
                    external_message_id="msg-voice",
                    fingerprint="2" * 64,
                    sender_ref="wxid-customer",
                    sender_name="客户A",
                    content="[语音]",
                    message_type="voice",
                    timestamp=now,
                    identity_role="unknown",
                    metadata_json=customer_metadata,
                ),
            ]
        )
        db.commit()
    return job_id, file_id, conversation_id


def test_media_extraction_job_extracts_available_files_and_preserves_missing_state(
    client: TestClient,
    session_factory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    auth = register(client, "chat-media-owner@example.com")
    source = create_source(client, auth)
    import_job_id, file_id, conversation_id = seed_parsed_wekeep_file(
        session_factory, auth, source
    )

    queued = client.post(
        f"/chat-sources/{source['id']}/media-extractions",
        headers=headers(auth),
    )
    assert queued.status_code == 202, queued.text
    assert queued.json()["accepted_files"] == 1
    media_job_id = queued.json()["jobs"][0]["id"]

    stored: dict[str, tuple[bytes, str]] = {}
    monkeypatch.setattr(worker, "SessionLocal", session_factory)
    monkeypatch.setattr(worker, "get_object", lambda *_args, **_kwargs: wekeep_archive())
    monkeypatch.setattr(
        worker,
        "put_object",
        lambda key, body, mime: stored.__setitem__(key, (body, mime)),
    )
    monkeypatch.setattr(worker, "delete_object", lambda _key: None)

    claimed = worker.claim_next_chat_media_job("test-media-worker")
    assert str(claimed) == media_job_id
    worker.process_chat_media_extraction_job(claimed)

    with session_factory() as db:
        media_job = db.get(ChatMediaExtractionJob, claimed)
        assets = db.scalars(
            select(ChatMediaAsset).where(
                ChatMediaAsset.tenant_id == UUID(auth["tenant"]["id"]),
                ChatMediaAsset.source_id == UUID(source["id"]),
            )
        ).all()
        assert media_job.status == "partial"
        assert media_job.total_assets == 5
        assert media_job.extracted_assets == 3
        assert media_job.missing_assets == 2
        assert media_job.error_count == 0
        assert len(assets) == 5
        assert sum(item.status == "available" for item in assets) == 3
        assert sum(item.status == "missing" for item in assets) == 2
        assert sum(item.asset_type == "avatar" for item in assets) == 2
        assert sum(item.role == "thumbnail" for item in assets) == 1
        assert sum(
            item.asset_type == "voice" and item.status == "missing"
            for item in assets
        ) == 1
        assert len(stored) == 3

    messages = client.get(
        f"/chat-sources/{source['id']}/messages",
        headers=headers(auth),
    )
    assert messages.status_code == 200
    image_message = next(
        item for item in messages.json() if item["message_type"] == "image"
    )
    assert {item["role"] for item in image_message["media_assets"]} == {
        "thumbnail",
        "original",
    }
    thumbnail = next(
        item for item in image_message["media_assets"] if item["role"] == "thumbnail"
    )
    assert thumbnail["status"] == "available"
    assert thumbnail["content_url"].endswith("/content")
    original = next(
        item for item in image_message["media_assets"] if item["role"] == "original"
    )
    assert original["status"] == "missing"
    voice_message = next(
        item for item in messages.json() if item["message_type"] == "voice"
    )
    assert voice_message["media_assets"][0]["status"] == "missing"

    preview = client.get(
        f"/chat-sources/{source['id']}/preview",
        headers=headers(auth),
    )
    assert preview.status_code == 200
    assert all(item["avatar_asset_id"] for item in preview.json()["participants"])

    monkeypatch.setattr(
        "app.api.chat_import.get_object",
        lambda *_args, **_kwargs: b"thumbnail",
    )
    downloaded = client.get(
        thumbnail["content_url"],
        headers=headers(auth),
    )
    assert downloaded.status_code == 200
    assert downloaded.content == b"thumbnail"
    assert downloaded.headers["content-type"] == "image/jpeg"

    other = register(client, "chat-media-other@example.com")
    forbidden = client.get(
        thumbnail["content_url"],
        headers=headers(other),
    )
    assert forbidden.status_code == 404

    jobs = client.get(
        f"/chat-sources/{source['id']}/media-extractions",
        headers=headers(auth),
    )
    assert jobs.status_code == 200
    assert jobs.json()[0]["status"] == "partial"
    assert import_job_id
    assert file_id
    assert conversation_id


def test_media_extraction_endpoint_does_not_merge_other_tenants(
    client: TestClient,
) -> None:
    owner = register(client, "chat-media-tenant-a@example.com")
    source = create_source(client, owner)
    other = register(client, "chat-media-tenant-b@example.com")

    response = client.get(
        f"/chat-sources/{source['id']}/media-extractions",
        headers=headers(other),
    )
    assert response.status_code == 404
