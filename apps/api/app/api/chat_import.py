from __future__ import annotations

import hashlib
import mimetypes
from datetime import UTC, datetime
from pathlib import PurePosixPath
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, File, Header, HTTPException, Query, UploadFile, status
from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError

from app.api.deps import CurrentPrincipal, DatabaseSession
from app.chat_import.parsers import SUPPORTED_CHAT_EXTENSIONS
from app.core.config import get_settings
from app.knowledge.storage import delete_object, put_object
from app.models.chat_import import (
    ChatConversation,
    ChatImportFile,
    ChatImportJob,
    ChatMessage,
    ChatParticipant,
    ChatSource,
)
from app.schemas.chat_import import (
    ChatConversationPreview,
    ChatIdentityConfirm,
    ChatImportAccepted,
    ChatImportFileRead,
    ChatImportJobDetail,
    ChatImportJobRead,
    ChatImportParticipantPreview,
    ChatImportPreview,
    ChatMessageRead,
    ChatSourceCreate,
    ChatSourceRead,
)

router = APIRouter(tags=["聊天记录导入"])


def _source_read(source: ChatSource) -> ChatSourceRead:
    return ChatSourceRead.model_validate(source, from_attributes=True)


def _job_read(job: ChatImportJob) -> ChatImportJobRead:
    return ChatImportJobRead.model_validate(job, from_attributes=True)


def _get_source(db, tenant_id: UUID, source_id: UUID) -> ChatSource:
    source = db.scalar(
        select(ChatSource).where(
            ChatSource.id == source_id,
            ChatSource.tenant_id == tenant_id,
        )
    )
    if source is None or source.status == "deleted":
        raise HTTPException(
            status_code=404,
            detail={"code": "chat_source_not_found", "message": "聊天记录数据集不存在"},
        )
    return source


@router.post(
    "/chat-sources",
    response_model=ChatSourceRead,
    status_code=status.HTTP_201_CREATED,
)
def create_chat_source(
    payload: ChatSourceCreate,
    principal: CurrentPrincipal,
    db: DatabaseSession,
) -> ChatSourceRead:
    source = ChatSource(
        id=uuid4(),
        tenant_id=principal.tenant.id,
        name=payload.name,
        export_tool=payload.export_tool,
        status="active",
    )
    db.add(source)
    db.commit()
    db.refresh(source)
    return _source_read(source)


@router.get("/chat-sources", response_model=list[ChatSourceRead])
def list_chat_sources(
    principal: CurrentPrincipal,
    db: DatabaseSession,
) -> list[ChatSourceRead]:
    sources = db.scalars(
        select(ChatSource)
        .where(
            ChatSource.tenant_id == principal.tenant.id,
            ChatSource.status != "deleted",
        )
        .order_by(ChatSource.created_at.desc())
    ).all()
    return [_source_read(source) for source in sources]


@router.get(
    "/chat-sources/{source_id}/imports",
    response_model=list[ChatImportJobRead],
)
def list_chat_import_jobs(
    source_id: UUID,
    principal: CurrentPrincipal,
    db: DatabaseSession,
) -> list[ChatImportJobRead]:
    source = _get_source(db, principal.tenant.id, source_id)
    jobs = db.scalars(
        select(ChatImportJob)
        .where(
            ChatImportJob.tenant_id == principal.tenant.id,
            ChatImportJob.source_id == source.id,
        )
        .order_by(ChatImportJob.created_at.desc())
    ).all()
    return [_job_read(job) for job in jobs]


