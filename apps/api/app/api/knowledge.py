from __future__ import annotations

import hashlib
from pathlib import PurePosixPath
from typing import Annotated
from urllib.parse import quote
from uuid import UUID, uuid4

from fastapi import APIRouter, File, HTTPException, UploadFile, status
from fastapi.responses import Response
from sqlalchemy import and_, func, select
from sqlalchemy.exc import IntegrityError

from app.api.deps import CurrentPrincipal, DatabaseSession
from app.api.knowledge_versions import document_history_router
from app.api.knowledge_versions import router as index_versions_router
from app.core.config import get_settings
from app.knowledge.indexing import prepare_legacy_change
from app.knowledge.legacy_revisions import next_document_index_version
from app.knowledge.parsers import validate_document_bytes
from app.knowledge.retrieval import retrieve_knowledge_base
from app.knowledge.storage import delete_object, get_object, put_object
from app.models.catalog import KnowledgeBase
from app.models.knowledge import (
    KnowledgeDocument,
    KnowledgeIndexMember,
    KnowledgeIndexObservation,
    KnowledgeIndexState,
    KnowledgeIngestionJob,
    KnowledgeUsageRecord,
)
from app.schemas.knowledge import (
    KnowledgeDocumentRead,
    KnowledgeImportUrlRequest,
    KnowledgeOperationUsage,
    KnowledgeSearchHit,
    KnowledgeSearchRequest,
    KnowledgeSearchResponse,
    KnowledgeUsageSummary,
)

router = APIRouter(tags=["知识库文档"])
router.include_router(index_versions_router)
router.include_router(document_history_router)


def _get_knowledge_base(
    db, tenant_id: UUID, knowledge_base_id: UUID, *, lock: bool = False
) -> KnowledgeBase:
    query = select(KnowledgeBase).where(
        KnowledgeBase.id == knowledge_base_id,
        KnowledgeBase.tenant_id == tenant_id,
    )
    if lock:
        query = query.with_for_update().execution_options(populate_existing=True)
    record = db.scalar(query)
    if record is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "knowledge_base_not_found", "message": "知识库不存在"},
        )
    if not record.enabled:
        raise HTTPException(
            status_code=409,
            detail={"code": "knowledge_base_disabled", "message": "知识库已停用"},
        )
    return record


def _document_read(document: KnowledgeDocument) -> KnowledgeDocumentRead:
    return KnowledgeDocumentRead(
        id=document.id,
        knowledge_base_id=document.knowledge_base_id,
        original_filename=document.original_filename,
        source_type=document.source_type,
        source_url=document.source_url,
        mime_type=document.mime_type,
        file_size_bytes=document.file_size_bytes,
        sha256=document.sha256,
        status=document.status,
        active_index_version=document.active_index_version,
        chunk_count=document.chunk_count,
        error_code=document.error_code,
        error_message=document.error_message,
        created_at=document.created_at,
        updated_at=document.updated_at,
    )


def _enqueue_job(
    db,
    *,
    tenant_id: UUID,
    knowledge_base_id: UUID,
    document_id: UUID,
    job_type: str,
    index_version: int,
    dedupe_key: str,
) -> KnowledgeIngestionJob:
    job = KnowledgeIngestionJob(
        id=uuid4(),
        tenant_id=tenant_id,
        knowledge_base_id=knowledge_base_id,
        document_id=document_id,
        job_type=job_type,
        status="queued",
        dedupe_key=dedupe_key,
        index_version=index_version,
    )
    db.add(job)
    return job


def _lock_document(db, tenant_id, knowledge_base_id, document_id):
    document = db.scalar(
        select(KnowledgeDocument).where(
            KnowledgeDocument.id == document_id,
            KnowledgeDocument.tenant_id == tenant_id,
            KnowledgeDocument.knowledge_base_id == knowledge_base_id,
        ).with_for_update().execution_options(populate_existing=True)
    )
    if document is None:
        raise HTTPException(404, detail={"code": "document_not_found", "message": "文档不存在"})
    return document


