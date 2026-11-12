from __future__ import annotations

import hashlib
from pathlib import PurePosixPath
from typing import Annotated
from urllib.parse import quote
from uuid import UUID, uuid4

from fastapi import APIRouter, File, HTTPException, UploadFile, status
from fastapi.responses import Response
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.api.deps import CurrentPrincipal, DatabaseSession
from app.core.config import get_settings
from app.knowledge.parsers import validate_document_bytes
from app.knowledge.retrieval import retrieve_knowledge_base
from app.knowledge.storage import delete_object, get_object, put_object
from app.models.catalog import KnowledgeBase
from app.models.knowledge import (
    KnowledgeDocument,
    KnowledgeIngestionJob,
    KnowledgeUsageRecord,
)
from app.schemas.knowledge import (
    KnowledgeDocumentRead,
    KnowledgeImportUrlRequest,
    KnowledgeSearchHit,
    KnowledgeSearchRequest,
    KnowledgeSearchResponse,
    KnowledgeUsageSummary,
)

router = APIRouter(tags=["知识库文档"])


def _get_knowledge_base(db, tenant_id: UUID, knowledge_base_id: UUID) -> KnowledgeBase:
    record = db.scalar(
        select(KnowledgeBase).where(
            KnowledgeBase.id == knowledge_base_id,
            KnowledgeBase.tenant_id == tenant_id,
        )
    )
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
    knowledge_base = _get_knowledge_base(db, principal.tenant.id, knowledge_base_id)
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
    db.add(document)
    _enqueue_job(
        db,
        tenant_id=principal.tenant.id,
        knowledge_base_id=knowledge_base.id,
        document_id=document.id,
        job_type="ingest",
        index_version=1,
        dedupe_key=f"ingest:{document.id}:1",
    )
    try:
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
    knowledge_base = _get_knowledge_base(db, principal.tenant.id, knowledge_base_id)
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
    db.add(document)
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
    _get_knowledge_base(db, principal.tenant.id, knowledge_base_id)
    document = db.scalar(
        select(KnowledgeDocument).where(
            KnowledgeDocument.id == document_id,
            KnowledgeDocument.tenant_id == principal.tenant.id,
            KnowledgeDocument.knowledge_base_id == knowledge_base_id,
        )
    )
    if document is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "document_not_found", "message": "文档不存在"},
        )
    if document.status not in {"failed", "deleting"}:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "document_not_retryable",
                "message": "只有失败或删除失败的文档可以重试",
            },
        )
    retry_delete = document.status == "deleting"
    document.status = "deleting" if retry_delete else "queued"
    document.error_code = None
    document.error_message = None
    document.pending_index_version = max(1, document.active_index_version + 1)
    _enqueue_job(
        db,
        tenant_id=principal.tenant.id,
        knowledge_base_id=knowledge_base_id,
        document_id=document.id,
        job_type="delete" if retry_delete else "ingest",
        index_version=(
            document.active_index_version
            if retry_delete
            else document.pending_index_version
        ),
        dedupe_key=f"retry:{document.id}:{uuid4().hex}",
    )
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
    if document.status not in {"ready", "failed"}:
        raise HTTPException(
            status_code=409,
            detail={"code": "document_not_ready", "message": "文档当前状态不允许重建索引"},
        )
    document.status = "ready" if document.active_index_version else "queued"
    document.error_code = None
    document.error_message = None
    document.pending_index_version = max(1, document.active_index_version + 1)
    _enqueue_job(
        db,
        tenant_id=principal.tenant.id,
        knowledge_base_id=knowledge_base_id,
        document_id=document.id,
        job_type="reindex",
        index_version=document.pending_index_version,
        dedupe_key=f"reindex:{document.id}:{document.pending_index_version}:{uuid4().hex}",
    )
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
    _get_knowledge_base(db, principal.tenant.id, knowledge_base_id)
    document = db.scalar(
        select(KnowledgeDocument).where(
            KnowledgeDocument.id == document_id,
            KnowledgeDocument.tenant_id == principal.tenant.id,
            KnowledgeDocument.knowledge_base_id == knowledge_base_id,
        )
    )
    if document is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "document_not_found", "message": "文档不存在"},
        )
    if document.status == "deleted":
        return {"status": "deleted"}
    db.query(KnowledgeIngestionJob).filter(
        KnowledgeIngestionJob.tenant_id == principal.tenant.id,
        KnowledgeIngestionJob.document_id == document.id,
        KnowledgeIngestionJob.status.in_(("queued", "retry_wait")),
    ).update({"status": "cancelled"}, synchronize_session=False)
    document.status = "deleting"
    _enqueue_job(
        db,
        tenant_id=principal.tenant.id,
        knowledge_base_id=knowledge_base_id,
        document_id=document.id,
        job_type="delete",
        index_version=document.active_index_version,
        dedupe_key=f"delete:{document.id}:{uuid4().hex}",
    )
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
    documents = db.scalars(
        select(KnowledgeDocument).where(
            KnowledgeDocument.tenant_id == principal.tenant.id,
            KnowledgeDocument.knowledge_base_id == knowledge_base_id,
            KnowledgeDocument.status != "deleted",
        )
    ).all()
    usage = db.execute(
        select(
            func.coalesce(func.sum(KnowledgeUsageRecord.input_tokens), 0),
            func.coalesce(func.sum(KnowledgeUsageRecord.request_count), 0),
        ).where(
            KnowledgeUsageRecord.tenant_id == principal.tenant.id,
            KnowledgeUsageRecord.knowledge_base_id == knowledge_base_id,
        )
    ).one()
    retrieval_requests = int(
        db.scalar(
            select(func.count(KnowledgeUsageRecord.id)).where(
                KnowledgeUsageRecord.tenant_id == principal.tenant.id,
                KnowledgeUsageRecord.knowledge_base_id == knowledge_base_id,
                KnowledgeUsageRecord.operation == "retrieval",
            )
        )
        or 0
    )
    return KnowledgeUsageSummary(
        document_count=len(documents),
        ready_documents=sum(item.status == "ready" for item in documents),
        queued_documents=sum(
            item.status in {"queued", "processing", "deleting"} for item in documents
        ),
        failed_documents=sum(item.status == "failed" for item in documents),
        storage_bytes=sum(item.file_size_bytes for item in documents),
        active_chunks=sum(item.chunk_count for item in documents if item.status == "ready"),
        embedding_tokens=int(usage[0] or 0),
        embedding_requests=int(usage[1] or 0),
        retrieval_requests=retrieval_requests,
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
        raise HTTPException(
            status_code=409,
            detail={"code": str(exc), "message": "知识库 Embedding 配置不可用，请检查模型配置"},
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
    _get_knowledge_base(db, principal.tenant.id, knowledge_base_id)
    documents = db.scalars(
        select(KnowledgeDocument).where(
            KnowledgeDocument.tenant_id == principal.tenant.id,
            KnowledgeDocument.knowledge_base_id == knowledge_base_id,
            KnowledgeDocument.status.in_(("ready", "failed")),
        )
    ).all()
    queued = 0
    for document in documents:
        next_version = max(1, document.active_index_version + 1)
        job_type = "reindex" if document.active_index_version > 0 else "ingest"
        document.pending_index_version = next_version
        document.status = "ready" if document.active_index_version > 0 else "queued"
        document.error_code = None
        document.error_message = None
        _enqueue_job(
            db,
            tenant_id=principal.tenant.id,
            knowledge_base_id=knowledge_base_id,
            document_id=document.id,
            job_type=job_type,
            index_version=next_version,
            dedupe_key=f"reindex-all:{document.id}:{next_version}:{uuid4().hex}",
        )
        queued += 1
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