@router.post(
    "/chat-sources/{source_id}/imports",
    response_model=ChatImportAccepted,
    status_code=status.HTTP_202_ACCEPTED,
)
def upload_chat_import(
    source_id: UUID,
    principal: CurrentPrincipal,
    db: DatabaseSession,
    files: Annotated[list[UploadFile], File()],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> ChatImportAccepted:
    source = _get_source(db, principal.tenant.id, source_id)
    if source.status != "active":
        raise HTTPException(
            status_code=409,
            detail={"code": "chat_source_not_active", "message": "该聊天记录数据集当前不可导入"},
        )
    if not files:
        raise HTTPException(
            status_code=422,
            detail={"code": "chat_import_files_required", "message": "至少选择一个聊天记录文件"},
        )
    settings = get_settings()
    if len(files) > settings.chat_import_max_files:
        raise HTTPException(
            status_code=413,
            detail={"code": "chat_import_file_count_exceeded", "message": "单次上传文件数量超过限制"},
        )
    if idempotency_key is not None:
        idempotency_key = idempotency_key.strip()
        if not idempotency_key or len(idempotency_key) > 128:
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "invalid_idempotency_key",
                    "message": "Idempotency-Key 长度必须为 1 到 128",
                },
            )
        existing_job = db.scalar(
            select(ChatImportJob).where(
                ChatImportJob.tenant_id == principal.tenant.id,
                ChatImportJob.idempotency_key == idempotency_key,
            )
        )
        if existing_job is not None:
            if existing_job.source_id != source.id:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "code": "idempotency_key_reused",
                        "message": "该幂等键已用于另一个聊天记录数据集",
                    },
                )
            return ChatImportAccepted(
                job=_job_read(existing_job),
                accepted_files=existing_job.total_files,
                skipped_duplicate_files=0,
            )

    pending: list[dict] = []
    seen_digests: set[str] = set()
    skipped_duplicates = 0
    total_upload_bytes = 0
    for upload in files:
        filename = PurePosixPath((upload.filename or "").replace("\\", "/")).name.strip()
        suffix = PurePosixPath(filename).suffix.casefold()
        if not filename or len(filename) > 255 or suffix not in SUPPORTED_CHAT_EXTENSIONS:
            raise HTTPException(
                status_code=415,
                detail={
                    "code": "unsupported_chat_export_type",
                    "message": "仅支持 JSON、CSV、TXT、HTML、XLSX 和 ZIP 聊天导出文件",
                },
            )
        body = upload.file.read(settings.chat_import_max_file_bytes + 1)
        if not body:
            raise HTTPException(
                status_code=422,
                detail={"code": "empty_chat_export", "message": "不能导入空文件"},
            )
        if len(body) > settings.chat_import_max_file_bytes:
            raise HTTPException(
                status_code=413,
                detail={
                    "code": "chat_import_file_too_large",
                    "message": "单个聊天记录文件超过大小限制",
                },
            )
        total_upload_bytes += len(body)
        if total_upload_bytes > settings.chat_import_max_batch_bytes:
            raise HTTPException(
                status_code=413,
                detail={
                    "code": "chat_import_batch_too_large",
                    "message": "本次批量上传的文件总大小超过限制",
                },
            )
        if suffix in {".zip", ".xlsx"} and not body.startswith(b"PK"):
            raise HTTPException(
                status_code=415,
                detail={"code": "chat_export_signature_mismatch", "message": "文件内容与扩展名不匹配"},
            )
        if suffix == ".json":
            try:
                body.decode("utf-8-sig")
            except UnicodeDecodeError:
                raise HTTPException(
                    status_code=415,
                    detail={"code": "chat_text_must_be_utf8", "message": "JSON 文件必须使用 UTF-8 编码"},
                ) from None
        digest = hashlib.sha256(body).hexdigest()
        if digest in seen_digests:
            skipped_duplicates += 1
            continue
        seen_digests.add(digest)
        existing_file = db.scalar(
            select(ChatImportFile.id).where(
                ChatImportFile.tenant_id == principal.tenant.id,
                ChatImportFile.source_id == source.id,
                ChatImportFile.sha256 == digest,
                ChatImportFile.status != "deleted",
            )
        )
        if existing_file is not None:
            skipped_duplicates += 1
            continue
        mime_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
        pending.append(
            {
                "filename": filename,
                "suffix": suffix,
                "body": body,
                "digest": digest,
                "mime_type": mime_type,
            }
        )

    if not pending:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "chat_import_files_already_seen",
                "message": "所选文件均已导入或在本次上传中重复",
            },
        )

    job_id = uuid4()
    job = ChatImportJob(
        id=job_id,
        tenant_id=principal.tenant.id,
        source_id=source.id,
        idempotency_key=idempotency_key,
        status="queued",
        progress=0,
        total_files=len(pending),
        max_attempts=5,
    )
    db.add(job)
    uploaded_keys: list[str] = []
    try:
        for item in pending:
            file_id = uuid4()
            object_key = (
                f"tenant/{principal.tenant.id}/chat-import/{source.id}/"
                f"{job_id.hex}/{file_id.hex}{item['suffix']}"
            )
            put_object(object_key, item["body"], item["mime_type"])
            uploaded_keys.append(object_key)
            db.add(
                ChatImportFile(
                    id=file_id,
                    tenant_id=principal.tenant.id,
                    source_id=source.id,
                    job_id=job_id,
                    original_filename=item["filename"],
                    extension=item["suffix"],
                    mime_type=item["mime_type"],
                    file_size_bytes=len(item["body"]),
                    sha256=item["digest"],
                    object_key=object_key,
                    status="uploaded",
                    parsed_message_count=0,
                    metadata_json={"source_filename": item["filename"]},
                )
            )
        db.commit()
    except IntegrityError:
        db.rollback()
        for object_key in uploaded_keys:
            try:
                delete_object(object_key)
            except Exception:
                pass
        raise HTTPException(
            status_code=409,
            detail={"code": "chat_import_conflict", "message": "文件正在被重复导入，请刷新后重试"},
        ) from None
    except Exception:
        db.rollback()
        for object_key in uploaded_keys:
            try:
                delete_object(object_key)
            except Exception:
                pass
        raise HTTPException(
            status_code=503,
            detail={"code": "chat_import_storage_unavailable", "message": "聊天记录文件存储暂时不可用"},
        ) from None
    db.refresh(job)
    return ChatImportAccepted(
        job=_job_read(job),
        accepted_files=len(pending),
        skipped_duplicate_files=skipped_duplicates,
    )