def _has_pending_job(db, document, job_types):
    return db.scalar(select(KnowledgeIngestionJob.id).where(
        KnowledgeIngestionJob.tenant_id == document.tenant_id,
        KnowledgeIngestionJob.knowledge_base_id == document.knowledge_base_id,
        KnowledgeIngestionJob.document_id == document.id,
        KnowledgeIngestionJob.job_type.in_(job_types),
        KnowledgeIngestionJob.status.in_(("queued", "running", "retry_wait")),
    ).limit(1)) is not None


def _initial_rebuild_in_progress(db, document):
    return (
        document.status in {"queued", "processing"}
        and (document.pending_index_version or 0) > 1
        and _has_pending_job(db, document, ("ingest", "reindex"))
    )


def _schedule_reindex(db, document):
    if _has_pending_job(db, document, ("ingest", "reindex")):
        return False
    next_version = next_document_index_version(db, document)
    document.pending_index_version = next_version
    document.status = "ready" if document.active_index_version else "queued"
    document.error_code = None
    document.error_message = None
    _enqueue_job(
        db,
        tenant_id=document.tenant_id,
        knowledge_base_id=document.knowledge_base_id,
        document_id=document.id,
        job_type="reindex" if document.active_index_version else "ingest",
        index_version=next_version,
        dedupe_key=f"reindex:{document.id}:{next_version}",
    )
    return True


def _schedule_delete(db, document):
    if _has_pending_job(db, document, ("delete",)):
        return False
    db.query(KnowledgeIngestionJob).filter(
        KnowledgeIngestionJob.tenant_id == document.tenant_id,
        KnowledgeIngestionJob.knowledge_base_id == document.knowledge_base_id,
        KnowledgeIngestionJob.document_id == document.id,
        KnowledgeIngestionJob.job_type.in_(("ingest", "reindex")),
        KnowledgeIngestionJob.status.in_(("queued", "retry_wait")),
    ).update({"status": "cancelled"}, synchronize_session=False)
    document.status = "deleting"
    document.error_code = None
    document.error_message = None
    _enqueue_job(
        db,
        tenant_id=document.tenant_id,
        knowledge_base_id=document.knowledge_base_id,
        document_id=document.id,
        job_type="delete",
        index_version=document.active_index_version,
        dedupe_key=f"delete:{document.id}:{uuid4().hex}",
    )
    return True


@router.get(
    "/knowledge-bases/{knowledge_base_id}/documents",
    response_model=list[KnowledgeDocumentRead],
)
def list_knowledge_documents(
    knowledge_base_id: UUID,
    principal: CurrentPrincipal,
    db: DatabaseSession,
) -> list[KnowledgeDocumentRead]:
    _get_knowledge_base(db, principal.tenant.id, knowledge_base_id)
    documents = db.scalars(
        select(KnowledgeDocument)
        .where(
            KnowledgeDocument.tenant_id == principal.tenant.id,
            KnowledgeDocument.knowledge_base_id == knowledge_base_id,
            KnowledgeDocument.status != "deleted",
        )
        .order_by(KnowledgeDocument.created_at.desc())
    ).all()
    return [_document_read(item) for item in documents]


