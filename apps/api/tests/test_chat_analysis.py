from __future__ import annotations

from collections.abc import Generator
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.chat_import import analysis, worker
from app.chat_import.analysis import AnalysisModelConfig, ChatAnalysisResult
from app.chat_import.parsers import NormalizedChatMessage
from app.db.base import Base
from app.db.session import get_db
from app.main import app
from app.models.catalog import ModelProvider, Persona
from app.models.chat_analysis import (
    ChatAnalysisReviewEvent,
)
from app.models.chat_import import ChatImportFile, ChatImportJob, ChatSource
from app.models.knowledge import KnowledgeDocument, KnowledgeIngestionJob


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
            "display_name": "Chat Analysis Tester",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def headers(tokens: dict) -> dict[str, str]:
    return {"Authorization": f"Bearer {tokens['access_token']}"}


def _source(client: TestClient, auth: dict) -> dict:
    response = client.post(
        "/chat-sources",
        headers=headers(auth),
        json={"name": "账号 A", "platform": "wechat", "export_tool": "test"},
    )
    assert response.status_code == 201, response.text
    return response.json()


def _seed_ready_import(
    session_factory, auth: dict, source: dict, body: bytes
) -> tuple[UUID, UUID]:
    tenant_id = UUID(auth["tenant"]["id"])
    source_id = UUID(source["source_account_id"])
    import_job_id = uuid4()
    with session_factory() as db:
        source_row = db.get(ChatSource, source_id)
        source_row.target_sender_ref = "owner"
        source_row.target_sender_name = "本人"
        source_row.identity_confirmed_at = datetime.now(UTC)
        db.add(
            ChatImportJob(
                id=import_job_id,
                tenant_id=tenant_id,
                source_id=source_id,
                status="ready_for_analysis",
                progress=100,
                total_files=1,
                processed_files=1,
                parsed_text_messages=3,
                ignored_non_text_messages=0,
                unique_senders=2,
                attempts=1,
                max_attempts=5,
            )
        )
        db.add(
            ChatImportFile(
                id=uuid4(),
                tenant_id=tenant_id,
                source_id=source_id,
                job_id=import_job_id,
                original_filename="chat.json",
                extension=".json",
                mime_type="application/json",
                file_size_bytes=len(body),
                sha256="a" * 64,
                object_key=f"temporary/{uuid4().hex}.json",
                status="parsed",
                parsed_text_message_count=3,
                ignored_non_text_message_count=0,
                expires_at=datetime.now(UTC) + timedelta(hours=1),
            )
        )
        db.add(
            ModelProvider(
                id=uuid4(),
                tenant_id=tenant_id,
                name="Test Provider",
                provider_type="openai_compatible",
                base_url="https://api.example.com/v1",
                api_key_ciphertext="test-ciphertext",
                api_key_last4="test",
                enabled=True,
            )
        )
        db.commit()
        provider_id = db.scalar(
            select(ModelProvider.id).where(
                ModelProvider.tenant_id == tenant_id,
                ModelProvider.name == "Test Provider",
            )
        )
    return import_job_id, provider_id


def _sample_body() -> bytes:
    return (
        '{"messages":['
        '{"conversation_id":"conversation-1","sender_id":"owner",'
        '"sender_name":"本人","content":"你好，我来帮你处理。","message_type":"text",'
        '"timestamp":"2026-11-12T10:00:00Z"},'
        '{"conversation_id":"conversation-1","sender_id":"customer",'
        '"sender_name":"客户A","content":"请问价格是多少？","message_type":"text",'
        '"timestamp":"2026-11-12T10:01:00Z"},'
        '{"conversation_id":"conversation-1","sender_id":"owner",'
        '"sender_name":"本人","content":"目前标准价格为 99 元。","message_type":"text",'
        '"timestamp":"2026-11-12T10:02:00Z"}'
        ']}'
    ).encode()