@router.post(
    "/chat-imports/{job_id}/retry",
    response_model=ChatImportJobRead,
    status_code=status.HTTP_202_ACCEPTED,
)
def retry_chat_import_job(
    job_id: UUID,
    principal: CurrentPrincipal,
    db: DatabaseSession,
) -> ChatImportJobRead:
    job = db.scalar(
        select(ChatImportJob).where(
            ChatImportJob.id == job_id,
            ChatImportJob.tenant_id == principal.tenant.id,
        )
    )
    if job is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "chat_import_job_not_found", "message": "导入任务不存在"},
        )
    if job.status not in {"failed", "cancelled"}:
        raise HTTPException(
            status_code=409,
            detail={"code": "chat_import_not_retryable", "message": "只有失败或已取消的任务可以重试"},
        )
    all_files = db.scalars(
        select(ChatImportFile).where(
            ChatImportFile.tenant_id == principal.tenant.id,
            ChatImportFile.job_id == job.id,
        )
    ).all()
    retryable = [
        item for item in all_files
        if item.status in {"failed", "processing", "uploaded"}
    ]
    if not retryable:
        raise HTTPException(
            status_code=409,
            detail={"code": "chat_import_no_retryable_files", "message": "没有可重试的文件"},
        )
    for item in retryable:
        item.status = "uploaded"
        item.error_code = None
        item.error_message = None
    job.status = "queued"
    job.progress = 0
    job.total_files = len(all_files)
    job.processed_files = sum(item.status == "parsed" for item in all_files)
    job.error_count = 0
    job.error_summary = None
    job.attempts = 0
    job.worker_id = None
    job.locked_at = None
    job.lease_expires_at = None
    db.commit()
    db.refresh(job)
    return _job_read(job)


@router.post(
    "/chat-imports/{job_id}/cancel",
    response_model=ChatImportJobRead,
)
def cancel_chat_import_job(
    job_id: UUID,
    principal: CurrentPrincipal,
    db: DatabaseSession,
) -> ChatImportJobRead:
    job = db.scalar(
        select(ChatImportJob).where(
            ChatImportJob.id == job_id,
            ChatImportJob.tenant_id == principal.tenant.id,
        )
    )
    if job is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "chat_import_job_not_found", "message": "导入任务不存在"},
        )
    if job.status not in {"queued", "processing"}:
        raise HTTPException(
            status_code=409,
            detail={"code": "chat_import_not_cancellable", "message": "当前任务状态不允许取消"},
        )
    job.status = "cancelled"
    job.worker_id = None
    job.locked_at = None
    job.lease_expires_at = None
    db.query(ChatImportFile).filter(
        ChatImportFile.tenant_id == principal.tenant.id,
        ChatImportFile.job_id == job.id,
        ChatImportFile.status == "processing",
    ).update({"status": "uploaded"}, synchronize_session=False)
    db.commit()
    db.refresh(job)
    return _job_read(job)