@router.post(
    "/knowledge-bases/{knowledge_base_id}/documents/upload",
    response_model=KnowledgeDocumentRead,
    status_code=status.HTTP_202_ACCEPTED,
)
def upload_knowledge_document(
    knowledge_base_id: UUID,
    principal: CurrentPrincipal,
    db: DatabaseSession,
    file: Annotated[UploadFile, File()],
) -> KnowledgeDocumentRead:
    knowledge_base = _get_knowledge_base(db, principal.tenant.id, knowledge_base_id, lock=True)
    settings = get_settings()
    filename = (file.filename or "upload").replace("\\", "/").split("/")[-1].strip()
    if not filename or len(filename) > 255:
        raise HTTPException(
            status_code=422,
            detail={"code": "invalid_filename", "message": "文件名无效或过长"},
        )
    body = file.file.read(settings.knowledge_max_upload_bytes + 1)
    if len(body) > settings.knowledge_max_upload_bytes:
        raise HTTPException(
            status_code=413,
            detail={"code": "file_too_large", "message": "上传文件超过知识库大小限制"},
        )
    try:
        mime_type = validate_document_bytes(filename, file.content_type or "", body)
    except ValueError as exc:
        raise HTTPException(
            status_code=415,
            detail={"code": str(exc), "message": "文件格式不支持、文件损坏或内容与扩展名不匹配"},
        ) from None

    digest = hashlib.sha256(body).hexdigest()
    existing = db.scalar(
        select(KnowledgeDocument).where(
            KnowledgeDocument.tenant_id == principal.tenant.id,
            KnowledgeDocument.knowledge_base_id == knowledge_base.id,
            KnowledgeDocument.sha256 == digest,
            KnowledgeDocument.status != "deleted",
        )
    )
    if existing is not None:
        return _document_read(existing)

    document_id = uuid4()
    suffix = PurePosixPath(filename).suffix.lower()
    object_key = (
        f"tenant/{principal.tenant.id}/knowledge/{knowledge_base.id}/"
        f"{document_id.hex}/{uuid4().hex}{suffix}"
    )
    try:
        put_object(object_key, body, mime_type)
    except Exception:
        raise HTTPException(
            status_code=503,
            detail={"code": "object_storage_unavailable", "message": "知识库对象存储暂时不可用"},
        ) from None

    document = KnowledgeDocument(
        id=document_id,
        tenant_id=principal.tenant.id,
        knowledge_base_id=knowledge_base.id,
        original_filename=filename,
        source_type="upload",
        source_url=None,
        object_key=object_key,
        mime_type=mime_type,
        file_size_bytes=len(body),
        sha256=digest,
        status="queued",
        active_index_version=0,
        pending_index_version=1,
    )
    prepare_legacy_change(db, principal.tenant.id, knowledge_base_id)
    db.add(document)
    try:
        db.flush()
        _enqueue_job(
            db,
            tenant_id=principal.tenant.id,
            knowledge_base_id=knowledge_base.id,
            document_id=document.id,
            job_type="ingest",
            index_version=1,
            dedupe_key=f"ingest:{document.id}:1",
        )
        db.commit()
    except IntegrityError:
        db.rollback()
        try:
            delete_object(object_key)
        except Exception:
            pass
        existing = db.scalar(
            select(KnowledgeDocument).where(
                KnowledgeDocument.tenant_id == principal.tenant.id,
                KnowledgeDocument.knowledge_base_id == knowledge_base.id,
                KnowledgeDocument.sha256 == digest,
            )
        )
        if existing is not None:
            return _document_read(existing)
        raise HTTPException(
            status_code=409,
            detail={"code": "document_upload_conflict", "message": "文件正在被重复导入"},
        ) from None
    db.refresh(document)
    return _document_read(document)


@router.post(
    "/knowledge-bases/{knowledge_base_id}/documents/import-url",
    response_model=KnowledgeDocumentRead,
    status_code=status.HTTP_202_ACCEPTED,
)
def import_knowledge_url(
    knowledge_base_id: UUID,
    payload: KnowledgeImportUrlRequest,
    principal: CurrentPrincipal,
    db: DatabaseSession,
) -> KnowledgeDocumentRead:
    knowledge_base = _get_knowledge_base(db, principal.tenant.id, knowledge_base_id, lock=True)
    existing = db.scalar(
        select(KnowledgeDocument).where(
            KnowledgeDocument.tenant_id == principal.tenant.id,
            KnowledgeDocument.knowledge_base_id == knowledge_base.id,
            KnowledgeDocument.source_url == payload.url,
            KnowledgeDocument.status != "deleted",
        )
    )
    if existing is not None:
        return _document_read(existing)

    document_id = uuid4()
    object_key = (
        f"tenant/{principal.tenant.id}/knowledge/{knowledge_base.id}/"
        f"{document_id.hex}/{uuid4().hex}.html"
    )
    document = KnowledgeDocument(
        id=document_id,
        tenant_id=principal.tenant.id,
        knowledge_base_id=knowledge_base.id,
        original_filename=payload.url[:255],
        source_type="url",
        source_url=payload.url,
        object_key=object_key,
        mime_type="text/html",
        file_size_bytes=0,
        sha256=None,
        status="queued",
        active_index_version=0,
        pending_index_version=1,
    )
    prepare_legacy_change(db, principal.tenant.id, knowledge_base_id)
    db.add(document)
    db.flush()
    _enqueue_job(
        db,
        tenant_id=principal.tenant.id,
        knowledge_base_id=knowledge_base.id,
        document_id=document.id,
        job_type="ingest",
        index_version=1,
        dedupe_key=f"import-url:{document.id}:1",
    )
    db.commit()
    db.refresh(document)
    return _document_read(document)


