"""Force a second claim while the first worker is outside its DB transaction."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from app.knowledge import index_worker, indexing, worker
from app.knowledge.embeddings import EmbeddingBatch, EmbeddingConfig
from app.models.identity import Tenant
from app.models.knowledge import (
    KnowledgeChunk,
    KnowledgeDocumentIndexRevision,
    KnowledgeIndexBuildJob,
    KnowledgeIndexChunk,
    KnowledgeIndexRelease,
    KnowledgeIngestionJob,
    PlatformEmbeddingModel,
)
from sqlalchemy import func, select
from test_knowledge_postgres import _knowledge_base, _ready_document, postgres_session_factory  # noqa: F401

CONTENT = "退款需要原订单号。"
VECTORS = EmbeddingBatch([[0.3] * 1536], 6, True, 1)
MODEL = EmbeddingConfig("https://example.org", "fixture", "openai", "fixture-model", 1536)


@pytest.fixture
def sources(postgres_session_factory, monkeypatch):  # noqa: F811
    sessions = postgres_session_factory
    tenant = uuid4()
    kb = _knowledge_base(tenant, "Fencing")
    doc, chunk = _ready_document(
        tenant_id=tenant,
        knowledge_base_id=kb.id,
        filename="one.txt",
        object_key="obj",
        content=CONTENT,
    )
    with sessions.begin() as db:
        pool_model = PlatformEmbeddingModel(
            id=uuid4(),
            code="fixture",
            display_name="Fixture model",
            provider_type=MODEL.provider_type,
            base_url=MODEL.base_url,
            upstream_model_name=MODEL.model_name,
            credential_ref="fixture",
            dimensions=1536,
            enabled=True,
            verified_at=datetime.now(UTC),
        )
        db.add(pool_model)
        kb.platform_embedding_model_id = pool_model.id
        db.add(Tenant(id=tenant, name="A", status="active"))
        db.flush()
        db.add(kb)
        db.flush()
        db.add(doc)
        db.flush()
        db.add(chunk)
    for module in (worker, index_worker):
        monkeypatch.setattr(module, "SessionLocal", sessions)
        monkeypatch.setattr(module, "get_object", lambda *_: CONTENT.encode())
    monkeypatch.setattr(worker, "resolve_embedding_config", lambda *_: MODEL)
    monkeypatch.setattr(indexing, "freeze_embedding_profile", lambda *_: {"fixture": "fixture"})
    monkeypatch.setattr(index_worker, "resolve_snapshot_embedding_config", lambda *_: MODEL)
    return sessions, kb, doc, chunk


@pytest.mark.parametrize("error", [False, True])
def test_reclaimed_legacy_job_rejects_old_workers_success_and_failure(sources, monkeypatch, error):
    sessions, kb, doc, original = sources
    with sessions.begin() as db:
        doc.pending_index_version = 2
        db.add(doc)
        job = KnowledgeIngestionJob(
            id=uuid4(),
            tenant_id=kb.tenant_id,
            knowledge_base_id=kb.id,
            document_id=doc.id,
            job_type="reindex",
            status="queued",
            index_version=2,
            dedupe_key="v1-reindex",
        )
        db.add(job)
    assert worker._claim_job() == job.id

    def reclaim(*args, **kwargs):
        with sessions.begin() as db:
            old = db.get(KnowledgeIngestionJob, job.id)
            old.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
        # Same worker id; only the attempt counter differentiates the lease.
        assert worker._claim_job() == job.id
        if error:
            raise RuntimeError("first attempt failed late")
        return VECTORS

    monkeypatch.setattr(worker, "embed_texts", reclaim)
    worker.process_job(job.id)
    with sessions() as db:
        recovered = db.get(KnowledgeIngestionJob, job.id)
        assert recovered.status == "running" and recovered.attempts == 2
        assert db.get(type(doc), doc.id).active_index_version == 1
        assert db.get(KnowledgeChunk, original.id) is not None
    monkeypatch.setattr(worker, "embed_texts", lambda *a, **k: VECTORS)
    worker.process_job(job.id)
    with sessions() as db:
        assert db.get(type(doc), doc.id).active_index_version == 2
        assert db.get(KnowledgeChunk, original.id) is not None
        assert db.get(KnowledgeIngestionJob, job.id).status == "succeeded"


@pytest.mark.parametrize("error", [False, True])
def test_shadow_job_reclaim_protects_release_until_new_lease_commits(sources, monkeypatch, error):
    sessions, kb, doc, original = sources
    with sessions.begin() as db:
        release = indexing.create_index_build(
            db,
            tenant_id=kb.tenant_id,
            knowledge_base_id=kb.id,
            expected_generation=0,
            request_key="fixture",
            config={},
        )
    assert worker._claim_job() is None  # legacy worker queue never sees shadow work
    job_id, old_token = index_worker.claim()
    new_claim = []

    def reclaim(*args, **kwargs):
        with sessions.begin() as db:
            job = db.get(KnowledgeIndexBuildJob, job_id)
            job.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
        new_claim.extend(index_worker.claim())
        if error:
            raise ValueError("first attempt failed late")
        return VECTORS

    monkeypatch.setattr(index_worker, "embed_texts", reclaim)
    index_worker.process(job_id, old_token)
    assert new_claim[0] == job_id and new_claim[1] != old_token
    with sessions() as db:
        assert db.get(KnowledgeIndexRelease, release.id).status == "building"
        assert db.get(KnowledgeIndexBuildJob, job_id).lease_token == new_claim[1]
        assert db.scalar(select(func.count(KnowledgeIndexChunk.id))) == 0
        assert db.get(KnowledgeChunk, original.id) is not None
    monkeypatch.setattr(index_worker, "embed_texts", lambda *a, **k: VECTORS)
    index_worker.process(*new_claim)
    with sessions() as db:
        assert db.get(KnowledgeIndexRelease, release.id).status == "ready"
        assert db.get(KnowledgeIndexBuildJob, job_id).status == "succeeded"
        assert db.scalar(select(func.count(KnowledgeIndexChunk.id))) == 1
        assert db.get(KnowledgeChunk, original.id) is not None


def test_cancelling_shadow_in_flight_preserves_live_document(sources, monkeypatch):
    sessions, kb, doc, original = sources
    with sessions.begin() as db:
        release = indexing.create_index_build(
            db,
            tenant_id=kb.tenant_id,
            knowledge_base_id=kb.id,
            expected_generation=0,
            request_key="fixture",
            config={},
        )
    job_id, token = index_worker.claim()

    def cancel(*args, **kwargs):
        with sessions.begin() as db:
            indexing.prepare_legacy_change(db, kb.tenant_id, kb.id)
        return VECTORS

    monkeypatch.setattr(index_worker, "embed_texts", cancel)
    index_worker.process(job_id, token)
    with sessions() as db:
        assert db.get(KnowledgeIndexRelease, release.id).status == "failed"
        assert db.get(KnowledgeIndexBuildJob, job_id).status == "cancelled"
        assert db.scalar(select(func.count(KnowledgeIndexChunk.id))) == 0
        assert db.get(KnowledgeChunk, original.id) is not None


@pytest.mark.parametrize("race", ["expired", "new_version", "deleted", "same_version_won"])
def test_legacy_completion_never_overwrites_superseded_index(sources, monkeypatch, race):
    sessions, kb, doc, original = sources
    with sessions.begin() as db:
        doc.pending_index_version = 2
        db.add(doc)
        job = KnowledgeIngestionJob(
            id=uuid4(),
            tenant_id=kb.tenant_id,
            knowledge_base_id=kb.id,
            document_id=doc.id,
            job_type="reindex",
            status="queued",
            index_version=2,
            dedupe_key="reindex",
        )
        db.add(job)
    assert worker._claim_job() == job.id

    def change_record(*args, **kwargs):
        with sessions.begin() as db:
            source = db.get(type(doc), doc.id)
            if race == "expired":
                db.get(KnowledgeIngestionJob, job.id).lease_expires_at = datetime.now(
                    UTC
                ) - timedelta(seconds=1)
            elif race == "new_version":
                source.pending_index_version = 3
            elif race == "same_version_won":
                source.active_index_version = 2
            else:
                source.status = "deleting"
        return VECTORS

    monkeypatch.setattr(worker, "embed_texts", change_record)
    worker.process_job(job.id)
    with sessions() as db:
        assert db.get(KnowledgeChunk, original.id) is not None
        assert db.get(KnowledgeIngestionJob, job.id).status == (
            "running" if race == "expired" else "cancelled"
        )


def test_expired_mismatched_legacy_job_cannot_mark_other_kb_document(sources):
    sessions, kb, doc, chunk = sources
    with sessions.begin() as db:
        other = _knowledge_base(kb.tenant_id, "Different KB of the same tenant")
        db.add(other)
        db.flush()
        now = datetime.now(UTC)
        job = KnowledgeIngestionJob(
            id=uuid4(),
            tenant_id=kb.tenant_id,
            knowledge_base_id=other.id,
            document_id=doc.id,
            job_type="reindex",
            status="running",
            index_version=doc.pending_index_version,
            attempts=5,
            max_attempts=5,
            worker_id="expired",
            lease_expires_at=now - timedelta(seconds=1),
            dedupe_key="incorrect-legacy-association",
        )
        db.add(job)
    assert worker._claim_job() is None
    with sessions() as db:
        assert db.get(KnowledgeIngestionJob, job.id).status == "dead_letter"
        source = db.get(type(doc), doc.id)
        assert source.status == "ready"
        assert source.error_message is None
        assert source.error_code is None


def test_old_worker_leaves_newer_parser_jobs_queued(sources, monkeypatch):
    sessions, kb, doc, chunk = sources
    # The API is on the next release, but the polling worker only understands this release.
    monkeypatch.setattr(indexing, "SUPPORTED_PARSER_VERSIONS", {"legacy", "future-parser"})
    with sessions.begin() as db:
        future = indexing.create_index_build(
            db,
            tenant_id=kb.tenant_id,
            knowledge_base_id=kb.id,
            expected_generation=0,
            request_key="future",
            config={"parser_version": "future-parser"},
        )
        legacy = indexing.create_index_build(
            db,
            tenant_id=kb.tenant_id,
            knowledge_base_id=kb.id,
            expected_generation=0,
            request_key="legacy",
            config={},
        )
    job_id, token = index_worker.claim()
    with sessions() as db:
        assert db.get(KnowledgeIndexBuildJob, job_id).release_id == legacy.id
        job = db.scalar(
            select(KnowledgeIndexBuildJob).where(KnowledgeIndexBuildJob.release_id == future.id)
        )
        assert job.status == "queued"
        assert job.attempts == 0
    assert index_worker.claim() is None


def test_explicit_document_delete_removes_content_from_both_index_generations(sources, monkeypatch):
    sessions, kb, doc, original = sources
    with sessions.begin() as db:
        indexing.create_index_build(
            db,
            tenant_id=kb.tenant_id,
            knowledge_base_id=kb.id,
            expected_generation=0,
            request_key="build",
            config={},
        )
    monkeypatch.setattr(index_worker, "embed_texts", lambda *a, **k: VECTORS)
    index_worker.process(*index_worker.claim())
    with sessions.begin() as db:
        assert db.scalar(select(func.count(KnowledgeIndexChunk.id))) == 1
        indexing.prepare_legacy_change(db, kb.tenant_id, kb.id)
        db.get(type(doc), doc.id).status = "deleting"
        db.add(
            KnowledgeIngestionJob(
                tenant_id=kb.tenant_id,
                knowledge_base_id=kb.id,
                document_id=doc.id,
                job_type="delete",
                status="queued",
                index_version=1,
                dedupe_key="delete",
            )
        )
    deleted = []
    monkeypatch.setattr(worker, "delete_object", deleted.append)
    worker.process_job(worker._claim_job())
    with sessions() as db:
        assert db.get(type(doc), doc.id).status == "deleted"
        assert db.scalar(select(func.count(KnowledgeIndexChunk.id))) == 0
        assert db.scalar(select(func.count(KnowledgeChunk.id))) == 0
    assert deleted == [doc.object_key]


@pytest.mark.parametrize("has_descriptor", [False, True])
def test_old_target_version_cannot_overwrite_retained_chunks(sources, monkeypatch, has_descriptor):
    sessions, kb, doc, original = sources
    # Simulate a stale producer that reuses version 2 after the live document returned to v1.
    scope = (kb.tenant_id, kb.id, doc.id, 2)
    retained = KnowledgeChunk(
        id=uuid4(),
        tenant_id=kb.tenant_id,
        knowledge_base_id=kb.id,
        document_id=doc.id,
        index_version=2,
        chunk_index=0,
        content="old material",
        content_hash="old-physical-hash",
        metadata_json={},
        embedding=[0.5] * 1536,
    )
    with sessions.begin() as db:
        db.add(retained)
        doc.pending_index_version = 2
        db.add(doc)
        if has_descriptor:
            db.add(
                KnowledgeDocumentIndexRevision(
                    tenant_id=kb.tenant_id,
                    knowledge_base_id=kb.id,
                    document_id=doc.id,
                    index_version=2,
                    chunk_count=1,
                    profile_origin="generated",
                    source_sha256=doc.sha256,
                    reader_fingerprint="a" * 64,
                )
            )
        job = KnowledgeIngestionJob(
            tenant_id=kb.tenant_id,
            knowledge_base_id=kb.id,
            document_id=doc.id,
            index_version=2,
            status="queued",
            job_type="reindex",
            dedupe_key="old-client-rebuild",
        )
        db.add(job)
    monkeypatch.setattr(worker, "embed_texts", lambda *a, **k: VECTORS)
    worker.process_job(worker._claim_job())
    with sessions() as db:
        stale_job = db.get(KnowledgeIngestionJob, job.id)
        assert (stale_job.status, stale_job.last_error) == (
            "dead_letter",
            "index_revision_already_exists",
        )
        assert db.get(type(doc), doc.id).active_index_version == 1
        assert db.get(KnowledgeChunk, original.id) is not None
        saved = db.get(KnowledgeChunk, retained.id)
        assert saved.content == "old material" and saved.embedding[0] == 0.5
        descriptor = db.get(KnowledgeDocumentIndexRevision, scope)
        assert (descriptor is not None) == has_descriptor
        if descriptor:
            assert descriptor.reader_fingerprint == "a" * 64
