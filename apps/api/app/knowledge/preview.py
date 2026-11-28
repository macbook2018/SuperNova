"""Read one page from the same published index the document is retrieved from."""

from uuid import UUID

from sqlalchemy import and_, func, select, true, union_all
from sqlalchemy.orm import Session

from app.knowledge.indexing import IndexConflict
from app.knowledge.structured_chunking import LOCATION_FIELDS
from app.models.catalog import KnowledgeBase
from app.models.knowledge import (
    KnowledgeChunk,
    KnowledgeDocument,
    KnowledgeIndexChunk,
    KnowledgeIndexMember,
    KnowledgeIndexRelease,
    KnowledgeIndexState,
)
from app.schemas.knowledge import DocumentChunkPage, PreviewChunk

_SPAN_FIELDS = LOCATION_FIELDS | {
    "block_index",
    "start",
    "end",
    "chunk_start",
    "chunk_end",
    "page_char_start",
    "page_char_end",
}


def _preview_chunk(row) -> PreviewChunk:
    metadata = row.metadata_json or {}
    spans = metadata.get("source_spans") or [metadata]
    locations = [
        {key: value for key, value in span.items() if key in _SPAN_FIELDS and value is not None}
        for span in spans
    ]
    return PreviewChunk(
        id=row.chunk_id,
        chunk_index=row.chunk_index,
        content=row.content,
        heading_path=metadata.get("heading_path") or [],
        locations=[loc for loc in locations if loc],
    )


def published_chunk_page(
    db: Session,
    tenant_id: UUID,
    kb_id: UUID,
    document_id: UUID,
    limit: int,
    offset: int,
    snapshot: str | None,
) -> DocumentChunkPage:
    doc, state, release, member = (
        KnowledgeDocument,
        KnowledgeIndexState,
        KnowledgeIndexRelease,
        KnowledgeIndexMember,
    )
    # Read the pointer, manifest and page in one statement. Publication or deletion
    # between two SQL statements must not make an old slice appear under a new version.
    scope = (
        select(
            doc.active_index_version,
            doc.status,
            state.active_release_id,
            func.coalesce(state.generation, 0).label("generation"),
            release.revision.label("release_revision"),
            member.document_id.label("member_id"),
        )
        .select_from(doc)
        .join(
            KnowledgeBase,
            and_(
                KnowledgeBase.id == doc.knowledge_base_id, KnowledgeBase.tenant_id == doc.tenant_id
            ),
        )
        .outerjoin(state, and_(state.tenant_id == tenant_id, state.knowledge_base_id == kb_id))
        .outerjoin(
            release,
            and_(
                release.tenant_id == tenant_id,
                release.knowledge_base_id == kb_id,
                release.id == state.active_release_id,
                release.status == "ready",
            ),
        )
        .outerjoin(
            member,
            and_(
                member.tenant_id == tenant_id,
                member.knowledge_base_id == kb_id,
                member.release_id == release.id,
                member.document_id == doc.id,
                member.status == "ready",
                member.source_version == doc.active_index_version,
                member.source_sha256.is_not_distinct_from(doc.sha256),
            ),
        )
        .where(
            doc.tenant_id == tenant_id,
            doc.knowledge_base_id == kb_id,
            doc.id == document_id,
            doc.status.not_in(("deleting", "deleted")),
            KnowledgeBase.enabled.is_(True),
        )
        .cte("preview_document")
    )

    def chunks(model, version_filter):
        return (
            select(
                model.id.label("chunk_id"), model.chunk_index, model.content, model.metadata_json
            )
            .select_from(scope)
            .join(model, version_filter)
            .where(
                model.tenant_id == tenant_id,
                model.knowledge_base_id == kb_id,
                model.document_id == document_id,
                scope.c.status == "ready",
            )
        )

    legacy = chunks(
        KnowledgeChunk,
        and_(
            scope.c.active_release_id.is_(None),
            KnowledgeChunk.index_version == scope.c.active_index_version,
        ),
    )
    published = chunks(
        KnowledgeIndexChunk,
        and_(
            scope.c.member_id.is_not(None),
            KnowledgeIndexChunk.release_id == scope.c.active_release_id,
        ),
    )
    page = (
        union_all(legacy, published).order_by("chunk_index").limit(limit + 1).offset(offset).cte()
    )
    rows = db.execute(
        select(scope, page).select_from(scope.outerjoin(page, true())).order_by(page.c.chunk_index)
    ).all()
    if not rows:
        raise LookupError("document_not_found")
    current = rows[0]
    if current.status != "ready" or current.active_index_version <= 0:
        raise IndexConflict("document_not_ready")
    if current.active_release_id is not None and current.member_id is None:
        raise IndexConflict("index_manifest_changed")
    token = f"{document_id}:{current.generation}:{current.active_release_id or 'v1'}"
    token += f":{current.active_index_version}"
    if snapshot is not None and snapshot != token:
        raise IndexConflict("preview_version_changed")
    return DocumentChunkPage(
        document_id=document_id,
        snapshot=token,
        document_index_version=current.active_index_version,
        active_release_id=current.active_release_id,
        release_revision=current.release_revision,
        items=[_preview_chunk(r) for r in rows[:limit] if r.chunk_id is not None],
        has_more=len(rows) > limit,
    )