@router.post(
    "/knowledge-bases/{knowledge_base_id}/documents/{document_id}/retry",
    response_model=KnowledgeDocumentRead,
    status_code=status.HTTP_202_ACCEPTED,
)
def retry_knowledge_document(
    knowledge_base_id: UUID,
    document_id: UUID,
    principal: CurrentPrincipal,
    db: DatabaseSession,
) -> KnowledgeDocumentRead:
    _get_knowledge_base(db, principal.tenant.id, knowledge_base_id, lock=True)
    document = _lock_document(db, principal.tenant.id, knowledge_base_id, document_id)
    if _initial_rebuild_in_progress(db, document):
        return _document_read(document)
    if document.status not in {"failed", "deleting"}:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "document_not_retryable",
                "message": "只有失败或删除失败的文档可以重试",
            },
        )
    changed = (
        _schedule_delete(db, document)
        if document.status == "deleting" else _schedule_reindex(db, document)
    )
    if changed:
        prepare_legacy_change(db, principal.tenant.id, knowledge_base_id)
    db.commit()
    db.refresh(document)
    return _document_read(document)


@router.post(
    "/knowledge-bases/{knowledge_base_id}/documents/{document_id}/reindex",
    response_model=KnowledgeDocumentRead,
    status_code=status.HTTP_202_ACCEPTED,
)
def reindex_knowledge_document(
    knowledge_base_id: UUID,
    document_id: UUID,
    principal: CurrentPrincipal,
    db: DatabaseSession,
) -> KnowledgeDocumentRead:
    _get_knowledge_base(db, principal.tenant.id, knowledge_base_id, lock=True)
    document = _lock_document(db, principal.tenant.id, knowledge_base_id, document_id)
    if _initial_rebuild_in_progress(db, document):
        return _document_read(document)
    if document.status == "deleted":
        raise HTTPException(404, detail={"code": "document_not_found", "message": "文档不存在"})
    if document.status not in {"ready", "failed"}:
        raise HTTPException(
            status_code=409,
            detail={"code": "document_not_ready", "message": "文档当前状态不允许重建索引"},
        )
    if _schedule_reindex(db, document):
        prepare_legacy_change(db, principal.tenant.id, knowledge_base_id)
    db.commit()
    db.refresh(document)
    return _document_read(document)


@router.delete(
    "/knowledge-bases/{knowledge_base_id}/documents/{document_id}",
    status_code=status.HTTP_202_ACCEPTED,
)
def delete_knowledge_document(
    knowledge_base_id: UUID,
    document_id: UUID,
    principal: CurrentPrincipal,
    db: DatabaseSession,
) -> dict:
    _get_knowledge_base(db, principal.tenant.id, knowledge_base_id, lock=True)
    document = _lock_document(db, principal.tenant.id, knowledge_base_id, document_id)
    if document.status == "deleted":
        return {"status": "deleted"}
    if _schedule_delete(db, document):
        prepare_legacy_change(db, principal.tenant.id, knowledge_base_id)
    db.commit()
    return {"status": "deleting", "document_id": str(document.id)}


