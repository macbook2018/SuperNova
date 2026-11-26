"""Worker for the isolated candidate-index queue, independent of v1 ingestion jobs."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import func, or_, select, update

from app.core.config import get_settings
from app.db.session import SessionLocal
from app.knowledge.chunking import chunk_blocks
from app.knowledge.embeddings import embed_texts, resolve_snapshot_embedding_config
from app.knowledge.indexing import SUPPORTED_PARSER_VERSIONS, lock_index_kb
from app.knowledge.observations import observe_embedding_calls
from app.knowledge.parsers import parse_document, validate_document_bytes
from app.knowledge.storage import get_object
from app.models.catalog import KnowledgeBase
from app.models.knowledge import (
    KnowledgeDocument,
    KnowledgeIndexBuildJob,
    KnowledgeIndexChunk,
    KnowledgeIndexMember,
    KnowledgeIndexRelease,
    KnowledgeIndexState,
)


class LostIndexLease(Exception):
    pass


def _lease_end():
    return datetime.now(UTC) + timedelta(seconds=get_settings().knowledge_worker_lease_seconds)


def _owned(job_id, token):
    return (
        KnowledgeIndexBuildJob.id == job_id,
        KnowledgeIndexBuildJob.lease_token == token,
        KnowledgeIndexBuildJob.status == "running",
        KnowledgeIndexBuildJob.lease_expires_at > datetime.now(UTC),
    )


def extend(job_id, token):
    with SessionLocal.begin() as db:
        changed = db.execute(
            update(KnowledgeIndexBuildJob)
            .where(*_owned(job_id, token))
            .values(
                lease_expires_at=_lease_end(),
            )
        )
        if changed.rowcount != 1:
            raise LostIndexLease


def claim() -> tuple[UUID, UUID] | None:
    now = datetime.now(UTC)
    with SessionLocal.begin() as db:
        job = db.scalar(
            select(KnowledgeIndexBuildJob)
            .join(
                KnowledgeIndexRelease, KnowledgeIndexRelease.id == KnowledgeIndexBuildJob.release_id
            )
            .where(
                KnowledgeIndexRelease.status == "building",
                KnowledgeIndexRelease.config_json["parser_version"]
                .as_string()
                .in_(SUPPORTED_PARSER_VERSIONS),
                or_(
                    KnowledgeIndexBuildJob.status.in_(("queued", "retry_wait"))
                    & (KnowledgeIndexBuildJob.available_at <= now),
                    (KnowledgeIndexBuildJob.status == "running")
                    & (KnowledgeIndexBuildJob.lease_expires_at <= now),
                ),
            )
            .order_by(KnowledgeIndexBuildJob.available_at, KnowledgeIndexBuildJob.id)
            .limit(1)
            .with_for_update(of=KnowledgeIndexBuildJob, skip_locked=True)
        )
        if job is None:
            return None
        job.status = "running"
        job.lease_token = uuid4()
        job.lease_expires_at = _lease_end()
        job.attempts += 1
        return job.id, job.lease_token


def _target(db, job, token, *, for_commit):
    kb = (
        lock_index_kb(db, job.tenant_id, job.knowledge_base_id)
        if for_commit
        else db.get(KnowledgeBase, job.knowledge_base_id)
    )
    if not kb or kb.tenant_id != job.tenant_id:
        raise LostIndexLease
    q = select(KnowledgeIndexBuildJob).where(*_owned(job.id, token))
    if for_commit:
        q = q.with_for_update()
    if db.scalar(q.execution_options(populate_existing=True)) is None:
        raise LostIndexLease
    release = db.scalar(
        select(KnowledgeIndexRelease)
        .where(
            KnowledgeIndexRelease.id == job.release_id,
            KnowledgeIndexRelease.tenant_id == kb.tenant_id,
            KnowledgeIndexRelease.knowledge_base_id == kb.id,
        )
        .execution_options(populate_existing=True)
    )
    state = db.get(KnowledgeIndexState, (kb.tenant_id, kb.id), populate_existing=True)
    member = db.scalar(
        select(KnowledgeIndexMember)
        .where(
            KnowledgeIndexMember.release_id == job.release_id,
            KnowledgeIndexMember.tenant_id == kb.tenant_id,
            KnowledgeIndexMember.knowledge_base_id == kb.id,
            KnowledgeIndexMember.document_id == job.document_id,
        )
        .execution_options(populate_existing=True)
    )
    doc = db.scalar(
        select(KnowledgeDocument)
        .where(
            KnowledgeDocument.id == job.document_id,
            KnowledgeDocument.tenant_id == kb.tenant_id,
            KnowledgeDocument.knowledge_base_id == kb.id,
        )
        .execution_options(populate_existing=True)
    )
    if (
        not kb.enabled
        or not release
        or not state
        or state.generation != release.expected_generation
        or release.status != "building"
        or not member
        or not doc
        or doc.status != "ready"
        or doc.active_index_version != member.source_version
        or doc.sha256 != member.source_sha256
    ):
        raise ValueError("index_manifest_changed")
    return kb, release, member, doc


def _fail(job_id, token, error):
    with SessionLocal.begin() as db:
        job = db.get(KnowledgeIndexBuildJob, job_id)
        if job is None:
            return
        try:
            lock_index_kb(db, job.tenant_id, job.knowledge_base_id)
        except LookupError:
            # The KB and its jobs were deleted after the first read.
            return
        job = db.scalar(
            select(KnowledgeIndexBuildJob)
            .where(*_owned(job_id, token))
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if job is None:
            return
        # Persist a category without provider response bodies or source text.
        job.last_error = type(error).__name__
        job.lease_token = None
        job.lease_expires_at = None
        if job.attempts < job.max_attempts and not isinstance(error, ValueError):
            job.status = "retry_wait"
            job.available_at = datetime.now(UTC) + timedelta(seconds=min(300, 2**job.attempts))
            return
        job.status = "dead_letter"
        db.execute(
            update(KnowledgeIndexRelease)
            .where(
                KnowledgeIndexRelease.id == job.release_id,
                KnowledgeIndexRelease.status == "building",
            )
            .values(status="failed")
        )
        db.execute(
            update(KnowledgeIndexMember)
            .where(
                KnowledgeIndexMember.release_id == job.release_id,
                KnowledgeIndexMember.document_id == job.document_id,
            )
            .values(status="failed")
        )
        db.execute(
            update(KnowledgeIndexBuildJob)
            .where(
                KnowledgeIndexBuildJob.release_id == job.release_id,
                KnowledgeIndexBuildJob.id != job.id,
                KnowledgeIndexBuildJob.status.in_(("queued", "running", "retry_wait")),
            )
            .values(status="cancelled", lease_token=None, lease_expires_at=None)
        )


def process(job_id: UUID, token: UUID) -> None:
    try:
        with SessionLocal() as db:
            job = db.get(KnowledgeIndexBuildJob, job_id)
            if job is None:
                return
            kb, release, member, doc = _target(db, job, token, for_commit=False)
            if job.attempts > job.max_attempts:
                raise ValueError("index_build_attempts_exhausted")
            settings = get_settings()
            body = get_object(
                doc.object_key,
                max(settings.knowledge_max_upload_bytes, settings.knowledge_max_url_bytes),
            )
            if hashlib.sha256(body).hexdigest() != member.source_sha256:
                raise ValueError("index_source_hash_mismatch")
            mime = validate_document_bytes(doc.original_filename, doc.mime_type, body)
            config = release.config_json
            strategy = config["parser_version"]
            blocks = parse_document(doc.original_filename, mime, body, strategy=strategy)
            chunks = chunk_blocks(
                blocks, chunk_size=config["chunk_size"], chunk_overlap=config["chunk_overlap"],
                strategy=strategy,
            )
            if not chunks or len(chunks) > 5000:
                raise ValueError("document_chunk_limit_exceeded")
            embedding = resolve_snapshot_embedding_config(
                db, kb, config["embedding_profile"], release.embedding_fingerprint
            )
            # Release the read transaction before provider I/O.
            db.commit()
            extend(job_id, token)
            with observe_embedding_calls(
                db,
                tenant_id=kb.tenant_id,
                knowledge_base_id=kb.id,
                operation="shadow_embedding",
                job_id=job.id,
                release_id=release.id,
                document_id=doc.id,
            ):
                batch = embed_texts(
                    embedding,
                    [c.get("index_text", c["content"]) for c in chunks],
                    on_batch=lambda: extend(job_id, token),
                )
            if len(batch.vectors) != len(chunks):
                raise ValueError("embedding_count_mismatch")
            kb, release, member, doc = _target(db, job, token, for_commit=True)
            resolve_snapshot_embedding_config(
                db, kb, config["embedding_profile"], release.embedding_fingerprint
            )
            db.add_all(
                [
                    KnowledgeIndexChunk(
                        id=uuid4(),
                        tenant_id=kb.tenant_id,
                        knowledge_base_id=kb.id,
                        release_id=release.id,
                        document_id=doc.id,
                        chunk_index=i,
                        content=c["content"],
                        content_hash=c["content_hash"],
                        metadata_json=c["metadata"],
                        embedding=v,
                    )
                    for i, (c, v) in enumerate(zip(chunks, batch.vectors, strict=True))
                ]
            )
            member.chunk_count = len(chunks)
            member.status = "ready"
            job.status = "succeeded"
            job.lease_token = None
            job.lease_expires_at = None
            job.last_error = None
            db.flush()
            pending = db.scalar(
                select(func.count())
                .select_from(KnowledgeIndexMember)
                .where(
                    KnowledgeIndexMember.release_id == release.id,
                    KnowledgeIndexMember.status != "ready",
                )
            )
            if not pending:
                release.status = "ready"
            db.commit()
    except LostIndexLease:
        return
    except Exception as exc:
        _fail(job_id, token, exc)