@router.get("/chat-imports/{job_id}", response_model=ChatImportJobDetail)
def get_chat_import_job(
    job_id: UUID,
    principal: CurrentPrincipal,
    db: DatabaseSession,
) -> ChatImportJobDetail:
    job = db.scalar(
        select(ChatImportJob).where(
            ChatImportJob.id == job_id,
            ChatImportJob.tenant_id == principal.tenant.id,
        )
    )
    if job is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "chat_import_job_not_found", "message": "导入任务不存在"},
        )
    files = db.scalars(
        select(ChatImportFile)
        .where(
            ChatImportFile.tenant_id == principal.tenant.id,
            ChatImportFile.job_id == job.id,
        )
        .order_by(ChatImportFile.created_at)
    ).all()
    return ChatImportJobDetail(
        **_job_read(job).model_dump(),
        files=[
            ChatImportFileRead.model_validate(item, from_attributes=True)
            for item in files
        ],
    )


@router.get(
    "/chat-sources/{source_id}/messages",
    response_model=list[ChatMessageRead],
)
def list_chat_messages(
    source_id: UUID,
    principal: CurrentPrincipal,
    db: DatabaseSession,
    conversation_id: UUID | None = None,
    sender_ref: str | None = None,
    start_at: datetime | None = None,
    end_at: datetime | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
    offset: Annotated[int, Query(ge=0, le=10000)] = 0,
) -> list[ChatMessageRead]:
    source = _get_source(db, principal.tenant.id, source_id)
    if start_at is not None and end_at is not None and start_at > end_at:
        raise HTTPException(
            status_code=422,
            detail={"code": "invalid_time_range", "message": "开始时间不能晚于结束时间"},
        )
    statement = select(ChatMessage).where(
        ChatMessage.tenant_id == principal.tenant.id,
        ChatMessage.source_id == source.id,
    )
    if conversation_id is not None:
        statement = statement.where(ChatMessage.conversation_id == conversation_id)
    if sender_ref is not None:
        statement = statement.where(ChatMessage.sender_ref == sender_ref)
    if start_at is not None:
        statement = statement.where(ChatMessage.timestamp >= start_at)
    if end_at is not None:
        statement = statement.where(ChatMessage.timestamp <= end_at)
    messages = db.scalars(
        statement.order_by(ChatMessage.timestamp, ChatMessage.id)
        .limit(limit)
        .offset(offset)
    ).all()
    return [
        ChatMessageRead(
            id=item.id,
            conversation_id=item.conversation_id,
            sender_ref=item.sender_ref,
            sender_name=item.sender_name,
            receiver_ref=item.receiver_ref,
            receiver_name=item.receiver_name,
            content=item.content,
            message_type=item.message_type,
            timestamp=item.timestamp,
            identity_role=item.identity_role,
            metadata=item.metadata_json,
        )
        for item in messages
    ]


@router.get(
    "/chat-sources/{source_id}/preview",
    response_model=ChatImportPreview,
)
def preview_chat_source(
    source_id: UUID,
    principal: CurrentPrincipal,
    db: DatabaseSession,
) -> ChatImportPreview:
    source = _get_source(db, principal.tenant.id, source_id)
    participant_rows = db.scalars(
        select(ChatParticipant).where(
            ChatParticipant.tenant_id == principal.tenant.id,
            ChatParticipant.source_id == source.id,
        )
    ).all()
    grouped: dict[str, dict] = {}
    for participant in participant_rows:
        item = grouped.setdefault(
            participant.sender_ref,
            {
                "sender_ref": participant.sender_ref,
                "sender_name": participant.sender_name,
                "message_count": 0,
                "conversations": set(),
                "identity_role": participant.identity_role,
            },
        )
        item["message_count"] += participant.message_count
        item["conversations"].add(participant.conversation_id)
        if item["sender_name"] != participant.sender_name:
            item["sender_name"] = participant.sender_name

    participants = [
        ChatImportParticipantPreview(
            sender_ref=item["sender_ref"],
            sender_name=item["sender_name"],
            message_count=item["message_count"],
            conversation_count=len(item["conversations"]),
            identity_role=item["identity_role"],
        )
        for item in sorted(
            grouped.values(), key=lambda value: value["message_count"], reverse=True
        )
    ]
    conversations = db.scalars(
        select(ChatConversation)
        .where(
            ChatConversation.tenant_id == principal.tenant.id,
            ChatConversation.source_id == source.id,
        )
        .order_by(ChatConversation.last_message_at.desc())
    ).all()
    return ChatImportPreview(
        source_id=source.id,
        identity_confirmed=source.identity_confirmed_at is not None,
        target_sender_ref=source.target_sender_ref,
        participants=participants,
        conversations=[
            ChatConversationPreview.model_validate(item, from_attributes=True)
            for item in conversations
        ],
    )