@router.get(
    "/knowledge-bases/{knowledge_base_id}/usage",
    response_model=KnowledgeUsageSummary,
)
def get_knowledge_usage(
    knowledge_base_id: UUID,
    principal: CurrentPrincipal,
    db: DatabaseSession,
) -> KnowledgeUsageSummary:
    _get_knowledge_base(db, principal.tenant.id, knowledge_base_id)
    tenant_id = principal.tenant.id
    doc, member, state = KnowledgeDocument, KnowledgeIndexMember, KnowledgeIndexState
    # Read the active pointer and both chunk counts in the same statement snapshot.
    counts = db.execute(
        select(
            state.active_release_id,
            func.count(doc.id).label("documents"),
            func.count(doc.id).filter(doc.status == "ready").label("ready"),
            func.count(doc.id)
            .filter(doc.status.in_(("queued", "processing", "deleting")))
            .label("queued"),
            func.count(doc.id).filter(doc.status == "failed").label("failed"),
            func.coalesce(func.sum(doc.file_size_bytes), 0).label("storage"),
            func.coalesce(func.sum(doc.chunk_count).filter(doc.status == "ready"), 0).label(
                "legacy_chunks"
            ),
            func.coalesce(func.sum(member.chunk_count), 0).label("release_chunks"),
        )
        .select_from(KnowledgeBase)
        .outerjoin(
            state,
            and_(
                state.tenant_id == KnowledgeBase.tenant_id,
                state.knowledge_base_id == KnowledgeBase.id,
            ),
        )
        .outerjoin(
            doc,
            and_(
                doc.tenant_id == KnowledgeBase.tenant_id,
                doc.knowledge_base_id == KnowledgeBase.id,
                doc.status != "deleted",
            ),
        )
        .outerjoin(
            member,
            and_(
                member.tenant_id == doc.tenant_id,
                member.knowledge_base_id == doc.knowledge_base_id,
                member.document_id == doc.id,
                member.release_id == state.active_release_id,
                member.status == "ready",
                doc.status == "ready",
                member.source_version == doc.active_index_version,
                member.source_sha256.is_not_distinct_from(doc.sha256),
            ),
        )
        .where(KnowledgeBase.tenant_id == tenant_id, KnowledgeBase.id == knowledge_base_id)
        .group_by(state.active_release_id)
    ).one_or_none()
    if counts is None:
        raise HTTPException(
            404, detail={"code": "knowledge_base_not_found", "message": "知识库不存在"}
        )

    legacy = KnowledgeUsageRecord
    usage = db.execute(
        select(
            func.coalesce(func.sum(legacy.input_tokens), 0).label("tokens"),
            func.coalesce(func.sum(legacy.request_count), 0).label("requests"),
            func.count(legacy.id).filter(legacy.operation == "retrieval").label("retrievals"),
        ).where(
            legacy.tenant_id == tenant_id,
            legacy.knowledge_base_id == knowledge_base_id,
            legacy.operation.in_(("ingest", "reindex", "retrieval")),
        )
    ).one()
    observation = KnowledgeIndexObservation
    steps = db.execute(
        select(
            observation.operation,
            observation.status,
            func.count().label("calls"),
            func.coalesce(func.sum(observation.input_tokens), 0).label("known_input_tokens"),
            func.coalesce(func.sum(observation.output_tokens), 0).label("known_output_tokens"),
            func.count()
            .filter(observation.input_tokens.is_(None), observation.output_tokens.is_(None))
            .label("unknown_calls"),
        )
        .where(
            observation.tenant_id == tenant_id, observation.knowledge_base_id == knowledge_base_id
        )
        .group_by(observation.operation, observation.status)
        .order_by(observation.operation, observation.status)
    ).mappings()
    return KnowledgeUsageSummary(
        document_count=counts.documents,
        ready_documents=counts.ready,
        queued_documents=counts.queued,
        failed_documents=counts.failed,
        storage_bytes=counts.storage,
        active_chunks=counts.release_chunks if counts.active_release_id else counts.legacy_chunks,
        active_release_id=counts.active_release_id,
        legacy_active_chunks=counts.legacy_chunks,
        embedding_tokens=usage.tokens,
        embedding_requests=usage.requests,
        retrieval_requests=usage.retrievals,
        operation_usage=[KnowledgeOperationUsage(**step) for step in steps],
    )


