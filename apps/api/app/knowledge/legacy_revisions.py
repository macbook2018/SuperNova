"""Document-scoped checkpoints for the original knowledge index."""

from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.knowledge.embeddings import (
    EmbeddingConfig,
    embedding_config_fingerprint,
    resolve_embedding_config,
)
from app.knowledge.indexing import IndexConflict, lock_index_kb, prepare_legacy_change
from app.models.catalog import KnowledgeBase
from app.models.knowledge import (
    KnowledgeChunk,
    KnowledgeDocument,
    KnowledgeDocumentIndexRevision,
    KnowledgeIndexState,
    KnowledgeIngestionJob,
)


def _complete_chunks(db, scope, version: int, expected_count: int) -> bool:
    if expected_count <= 0:
        return False
    count, first, last = db.execute(
        select(
            func.count(KnowledgeChunk.id),
            func.min(KnowledgeChunk.chunk_index),
            func.max(KnowledgeChunk.chunk_index),
        ).where(
            KnowledgeChunk.tenant_id == scope[0],
            KnowledgeChunk.knowledge_base_id == scope[1],
            KnowledgeChunk.document_id == scope[2],
            KnowledgeChunk.index_version == version,
        )
    ).one()
    # (document, version, ordinal) is unique, so these bounds also exclude holes.
    return (count, first, last) == (expected_count, 0, expected_count - 1)


def next_document_index_version(db: Session, document: KnowledgeDocument) -> int:
    # Earlier writers can reset the document counter after a rollback. Allocated versions
    # include completed or failed jobs and historical chunks with no revision descriptor.
    version_queries = []
    for model in (KnowledgeChunk, KnowledgeDocumentIndexRevision, KnowledgeIngestionJob):
        version_queries.append(
            select(func.max(model.index_version))
            .where(
                model.tenant_id == document.tenant_id,
                model.knowledge_base_id == document.knowledge_base_id,
                model.document_id == document.id,
            )
            .scalar_subquery()
        )
    observed = db.execute(select(*version_queries)).one()
    highest = max(
        document.active_index_version,
        document.pending_index_version or 0,
        *(value or 0 for value in observed),
    )
    return highest + 1


def record_revisions(
    db: Session,
    document: KnowledgeDocument,
    kb: KnowledgeBase,
    config: EmbeddingConfig,
    *,
    new_version: int,
    new_count: int,
) -> None:
    """Called while holding the KB and document locks in the final write transaction."""
    scope = (document.tenant_id, document.knowledge_base_id, document.id)
    fingerprint = embedding_config_fingerprint(kb, config)
    active = document.active_index_version
    if new_version <= active or db.get(KnowledgeDocumentIndexRevision, (*scope, new_version)):
        raise IndexConflict("index_revision_already_exists")
    existing_chunk = db.scalar(
        select(KnowledgeChunk.id)
        .where(
            KnowledgeChunk.tenant_id == scope[0],
            KnowledgeChunk.knowledge_base_id == scope[1],
            KnowledgeChunk.document_id == scope[2],
            KnowledgeChunk.index_version == new_version,
        )
        .limit(1)
    )
    if existing_chunk is not None:
        raise IndexConflict("index_revision_already_exists")
    revisions = [(new_version, new_count, "generated")]
    if active and db.get(KnowledgeDocumentIndexRevision, (*scope, active)) is None:
        if _complete_chunks(db, scope, active, document.chunk_count):
            # Historical rows do not prove which model originally made the vectors.
            revisions.append((active, document.chunk_count, "legacy_unknown"))
    for version, count, origin in revisions:
        db.add(
            KnowledgeDocumentIndexRevision(
                tenant_id=document.tenant_id,
                knowledge_base_id=document.knowledge_base_id,
                document_id=document.id,
                index_version=version,
                source_sha256=document.sha256,
                chunk_count=count,
                reader_fingerprint=fingerprint,
                profile_origin=origin,
            )
        )


