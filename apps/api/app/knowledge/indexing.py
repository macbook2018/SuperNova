"""Immutable candidate indexes. All changes to a KB manifest serialize on its KB row."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import and_, func, select, update
from sqlalchemy.orm import Session

from app.knowledge.embeddings import (
    embedding_fingerprint,
    freeze_embedding_profile,
    resolve_snapshot_embedding_config,
)
from app.models.catalog import KnowledgeBase
from app.models.knowledge import (
    KnowledgeChunk,
    KnowledgeDocument,
    KnowledgeIndexBuildJob,
    KnowledgeIndexMember,
    KnowledgeIndexRelease,
    KnowledgeIndexState,
    KnowledgeIngestionJob,
)

SUPPORTED_PARSER_VERSIONS = frozenset({"legacy"})


class IndexConflict(ValueError):
    """A stale or unavailable snapshot cannot be built or published."""


def lock_index_kb(db: Session, tenant_id: UUID, knowledge_base_id: UUID) -> KnowledgeBase:
    kb = db.scalar(
        select(KnowledgeBase)
        .where(KnowledgeBase.tenant_id == tenant_id, KnowledgeBase.id == knowledge_base_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if kb is None:
        raise LookupError("knowledge_base_not_found")
    return kb


def _state(db, kb):
    return db.get(KnowledgeIndexState, (kb.tenant_id, kb.id), populate_existing=True)


def prepare_legacy_change(db: Session, tenant_id: UUID, knowledge_base_id: UUID) -> None:
    """Caller holds the KB row lock; the ordinary write and pointer change commit together."""
    state = _state(db, lock_index_kb(db, tenant_id, knowledge_base_id))
    if state is None:
        return
    state.active_release_id = None
    state.generation += 1
    # Stop submitting external work for a snapshot that can never be activated.
    stale = select(KnowledgeIndexRelease.id).where(
        KnowledgeIndexRelease.tenant_id == tenant_id,
        KnowledgeIndexRelease.knowledge_base_id == knowledge_base_id,
        KnowledgeIndexRelease.status == "building",
    )
    db.execute(
        update(KnowledgeIndexBuildJob)
        .where(
            KnowledgeIndexBuildJob.release_id.in_(stale),
            KnowledgeIndexBuildJob.status.in_(("queued", "retry_wait", "running")),
        )
        .values(status="cancelled", lease_token=None, lease_expires_at=None)
    )
    db.execute(
        update(KnowledgeIndexRelease)
        .where(KnowledgeIndexRelease.id.in_(stale))
        .values(status="failed")
    )


def _manifest(db, kb):
    documents = list(
        db.scalars(
            select(KnowledgeDocument)
            .where(
                KnowledgeDocument.tenant_id == kb.tenant_id,
                KnowledgeDocument.knowledge_base_id == kb.id,
                KnowledgeDocument.status != "deleted",
            )
            .execution_options(populate_existing=True)
        )
    )
    if not documents or any(
        doc.status != "ready"
        or doc.active_index_version <= 0
        or not doc.sha256
        or not doc.chunk_count
        for doc in documents
    ):
        raise IndexConflict("index_source_documents_not_ready")
    # A reindex leaves a document ready during its build.
    pending = db.scalar(
        select(KnowledgeIngestionJob.id)
        .where(
            KnowledgeIngestionJob.tenant_id == kb.tenant_id,
            KnowledgeIngestionJob.knowledge_base_id == kb.id,
            KnowledgeIngestionJob.status.in_(("queued", "running", "retry_wait")),
        )
        .limit(1)
    )
    if pending is not None:
        raise IndexConflict("index_source_jobs_in_progress")
    return {doc.id: (doc.active_index_version, doc.sha256) for doc in documents}


def _validate_legacy_projection(db, kb):
    merged = (
        select(KnowledgeDocument.id)
        .outerjoin(
            KnowledgeChunk,
            and_(
                KnowledgeChunk.tenant_id == KnowledgeDocument.tenant_id,
                KnowledgeChunk.knowledge_base_id == KnowledgeDocument.knowledge_base_id,
                KnowledgeChunk.document_id == KnowledgeDocument.id,
                KnowledgeChunk.index_version == KnowledgeDocument.active_index_version,
            ),
        )
        .where(
            KnowledgeDocument.tenant_id == kb.tenant_id,
            KnowledgeDocument.knowledge_base_id == kb.id,
            KnowledgeDocument.status == "ready",
        )
        .group_by(KnowledgeDocument.id, KnowledgeDocument.chunk_count)
        .having(func.count(KnowledgeChunk.id) != KnowledgeDocument.chunk_count)
        .limit(1)
    )
    if db.scalar(merged) is not None:
        raise IndexConflict("index_legacy_projection_incomplete")


def create_index_build(
    db: Session,
    *,
    tenant_id: UUID,
    knowledge_base_id: UUID,
    request_key: str,
    config: dict,
    expected_generation: int,
) -> KnowledgeIndexRelease:
    kb = lock_index_kb(db, tenant_id, knowledge_base_id)
    if not kb.enabled:
        raise IndexConflict("knowledge_base_disabled")
    if not request_key.strip() or len(request_key) > 180:
        raise ValueError("invalid_request_key")
    if config.keys() - {"parser_version", "chunk_size", "chunk_overlap"}:
        raise ValueError("unknown_index_configuration")
    parser_version = config.get("parser_version", "legacy")
    size = config.get("chunk_size", kb.chunk_size)
    overlap = config.get("chunk_overlap", kb.chunk_overlap)
    if parser_version not in SUPPORTED_PARSER_VERSIONS:
        raise ValueError("unsupported_index_parser")
    if type(size) is not int or size < 100 or size > 4000:
        raise ValueError("chunk_size_out_of_range")
    if type(overlap) is not int or overlap < 0 or overlap >= size:
        raise ValueError("chunk_overlap_out_of_range")
    digest = hashlib.sha256(
        json.dumps([expected_generation, config], sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    existing = db.scalar(
        select(KnowledgeIndexRelease).where(
            KnowledgeIndexRelease.tenant_id == tenant_id,
            KnowledgeIndexRelease.knowledge_base_id == kb.id,
            KnowledgeIndexRelease.request_key == request_key,
        )
    )
    if existing is not None:
        if existing.config_json.get("request_hash") != digest:
            raise IndexConflict("index_idempotency_key_conflict")
        return existing
    state = _state(db, kb)
    if state is None:
        state = KnowledgeIndexState(
            tenant_id=tenant_id, knowledge_base_id=kb.id, generation=0, last_revision=0
        )
        db.add(state)
    if expected_generation != state.generation:
        raise IndexConflict("index_generation_conflict")
    manifest = _manifest(db, kb)
    _validate_legacy_projection(db, kb)
    profile = freeze_embedding_profile(db, kb)
    state.last_revision += 1
    release = KnowledgeIndexRelease(
        id=uuid4(),
        tenant_id=tenant_id,
        knowledge_base_id=kb.id,
        revision=state.last_revision,
        expected_generation=state.generation,
        request_key=request_key,
        status="building",
        config_json={
            "parser_version": parser_version,
            "chunk_size": size,
            "chunk_overlap": overlap,
            "embedding_profile": profile,
            "request_hash": digest,
        },
        embedding_fingerprint=embedding_fingerprint(profile),
    )
    db.add(release)
    db.flush()
    for doc_id, (version, digest) in manifest.items():
        db.add(
            KnowledgeIndexMember(
                tenant_id=tenant_id,
                knowledge_base_id=kb.id,
                release_id=release.id,
                document_id=doc_id,
                source_version=version,
                source_sha256=digest,
                status="pending",
                chunk_count=0,
            )
        )
    db.flush()
    db.add_all(
        [
            KnowledgeIndexBuildJob(
                id=uuid4(),
                tenant_id=tenant_id,
                knowledge_base_id=kb.id,
                release_id=release.id,
                document_id=doc_id,
                status="queued",
            )
            for doc_id in manifest
        ]
    )
    return release


def switch_index_release(
    db: Session,
    *,
    tenant_id: UUID,
    knowledge_base_id: UUID,
    release_id: UUID | None,
    expected_generation: int,
    rollback: bool = False,
) -> KnowledgeIndexState:
    kb = lock_index_kb(db, tenant_id, knowledge_base_id)
    if not kb.enabled:
        raise IndexConflict("knowledge_base_disabled")
    # Legacy writers also lock this row in their compatibility trigger. An old writer
    # that commits after our manifest check must see and invalidate our new pointer.
    state = db.scalar(
        select(KnowledgeIndexState)
        .where(
            KnowledgeIndexState.tenant_id == tenant_id,
            KnowledgeIndexState.knowledge_base_id == knowledge_base_id,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if state is None or state.generation != expected_generation:
        raise IndexConflict("index_generation_conflict")
    if release_id is None:
        if not rollback:
            raise IndexConflict("index_release_required")
        _validate_legacy_projection(db, kb)
    else:
        release = db.scalar(
            select(KnowledgeIndexRelease).where(
                KnowledgeIndexRelease.tenant_id == tenant_id,
                KnowledgeIndexRelease.knowledge_base_id == kb.id,
                KnowledgeIndexRelease.id == release_id,
            )
        )
        if release is None:
            raise LookupError("index_release_not_found")
        if release.status != "ready":
            raise IndexConflict("index_release_not_ready")
        if rollback:
            if release.published_at is None:
                raise IndexConflict("index_release_never_published")
        elif release.expected_generation != state.generation:
            raise IndexConflict("index_release_stale")
        members = db.scalars(
            select(KnowledgeIndexMember).where(
                KnowledgeIndexMember.tenant_id == tenant_id,
                KnowledgeIndexMember.knowledge_base_id == kb.id,
                KnowledgeIndexMember.release_id == release_id,
            )
        ).all()
        snapshot = {m.document_id: (m.source_version, m.source_sha256) for m in members}
        if any(m.status != "ready" or m.chunk_count <= 0 for m in members) or snapshot != _manifest(
            db, kb
        ):
            raise IndexConflict("index_manifest_changed")
        resolve_snapshot_embedding_config(
            db, kb, release.config_json["embedding_profile"], release.embedding_fingerprint
        )
        if release.published_at is None:
            release.published_at = datetime.now(UTC)
    if state.active_release_id != release_id:
        state.active_release_id = release_id
        state.generation += 1
    return state