@router.post(
    "/knowledge-bases/{knowledge_base_id}/search",
    response_model=KnowledgeSearchResponse,
)
def search_knowledge_base(
    knowledge_base_id: UUID,
    payload: KnowledgeSearchRequest,
    principal: CurrentPrincipal,
    db: DatabaseSession,
) -> KnowledgeSearchResponse:
    knowledge_base = _get_knowledge_base(db, principal.tenant.id, knowledge_base_id)
    try:
        raw_hits = retrieve_knowledge_base(
            db,
            tenant_id=principal.tenant.id,
            knowledge_base=knowledge_base,
            query=payload.query,
            top_k=payload.top_k,
            score_threshold=payload.score_threshold,
            document_ids=payload.document_ids or None,
        )
    except ValueError as exc:
        message = "知识库 Embedding 配置不可用，请检查模型配置"
        if str(exc) == "index_embedding_profile_changed":
            message = "当前 Embedding 与索引不匹配，请恢复原模型配置或使用新模型重建索引"
        raise HTTPException(
            status_code=409, detail={"code": str(exc), "message": message}
        ) from None
    except Exception:
        raise HTTPException(
            status_code=502,
            detail={"code": "knowledge_retrieval_failed", "message": "知识库检索暂时不可用"},
        ) from None

    hits = [
        KnowledgeSearchHit(
            chunk_id=item["chunk_id"],
            document_id=item["document_id"],
            document_name=item["document_name"],
            source_type=item["source_type"],
            source_url=item["source_url"],
            page_number=item["page_number"],
            sheet_name=item["sheet_name"],
            slide_number=item["slide_number"],
            chunk_index=item["chunk_index"],
            score=item["score"],
            vector_rank=item["vector_rank"],
            lexical_rank=item["lexical_rank"],
            snippet=item["snippet"],
            metadata=item["metadata"],
        )
        for item in raw_hits
    ]
    return KnowledgeSearchResponse(
        query=payload.query,
        knowledge_base_id=knowledge_base.id,
        hits=hits,
    )


@router.post(
    "/knowledge-bases/{knowledge_base_id}/reindex",
    status_code=status.HTTP_202_ACCEPTED,
)
def reindex_knowledge_base(
    knowledge_base_id: UUID,
    principal: CurrentPrincipal,
    db: DatabaseSession,
) -> dict:
    _get_knowledge_base(db, principal.tenant.id, knowledge_base_id, lock=True)
    documents = db.scalars(
        select(KnowledgeDocument).where(
            KnowledgeDocument.tenant_id == principal.tenant.id,
            KnowledgeDocument.knowledge_base_id == knowledge_base_id,
            KnowledgeDocument.status.in_(("ready", "failed")),
        ).order_by(KnowledgeDocument.id).with_for_update().execution_options(populate_existing=True)
    ).all()
    queued = sum(_schedule_reindex(db, document) for document in documents)
    if queued:
        prepare_legacy_change(db, principal.tenant.id, knowledge_base_id)
    db.commit()
    return {"queued_documents": queued, "status": "queued"}


@router.get(
    "/knowledge-bases/{knowledge_base_id}/documents/{document_id}/download",
)
def download_knowledge_document(
    knowledge_base_id: UUID,
    document_id: UUID,
    principal: CurrentPrincipal,
    db: DatabaseSession,
) -> Response:
    _get_knowledge_base(db, principal.tenant.id, knowledge_base_id)
    document = db.scalar(
        select(KnowledgeDocument).where(
            KnowledgeDocument.id == document_id,
            KnowledgeDocument.tenant_id == principal.tenant.id,
            KnowledgeDocument.knowledge_base_id == knowledge_base_id,
        )
    )
    if document is None or document.status == "deleted":
        raise HTTPException(
            status_code=404,
            detail={"code": "document_not_found", "message": "文档不存在"},
        )
    settings = get_settings()
    try:
        body = get_object(
            document.object_key,
            max(settings.knowledge_max_upload_bytes, settings.knowledge_max_url_bytes),
        )
    except Exception:
        raise HTTPException(
            status_code=503,
            detail={"code": "object_storage_unavailable", "message": "原始文档暂时无法下载"},
        ) from None
    filename = quote(document.original_filename, safe="")
    return Response(
        content=body,
        media_type=document.mime_type,
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{filename}"},
    )