def test_analysis_uses_owner_samples_redacts_secrets_and_extracts_faqs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[dict] = []

    def fake_completion(_config, *, system_prompt, user_payload, max_tokens):
        captured.append(
            {
                "system_prompt": system_prompt,
                "user_payload": user_payload,
                "max_tokens": max_tokens,
            }
        )
        if "owner_messages" in user_payload:
            return (
                {
                    "summary": "表达简洁，语气友好。",
                    "profile": {
                        "language": "中文",
                        "formality": "口语化",
                        "tone": ["友好"],
                        "common_expressions": ["我来帮你处理"],
                    },
                    "system_prompt_suggestion": "用简洁、友好的中文回答。",
                },
                100,
                30,
            )
        return (
            {
                "items": [
                    {
                        "title": "标准价格",
                        "category": "价格",
                        "question": "标准价格是多少？",
                        "question_variants": ["费用如何计算？"],
                        "answer": "目前标准价格为 99 元。",
                        "confidence": 0.94,
                        "occurrence_count": 3,
                        "effective_date": "2026-11-12",
                    }
                ]
            },
            120,
            40,
        )

    monkeypatch.setattr(analysis, "call_json_completion", fake_completion)
    now = datetime.now(UTC)
    records = [
        NormalizedChatMessage(
            conversation_external_id="conversation-1",
            conversation_title="客户A",
            conversation_type="direct",
            external_message_id="private-message-id",
            sender_ref="owner",
            sender_name="本人",
            receiver_ref="customer",
            receiver_name="客户A",
            content="我的联系邮箱是 owner@example.com，请问有什么需要？",
            message_type="text",
            timestamp=now,
            source_file="chat.json",
            source_row=1,
            metadata={},
        ),
        NormalizedChatMessage(
            conversation_external_id="conversation-1",
            conversation_title="客户A",
            conversation_type="direct",
            external_message_id="private-message-id-2",
            sender_ref="customer",
            sender_name="客户A",
            receiver_ref="owner",
            receiver_name="本人",
            content="请问价格是多少？",
            message_type="text",
            timestamp=now + timedelta(minutes=1),
            source_file="chat.json",
            source_row=2,
            metadata={},
        ),
        NormalizedChatMessage(
            conversation_external_id="conversation-1",
            conversation_title="客户A",
            conversation_type="direct",
            external_message_id="private-message-id-3",
            sender_ref="owner",
            sender_name="本人",
            receiver_ref="customer",
            receiver_name="客户A",
            content="目前标准价格为 99 元。",
            message_type="text",
            timestamp=now + timedelta(minutes=2),
            source_file="chat.json",
            source_row=3,
            metadata={},
        ),
    ]
    lease_calls: list[bool] = []
    result = analysis.analyze_chat_records(
        AnalysisModelConfig(
            base_url="https://api.example.com/v1",
            api_key="test",
            model_name="test-model",
        ),
        records,
        "owner",
        on_request=lambda: lease_calls.append(True),
    )

    assert result.owner_message_count == 2
    assert len(result.faq_candidates) == 1
    assert result.faq_candidates[0]["occurrence_count"] == 3
    assert "owner@example.com" not in str(captured[0]["user_payload"])
    assert "[已脱敏]" in str(captured[0]["user_payload"])
    assert "private-message-id" not in str(captured)
    assert "customer" not in str(captured)
    assert len(lease_calls) == 4
    assert result.input_tokens == 220
    assert result.output_tokens == 70
    assert result.request_count == 2