def _document(db, tenant_id, kb_id, doc_id, *, lock):
    q = select(KnowledgeDocument).where(
        KnowledgeDocument.tenant_id == tenant_id,
        KnowledgeDocument.knowledge_base_id == kb_id,
        KnowledgeDocument.id == doc_id,
        KnowledgeDocument.status.not_in(("deleted", "deleting")),
    )
    if lock:
        q = q.with_for_update().execution_options(populate_existing=True)
    doc = db.scalar(q)
    if doc is None:
        raise LookupError("knowledge_document_not_found")
    return doc


def list_revisions(
    db: Session,
    tenant_id: UUID,
    kb_id: UUID,
    doc_id: UUID,
    limit: int,
    offset: int,
) -> dict:
    doc = _document(db, tenant_id, kb_id, doc_id, lock=False)
    state = db.get(KnowledgeIndexState, (tenant_id, kb_id))
    revisions = db.scalars(
        select(KnowledgeDocumentIndexRevision)
        .where(
            KnowledgeDocumentIndexRevision.tenant_id == tenant_id,
            KnowledgeDocumentIndexRevision.knowledge_base_id == kb_id,
            KnowledgeDocumentIndexRevision.document_id == doc_id,
        )
        .order_by(KnowledgeDocumentIndexRevision.index_version.desc())
        .limit(limit)
        .offset(offset)
    ).all()
    return {
        "active_index_version": doc.active_index_version,
        "generation": state.generation if state else 0,
        "revisions": revisions,
    }


def restore_revision(
    db: Session,
    tenant_id: UUID,
    kb_id: UUID,
    doc_id: UUID,
    version: int,
    expected_active_index_version: int,
    expected_generation: int,
) -> dict:
    kb = lock_index_kb(db, tenant_id, kb_id)
    if not kb.enabled:
        raise IndexConflict("knowledge_base_disabled")
    doc = _document(db, tenant_id, kb_id, doc_id, lock=True)
    scope = (tenant_id, kb_id, doc_id)
    state = db.get(KnowledgeIndexState, (tenant_id, kb_id), populate_existing=True)
    generation = state.generation if state else 0
    if (
        expected_generation != generation
        or doc.active_index_version != expected_active_index_version
    ):
        raise IndexConflict("index_generation_conflict")
    if doc.status != "ready" or db.scalar(
        select(KnowledgeIngestionJob.id)
        .where(
            KnowledgeIngestionJob.tenant_id == tenant_id,
            KnowledgeIngestionJob.knowledge_base_id == kb_id,
            KnowledgeIngestionJob.document_id == doc_id,
            KnowledgeIngestionJob.status.in_(("queued", "running", "retry_wait")),
        )
        .limit(1)
    ):
        raise IndexConflict("index_source_documents_not_ready")
    revision = db.get(KnowledgeDocumentIndexRevision, (*scope, version))
    if revision is None:
        raise LookupError("index_revision_not_found")
    if revision.source_sha256 != doc.sha256 or not _complete_chunks(
        db, scope, version, revision.chunk_count
    ):
        raise IndexConflict("index_legacy_projection_incomplete")
    try:
        fingerprint = embedding_config_fingerprint(kb, resolve_embedding_config(db, kb))
    except (ValueError, RuntimeError) as exc:
        raise IndexConflict("index_embedding_profile_unavailable") from exc
    if fingerprint != revision.reader_fingerprint:
        raise IndexConflict("index_embedding_profile_changed")
    if version != doc.active_index_version or (state and state.active_release_id is not None):
        if state is None:
            state = KnowledgeIndexState(
                tenant_id=tenant_id, knowledge_base_id=kb_id, last_revision=0, generation=0
            )
            db.add(state)
            db.flush()
        prepare_legacy_change(db, tenant_id, kb_id)
        # Keep pending_index_version as the high-water mark for allocating the next job.
        doc.pending_index_version = max(
            doc.pending_index_version, doc.active_index_version, version
        )
        doc.active_index_version = version
        doc.chunk_count = revision.chunk_count
        db.flush()
    return {
        "active_index_version": doc.active_index_version,
        "generation": state.generation if state else 0,
    }
