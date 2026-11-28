from __future__ import annotations

import hashlib
import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from app.db.base import Base
from app.knowledge import retrieval
from app.knowledge import worker as knowledge_worker
from app.knowledge.embeddings import EmbeddingBatch, EmbeddingConfig
from app.models import __all__ as MODEL_EXPORTS
from app.models.catalog import KnowledgeBase, Persona, PersonaKnowledgeBase
from app.models.identity import Tenant
from app.models.knowledge import (
    KnowledgeChunk,
    KnowledgeDocument,
    KnowledgeIngestionJob,
    KnowledgeUsageRecord,
)
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.orm import sessionmaker


@pytest.fixture
def postgres_session_factory():
    database_url = os.getenv("SUPERNOVA_DATABASE_URL", "")
    if not database_url.startswith("postgresql"):
        pytest.skip("PostgreSQL RAG integration test requires SUPERNOVA_DATABASE_URL")
    assert MODEL_EXPORTS

    admin_engine = create_engine(database_url, pool_pre_ping=True)
    schema = f"sn_knowledge_{uuid4().hex[:12]}"
    with admin_engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))

    engine = create_engine(
        database_url,
        connect_args={"options": f"-c search_path={schema},public"},
        pool_size=4,
        max_overflow=0,
        pool_pre_ping=True,
    )
    # This schema is fresh. A same-named table in public must not satisfy the check.
    Base.metadata.create_all(engine, checkfirst=False)
    factory = sessionmaker(
        bind=engine,
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
    )
    try:
        yield factory
    finally:
        engine.dispose()
        with admin_engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin_engine.dispose()


def _knowledge_base(tenant_id, name: str) -> KnowledgeBase:
    return KnowledgeBase(
        id=uuid4(),
        tenant_id=tenant_id,
        name=name,
        description="PostgreSQL RAG integration fixture",
        embedding_source="platform",
        platform_embedding_model_id=None,
        embedding_provider_id=None,
        embedding_model_name=None,
        embedding_dimensions=1536,
        chunk_size=800,
        chunk_overlap=120,
        retrieval_top_k=5,
        retrieval_score_threshold=0.15,
        enabled=True,
        active_index_version=1,
    )


def _ready_document(
    *,
    tenant_id,
    knowledge_base_id,
    filename: str,
    object_key: str,
    content: str,
) -> tuple[KnowledgeDocument, KnowledgeChunk]:
    document_id = uuid4()
    body = content.encode("utf-8")
    document = KnowledgeDocument(
        id=document_id,
        tenant_id=tenant_id,
        knowledge_base_id=knowledge_base_id,
        original_filename=filename,
        source_type="upload",
        source_url=None,
        object_key=object_key,
        mime_type="text/markdown",
        file_size_bytes=len(body),
        sha256=hashlib.sha256(body).hexdigest(),
        status="ready",
        active_index_version=1,
        pending_index_version=1,
        chunk_count=1,
    )
    chunk = KnowledgeChunk(
        id=uuid4(),
        tenant_id=tenant_id,
        knowledge_base_id=knowledge_base_id,
        document_id=document_id,
        index_version=1,
        chunk_index=0,
        content=content,
        content_hash=hashlib.sha256(content.encode("utf-8")).hexdigest(),
        embedding=[0.1] * 1536,
        metadata_json={"source_type": "faq"},
    )
    return document, chunk