def test_chat_analysis_review_and_faq_publication_are_tenant_scoped(
    client: TestClient,
    session_factory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    auth = register(client, "chat-analysis-owner@example.com")
    source = _source(client, auth)
    body = _sample_body()
    import_job_id, provider_id = _seed_ready_import(
        session_factory,
        auth,
        source,
        body,
    )
    stored_objects: dict[str, bytes] = {}
    with session_factory() as db:
        file_record = db.scalar(
            select(ChatImportFile).where(ChatImportFile.job_id == import_job_id)
        )
        stored_objects[file_record.object_key] = body

    monkeypatch.setattr(
        "app.api.chat_analysis.decrypt_provider_api_key",
        lambda _ciphertext: "test-api-key",
    )
    monkeypatch.setattr(
        "app.api.chat_analysis.validate_public_service_url",
        lambda _url: None,
    )
    monkeypatch.setattr(
        "app.chat_import.worker.resolve_analysis_model",
        lambda *_args: AnalysisModelConfig(
            base_url="https://api.example.com/v1",
            api_key="test-api-key",
            model_name="test-model",
        ),
    )
    monkeypatch.setattr(
        "app.chat_import.worker.get_object",
        lambda key, **_kwargs: stored_objects[key],
    )
    monkeypatch.setattr("app.chat_import.worker.delete_object", lambda _key: None)

    def fake_analyze(_config, records, owner_ref, on_request=None):
        if on_request:
            on_request()
        assert owner_ref == "owner"
        assert len(records) == 3
        assert sum(record.sender_ref == owner_ref for record in records) == 2
        return ChatAnalysisResult(
            persona_summary="表达简洁，语气友好。",
            persona_profile={"language": "中文", "tone": ["友好"]},
            system_prompt_suggestion="用简洁、友好的中文回答。",
            owner_message_count=2,
            faq_candidates=[
                {
                    "title": "标准价格",
                    "category": "价格",
                    "canonical_question": "标准价格是多少？",
                    "question_variants": ["费用如何计算？"],
                    "answer": "目前标准价格为 99 元。",
                    "confidence": 0.94,
                    "occurrence_count": 3,
                    "effective_date": "2026-11-12",
                    "fingerprint": "b" * 64,
                }
            ],
            input_tokens=220,
            output_tokens=70,
            request_count=2,
        )

    monkeypatch.setattr("app.chat_import.worker.analyze_chat_records", fake_analyze)

    start = client.post(
        f"/chat-imports/{import_job_id}/analyze",
        headers=headers(auth),
        json={
            "model_source": "byok",
            "provider_id": str(provider_id),
            "model_name": "test-model",
        },
    )
    assert start.status_code == 202, start.text
    analysis_job_id = UUID(start.json()["id"])

    monkeypatch.setattr(worker, "SessionLocal", session_factory)
    claimed = worker.claim_next_chat_analysis_job("analysis-test-worker")
    assert claimed == analysis_job_id
    worker.process_chat_analysis_job(claimed, "analysis-test-worker")

    bundle_response = client.get(
        f"/chat-imports/{import_job_id}/analysis",
        headers=headers(auth),
    )
    assert bundle_response.status_code == 200, bundle_response.text
    bundle = bundle_response.json()
    assert bundle["analysis_job"]["status"] == "succeeded"
    assert bundle["analysis_job"]["owner_message_count"] == 2
    assert bundle["persona_draft"]["status"] == "pending_review"
    assert bundle["faq_candidates"][0]["status"] == "pending_review"

    tenant_id = UUID(auth["tenant"]["id"])
    with session_factory() as db:
        assert db.scalar(
            select(ChatImportFile.id).where(ChatImportFile.job_id == import_job_id)
        ) is None
        assert db.scalar(
            select(ChatImportJob.status).where(ChatImportJob.id == import_job_id)
        ) == "succeeded"
        assert "chat_messages" not in Base.metadata.tables

        persona = Persona(
            id=uuid4(),
            tenant_id=tenant_id,
            name="客服 Persona",
            slug="support-persona",
            description="",
            system_prompt="你是一个客服。",
            model_source="byok",
            provider_id=provider_id,
            platform_model_id=None,
            model_name="test-model",
            temperature=0.7,
            top_p=1.0,
            max_tokens=2048,
            enabled=True,
        )
        db.add(persona)
        db.commit()
        persona_id = persona.id

    draft_id = bundle["persona_draft"]["id"]
    approved_draft = client.post(
        f"/persona-drafts/{draft_id}/approve",
        headers=headers(auth),
        json={},
    )
    assert approved_draft.status_code == 200, approved_draft.text

    persona_read = client.get(
        f"/personas/{persona_id}",
        headers=headers(auth),
    )
    assert persona_read.status_code == 200
    applied = client.post(
        f"/persona-drafts/{draft_id}/apply",
        headers=headers(auth),
        json={
            "persona_id": str(persona_id),
            "mode": "append",
            "expected_updated_at": persona_read.json()["updated_at"],
        },
    )
    assert applied.status_code == 200, applied.text
    assert applied.json()["status"] == "applied"

    candidate_id = bundle["faq_candidates"][0]["id"]
    approved_faq = client.post(
        f"/faq-candidates/{candidate_id}/approve",
        headers=headers(auth),
        json={},
    )
    assert approved_faq.status_code == 200, approved_faq.text

    knowledge_response = client.post(
        "/knowledge-bases",
        headers=headers(auth),
        json={
            "name": "聊天 FAQ 知识库",
            "description": "人工审核后发布的 FAQ",
            "embedding_source": "platform",
        },
    )
    assert knowledge_response.status_code == 201, knowledge_response.text
    knowledge_base_id = knowledge_response.json()["id"]
    published_objects: dict[str, bytes] = {}
    monkeypatch.setattr(
        "app.api.chat_analysis.put_object",
        lambda key, payload, _mime: published_objects.__setitem__(key, payload),
    )
    published = client.post(
        f"/faq-candidates/{candidate_id}/publish",
        headers=headers(auth),
        json={"knowledge_base_id": knowledge_base_id},
    )
    assert published.status_code == 200, published.text
    assert published.json()["status"] == "published"
    assert published.json()["published_document_id"]
    assert len(published_objects) == 1

    with session_factory() as db:
        document = db.get(
            KnowledgeDocument,
            UUID(published.json()["published_document_id"]),
        )
        assert document is not None
        assert document.status == "queued"
        assert db.scalar(
            select(KnowledgeIngestionJob.id).where(
                KnowledgeIngestionJob.document_id == document.id
            )
        ) is not None
        assert db.scalar(
            select(ChatAnalysisReviewEvent.id).where(
                ChatAnalysisReviewEvent.tenant_id == tenant_id
            )
        ) is not None
        assert "客户A" not in document.original_filename


def test_chat_analysis_bundle_is_hidden_from_other_tenants(
    client: TestClient,
    session_factory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner = register(client, "chat-analysis-private@example.com")
    other = register(client, "chat-analysis-other@example.com")
    source = _source(client, owner)
    body = _sample_body()
    import_job_id, provider_id = _seed_ready_import(
        session_factory,
        owner,
        source,
        body,
    )
    monkeypatch.setattr(
        "app.api.chat_analysis.decrypt_provider_api_key",
        lambda _ciphertext: "test-api-key",
    )
    monkeypatch.setattr(
        "app.api.chat_analysis.validate_public_service_url",
        lambda _url: None,
    )
    started = client.post(
        f"/chat-imports/{import_job_id}/analyze",
        headers=headers(owner),
        json={
            "model_source": "byok",
            "provider_id": str(provider_id),
            "model_name": "test-model",
        },
    )
    assert started.status_code == 202, started.text

    hidden = client.get(
        f"/chat-imports/{import_job_id}/analysis",
        headers=headers(other),
    )
    assert hidden.status_code == 404


def test_cancelling_import_also_cancels_queued_analysis(
    client: TestClient,
    session_factory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    auth = register(client, "chat-analysis-cancel@example.com")
    source = _source(client, auth)
    body = _sample_body()
    import_job_id, provider_id = _seed_ready_import(
        session_factory,
        auth,
        source,
        body,
    )
    monkeypatch.setattr(
        "app.api.chat_analysis.decrypt_provider_api_key",
        lambda _ciphertext: "test-api-key",
    )
    monkeypatch.setattr(
        "app.api.chat_analysis.validate_public_service_url",
        lambda _url: None,
    )
    monkeypatch.setattr("app.api.chat_import.delete_object", lambda _key: None)

    started = client.post(
        f"/chat-imports/{import_job_id}/analyze",
        headers=headers(auth),
        json={
            "model_source": "byok",
            "provider_id": str(provider_id),
            "model_name": "test-model",
        },
    )
    assert started.status_code == 202, started.text

    cancelled = client.post(
        f"/chat-imports/{import_job_id}/cancel",
        headers=headers(auth),
    )
    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()["status"] == "cancelled"

    bundle = client.get(
        f"/chat-imports/{import_job_id}/analysis",
        headers=headers(auth),
    )
    assert bundle.status_code == 200, bundle.text
    assert bundle.json()["analysis_job"]["status"] == "cancelled"