@router.post(
    "/chat-sources/{source_id}/confirm-identity",
    response_model=ChatSourceRead,
)
def confirm_chat_identity(
    source_id: UUID,
    payload: ChatIdentityConfirm,
    principal: CurrentPrincipal,
    db: DatabaseSession,
) -> ChatSourceRead:
    source = _get_source(db, principal.tenant.id, source_id)
    participant = db.scalar(
        select(ChatParticipant).where(
            ChatParticipant.tenant_id == principal.tenant.id,
            ChatParticipant.source_id == source.id,
            ChatParticipant.sender_ref == payload.target_sender_ref,
        )
    )
    if participant is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "chat_sender_not_found", "message": "未在已导入的聊天记录中找到该发送者"},
        )
    source.target_sender_ref = payload.target_sender_ref
    source.target_sender_name = payload.target_sender_name or participant.sender_name
    source.identity_confirmed_at = datetime.now(UTC)
    db.execute(
        update(ChatParticipant)
        .where(
            ChatParticipant.tenant_id == principal.tenant.id,
            ChatParticipant.source_id == source.id,
        )
        .values(
            identity_role="target"
        )
    )
    db.execute(
        update(ChatParticipant)
        .where(
            ChatParticipant.tenant_id == principal.tenant.id,
            ChatParticipant.source_id == source.id,
            ChatParticipant.sender_ref != payload.target_sender_ref,
        )
        .values(identity_role="other")
    )
    db.execute(
        update(ChatMessage)
        .where(
            ChatMessage.tenant_id == principal.tenant.id,
            ChatMessage.source_id == source.id,
        )
        .values(identity_role="other")
    )
    db.execute(
        update(ChatMessage)
        .where(
            ChatMessage.tenant_id == principal.tenant.id,
            ChatMessage.source_id == source.id,
            ChatMessage.sender_ref == payload.target_sender_ref,
        )
        .values(identity_role="target")
    )
    db.execute(
        update(ChatImportJob)
        .where(
            ChatImportJob.tenant_id == principal.tenant.id,
            ChatImportJob.source_id == source.id,
            ChatImportJob.status == "awaiting_identity",
        )
        .values(status="succeeded", progress=100)
    )
    db.commit()
    db.refresh(source)
    return _source_read(source)


@router.delete("/chat-sources/{source_id}", status_code=status.HTTP_202_ACCEPTED)
def delete_chat_source(
    source_id: UUID,
    principal: CurrentPrincipal,
    db: DatabaseSession,
) -> dict:
    source = _get_source(db, principal.tenant.id, source_id)
    source.status = "deleting"
    db.commit()
    object_keys = db.scalars(
        select(ChatImportFile.object_key).where(
            ChatImportFile.tenant_id == principal.tenant.id,
            ChatImportFile.source_id == source.id,
        )
    ).all()
    try:
        for object_key in object_keys:
            delete_object(object_key)
    except Exception:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "chat_source_storage_cleanup_pending",
                "message": "原始文件清理暂时未完成，请稍后重试删除",
            },
        ) from None

    db.execute(
        delete(ChatMessage).where(
            ChatMessage.tenant_id == principal.tenant.id,
            ChatMessage.source_id == source.id,
        )
    )
    db.execute(
        delete(ChatParticipant).where(
            ChatParticipant.tenant_id == principal.tenant.id,
            ChatParticipant.source_id == source.id,
        )
    )
    db.execute(
        delete(ChatConversation).where(
            ChatConversation.tenant_id == principal.tenant.id,
            ChatConversation.source_id == source.id,
        )
    )
    db.execute(
        delete(ChatImportFile).where(
            ChatImportFile.tenant_id == principal.tenant.id,
            ChatImportFile.source_id == source.id,
        )
    )
    db.execute(
        delete(ChatImportJob).where(
            ChatImportJob.tenant_id == principal.tenant.id,
            ChatImportJob.source_id == source.id,
        )
    )
    db.delete(source)
    db.commit()
    return {"status": "deleted", "source_id": str(source_id)}