def test_postgres_hybrid_retrieval_returns_only_persona_linked_citations(
    postgres_session_factory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tenant_id = uuid4()
    persona_id = uuid4()
    linked_kb = _knowledge_base(tenant_id, "客服知识库")
    unlinked_kb = _knowledge_base(tenant_id, "未关联知识库")
    linked_content = "常见问题：退货期限是多久？\n标准答复：商品支持签收后 30 天内退货。"
    unlinked_content = "其他人格内部信息：此内容不应进入当前客服上下文。"
    linked_document, linked_chunk = _ready_document(
        tenant_id=tenant_id,
        knowledge_base_id=linked_kb.id,
        filename="退货政策.md",
        object_key=f"tenant/{tenant_id}/knowledge/{linked_kb.id}/faq.md",
        content=linked_content,
    )
    unlinked_document, unlinked_chunk = _ready_document(
        tenant_id=tenant_id,
        knowledge_base_id=unlinked_kb.id,
        filename="内部资料.md",
        object_key=f"tenant/{tenant_id}/knowledge/{unlinked_kb.id}/internal.md",
        content=unlinked_content,
    )
    persona = Persona(
        id=persona_id,
        tenant_id=tenant_id,
        name="客服 Persona",
        slug="postgres-rag-support",
        description="",
        system_prompt="你是一名专业客服。",
        model_source="byok",
        provider_id=None,
        platform_model_id=None,
        model_name="test-model",
        temperature=0.4,
        top_p=0.9,
        max_tokens=1024,
        enabled=True,
    )

    with postgres_session_factory() as db:
        db.add(Tenant(id=tenant_id, name="PostgreSQL RAG Integration", status="active"))
        db.flush()
        db.add_all([linked_kb, unlinked_kb, persona])
        db.flush()
        db.add(
            PersonaKnowledgeBase(
                tenant_id=tenant_id,
                persona_id=persona_id,
                knowledge_base_id=linked_kb.id,
            )
        )
        db.add_all(
            [
                linked_document,
                unlinked_document,
            ]
        )
        db.flush()
        db.add_all([linked_chunk, unlinked_chunk])
        db.commit()

        monkeypatch.setattr(
            retrieval,
            "resolve_embedding_config",
            lambda *_args: EmbeddingConfig(
                base_url="https://embedding.example.com/v1",
                api_key="test-api-key",
                provider_type="openai_compatible",
                model_name="test-embedding",
                dimensions=1536,
            ),
        )
        monkeypatch.setattr(
            retrieval,
            "embed_texts",
            lambda _config, texts: EmbeddingBatch(
                vectors=[[0.1] * 1536 for _ in texts],
                input_tokens=8,
                usage_known=True,
                request_count=1,
            ),
        )

        context, citations = retrieval.retrieve_persona_context(
            db,
            tenant_id=tenant_id,
            persona_id=persona_id,
            query="退货期限是多久？",
        )

        assert len(citations) == 1
        assert citations[0]["knowledge_base_id"] == str(linked_kb.id)
        assert citations[0]["document_id"] == str(linked_document.id)
        assert citations[0]["chunk_id"] == str(linked_chunk.id)
        assert citations[0]["document_name"] == "退货政策.md"
        assert citations[0]["score"] >= 0.99
        assert "30 天内退货" in context
        assert "不可信的引用数据" in context
        assert "其他人格内部信息" not in context
        assert db.scalar(select(func.count(KnowledgeUsageRecord.id))) == 1


@pytest.mark.parametrize(
    ("job_type", "document_status", "active_index_version", "expected_document_status"),
    [
        ("ingest", "processing", 0, "failed"),
        ("reindex", "ready", 1, "ready"),
        ("delete", "deleting", 1, "deleting"),
    ],
)
def test_knowledge_worker_dead_letters_expired_jobs_at_max_attempts(
    postgres_session_factory,
    monkeypatch: pytest.MonkeyPatch,
    job_type: str,
    document_status: str,
    active_index_version: int,
    expected_document_status: str,
) -> None:
    tenant_id = uuid4()
    knowledge_base = _knowledge_base(tenant_id, f"Retry {job_type}")
    document_id = uuid4()
    now = datetime.now(UTC)
    document = KnowledgeDocument(
        id=document_id,
        tenant_id=tenant_id,
        knowledge_base_id=knowledge_base.id,
        original_filename=f"{job_type}.md",
        source_type="upload",
        source_url=None,
        object_key=f"tenant/{tenant_id}/knowledge/{knowledge_base.id}/{document_id}.md",
        mime_type="text/markdown",
        file_size_bytes=0,
        sha256=hashlib.sha256(job_type.encode("utf-8")).hexdigest(),
        status=document_status,
        active_index_version=active_index_version,
        pending_index_version=max(1, active_index_version),
        chunk_count=0,
    )
    job = KnowledgeIngestionJob(
        id=uuid4(),
        tenant_id=tenant_id,
        knowledge_base_id=knowledge_base.id,
        document_id=document_id,
        job_type=job_type,
        status="running",
        dedupe_key=f"expired-{job_type}-{uuid4().hex}",
        index_version=max(1, active_index_version),
        attempts=5,
        max_attempts=5,
        available_at=now - timedelta(minutes=2),
        locked_at=now - timedelta(minutes=2),
        lease_expires_at=now - timedelta(minutes=1),
        worker_id="stale-worker",
    )

    with postgres_session_factory() as db:
        db.add(Tenant(id=tenant_id, name=f"Retry {job_type}", status="active"))
        db.flush()
        db.add(knowledge_base)
        db.flush()
        db.add(document)
        db.flush()
        db.add(job)
        db.commit()

    monkeypatch.setattr(knowledge_worker, "SessionLocal", postgres_session_factory)
    monkeypatch.setattr(knowledge_worker, "WORKER_ID", "knowledge-retry-test-worker")

    assert knowledge_worker._claim_job() is None

    with postgres_session_factory() as db:
        saved_job = db.get(KnowledgeIngestionJob, job.id)
        saved_document = db.get(KnowledgeDocument, document_id)
        assert saved_job is not None
        assert saved_job.status == "dead_letter"
        assert saved_job.attempts == 5
        assert saved_job.last_error == "knowledge_max_attempts_exceeded"
        assert saved_job.worker_id is None
        assert saved_job.lease_expires_at is None
        assert saved_document is not None
        assert saved_document.status == expected_document_status
        assert saved_document.error_code == "MaxAttemptsExceeded"
