from __future__ import annotations

import hashlib
import mimetypes
from datetime import UTC, datetime, timedelta
from pathlib import PurePosixPath
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, File, Header, HTTPException, UploadFile, status
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError

from app.api.deps import CurrentPrincipal, DatabaseSession
from app.chat_import.parsers import SUPPORTED_CHAT_EXTENSIONS
from app.core.config import get_settings
from app.knowledge.storage import delete_object, put_object
from app.models.chat_import import (
    ChatImportFile,
    ChatImportJob,
    ChatImportSenderSummary,
    ChatSource,
)
from app.schemas.chat_import import (
    ChatIdentityConfirm,
    ChatImportAccepted,
    ChatImportJobRead,
    ChatImportPreview,
    ChatImportSenderPreview,
    ChatSourceCreate,
    ChatSourceRead,
)

router = APIRouter(tags=["文本分析导入"])


def _source_read(source: ChatSource) -> ChatSourceRead:
    return ChatSourceRead(
        id=source.id,
        source_account_id=source.id,
        platform=source.platform,
        name=source.name,
        export_tool=source.export_tool,
        target_sender_ref=source.target_sender_ref,
        target_sender_name=source.target_sender_name,
        identity_confirmed_at=source.identity_confirmed_at,
        status=source.status,
        created_at=source.created_at,
        updated_at=source.updated_at,
    )


def _job_read(job: ChatImportJob) -> ChatImportJobRead:
    return ChatImportJobRead(
        id=job.id,
        source_account_id=job.source_id,
        status=job.status,
        progress=job.progress,
        total_files=job.total_files,
        processed_files=job.processed_files,
        parsed_text_messages=job.parsed_text_messages,
        ignored_non_text_messages=job.ignored_non_text_messages,
        unique_senders=job.unique_senders,
        error_count=job.error_count,
        error_summary=job.error_summary,
        attempts=job.attempts,
        created_at=job.created_at,
        updated_at=job.updated_at,
    )


def _get_source(db, tenant_id: UUID, source_account_id: UUID) -> ChatSource:
    source = db.scalar(
        select(ChatSource).where(
            ChatSource.id == source_account_id,
            ChatSource.tenant_id == tenant_id,
        )
    )
    if source is None or source.status != "active":
        raise HTTPException(
            status_code=404,
            detail={"code": "source_account_not_found", "message": "来源账号不存在"},
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
    # Each row receives a new server-generated ID. Never look up or merge an
    # account by wxid, group ID, nickname, or other imported participant data.
    source = ChatSource(
        id=uuid4(),
        tenant_id=principal.tenant.id,
        platform=payload.platform,
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
            ChatSource.status == "active",
        )
        .order_by(ChatSource.created_at.desc())
    ).all()
    return [_source_read(source) for source in sources]


@router.get(
    "/chat-sources/{source_account_id}/imports",
    response_model=list[ChatImportJobRead],
)
def list_chat_import_jobs(
    source_account_id: UUID,
    principal: CurrentPrincipal,
    db: DatabaseSession,
) -> list[ChatImportJobRead]:
    source = _get_source(db, principal.tenant.id, source_account_id)
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
    "/chat-sources/{source_account_id}/imports",
    response_model=ChatImportAccepted,
    status_code=status.HTTP_202_ACCEPTED,
)
def upload_chat_import(
    source_account_id: UUID,
    principal: CurrentPrincipal,
    db: DatabaseSession,
    files: Annotated[list[UploadFile], File()],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> ChatImportAccepted:
    source = _get_source(db, principal.tenant.id, source_account_id)
    if not files:
        raise HTTPException(
            status_code=422,
            detail={"code": "chat_import_files_required", "message": "至少选择一个文本导出文件"},
        )

    settings = get_settings()
    if len(files) > settings.chat_import_max_files:
        raise HTTPException(
            status_code=413,
            detail={
                "code": "chat_import_file_count_exceeded",
                "message": "单次上传文件数量超过限制",
            },
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
                        "message": "该幂等键已用于另一个来源账号",
                    },
                )
            if existing_job.status in {
                "queued", "processing", "awaiting_identity", "ready_for_analysis"
            }:
                return ChatImportAccepted(
                    job=_job_read(existing_job),
                    accepted_files=existing_job.total_files,
                    skipped_duplicate_files=0,
                )
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "chat_import_idempotency_expired",
                    "message": "该导入任务已结束，请使用新的幂等键重新上传",
                },
            )

    pending: list[dict] = []
    seen_digests: set[str] = set()
    skipped_duplicates = 0
    total_upload_bytes = 0
    now = datetime.now(UTC)

    for upload in files:
        filename = PurePosixPath((upload.filename or "").replace("\\", "/")).name.strip()
        suffix = PurePosixPath(filename).suffix.casefold()
        if not filename or len(filename) > 255 or suffix not in SUPPORTED_CHAT_EXTENSIONS:
            raise HTTPException(
                status_code=415,
                detail={
                    "code": "unsupported_chat_export_type",
                    "message": "仅支持 JSON、CSV、TXT、HTML、XLSX 和 ZIP 文本导出文件",
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
                    "message": "单个导入文件超过大小限制",
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
                detail={
                    "code": "chat_export_signature_mismatch",
                    "message": "文件内容与扩展名不匹配",
                },
            )
        if suffix == ".json":
            try:
                body.decode("utf-8-sig")
            except UnicodeDecodeError:
                raise HTTPException(
                    status_code=415,
                    detail={
                        "code": "chat_text_must_be_utf8",
                        "message": "JSON 文件必须使用 UTF-8 编码",
                    },
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
                ChatImportFile.expires_at > now,
            )
        )
        if existing_file is not None:
            skipped_duplicates += 1
            continue

        pending.append(
            {
                "filename": filename,
                "suffix": suffix,
                "body": body,
                "digest": digest,
                "mime_type": mimetypes.guess_type(filename)[0] or "application/octet-stream",
            }
        )

    if not pending:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "chat_import_files_already_seen",
                "message": "所选文件均已在当前来源账号的有效临时任务中",
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
        max_attempts=3,
    )
    uploaded_keys: list[str] = []
    expires_at = now + timedelta(hours=settings.chat_import_temp_ttl_hours)

    try:
        db.add(job)
        db.flush()
        for item in pending:
            file_id = uuid4()
            object_key = (
                f"tenant/{principal.tenant.id}/source-account/{source.id}/"
                f"chat-import/{job_id.hex}/{file_id.hex}{item['suffix']}"
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
                    parsed_text_message_count=0,
                    ignored_non_text_message_count=0,
                    expires_at=expires_at,
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
            detail={
                "code": "chat_import_storage_unavailable",
                "message": "临时导入文件存储暂时不可用",
            },
        ) from None

    db.refresh(job)
    return ChatImportAccepted(
        job=_job_read(job),
        accepted_files=len(pending),
        skipped_duplicate_files=skipped_duplicates,
    )


@router.post("/chat-imports/{job_id}/cancel", response_model=ChatImportJobRead)
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
    if job.status not in {
        "queued", "processing", "awaiting_identity", "ready_for_analysis"
    }:
        raise HTTPException(
            status_code=409,
            detail={"code": "chat_import_not_cancellable", "message": "当前任务状态不允许取消"},
        )

    job.status = "cancelled"
    job.worker_id = None
    job.locked_at = None
    job.lease_expires_at = None
    db.commit()

    files = db.scalars(
        select(ChatImportFile).where(
            ChatImportFile.tenant_id == principal.tenant.id,
            ChatImportFile.job_id == job.id,
        )
    ).all()
    cleanup_failed = False
    for file_record in files:
        try:
            delete_object(file_record.object_key)
            db.delete(file_record)
        except Exception:
            cleanup_failed = True
    db.execute(
        delete(ChatImportSenderSummary).where(
            ChatImportSenderSummary.tenant_id == principal.tenant.id,
            ChatImportSenderSummary.job_id == job.id,
        )
    )
    db.commit()
    if cleanup_failed:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "chat_import_cleanup_pending",
                "message": "任务已取消，临时文件清理将在后台重试",
            },
        )
    db.refresh(job)
    return _job_read(job)


@router.get("/chat-imports/{job_id}", response_model=ChatImportJobRead)
def get_chat_import_job(
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
    return _job_read(job)


@router.get(
    "/chat-sources/{source_account_id}/preview",
    response_model=ChatImportPreview,
)
def preview_chat_source(
    source_account_id: UUID,
    principal: CurrentPrincipal,
    db: DatabaseSession,
) -> ChatImportPreview:
    source = _get_source(db, principal.tenant.id, source_account_id)
    rows = db.scalars(
        select(ChatImportSenderSummary).where(
            ChatImportSenderSummary.tenant_id == principal.tenant.id,
            ChatImportSenderSummary.source_id == source.id,
        )
    ).all()

    grouped: dict[str, dict] = {}
    for row in rows:
        item = grouped.setdefault(
            row.sender_ref,
            {"sender_ref": row.sender_ref, "sender_name": row.sender_name, "message_count": 0},
        )
        item["message_count"] += row.message_count
        if row.created_at:
            item["sender_name"] = row.sender_name

    senders = [
        ChatImportSenderPreview(**item)
        for item in sorted(
            grouped.values(),
            key=lambda value: value["message_count"],
            reverse=True,
        )
    ]
    return ChatImportPreview(
        source_account_id=source.id,
        identity_confirmed=source.identity_confirmed_at is not None,
        target_sender_ref=source.target_sender_ref,
        senders=senders,
    )


@router.post(
    "/chat-sources/{source_account_id}/confirm-identity",
    response_model=ChatSourceRead,
)
def confirm_chat_identity(
    source_account_id: UUID,
    payload: ChatIdentityConfirm,
    principal: CurrentPrincipal,
    db: DatabaseSession,
) -> ChatSourceRead:
    source = _get_source(db, principal.tenant.id, source_account_id)
    if source.identity_confirmed_at is not None:
        if source.target_sender_ref != payload.target_sender_ref:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "source_account_identity_already_confirmed",
                    "message": "该来源账号已确认本人身份；如需使用另一个微信账号，请新建来源账号",
                },
            )
        return _source_read(source)

    sender = db.scalar(
        select(ChatImportSenderSummary)
        .where(
            ChatImportSenderSummary.tenant_id == principal.tenant.id,
            ChatImportSenderSummary.source_id == source.id,
            ChatImportSenderSummary.sender_ref == payload.target_sender_ref,
        )
        .order_by(ChatImportSenderSummary.message_count.desc())
        .limit(1)
    )
    if sender is None:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "chat_sender_not_found",
                "message": "未在该来源账号的文本导入摘要中找到该发送者",
            },
        )

    source.target_sender_ref = payload.target_sender_ref
    source.target_sender_name = payload.target_sender_name or sender.sender_name
    source.identity_confirmed_at = datetime.now(UTC)

    db.query(ChatImportJob).filter(
        ChatImportJob.tenant_id == principal.tenant.id,
        ChatImportJob.source_id == source.id,
        ChatImportJob.status == "awaiting_identity",
    ).update(
        {"status": "ready_for_analysis", "progress": 100},
        synchronize_session=False,
    )
    # Sender IDs other than the explicitly selected account owner are not
    # retained after confirmation. The owner ID remains scoped to this account.
    db.execute(
        delete(ChatImportSenderSummary).where(
            ChatImportSenderSummary.tenant_id == principal.tenant.id,
            ChatImportSenderSummary.source_id == source.id,
        )
    )
    db.commit()
    db.refresh(source)
    return _source_read(source)


@router.delete("/chat-sources/{source_account_id}", status_code=status.HTTP_202_ACCEPTED)
def delete_chat_source(
    source_account_id: UUID,
    principal: CurrentPrincipal,
    db: DatabaseSession,
) -> dict:
    source = _get_source(db, principal.tenant.id, source_account_id)
    source.status = "deleting"
    db.commit()

    files = db.scalars(
        select(ChatImportFile).where(
            ChatImportFile.tenant_id == principal.tenant.id,
            ChatImportFile.source_id == source.id,
        )
    ).all()
    cleanup_failed = False
    for file_record in files:
        try:
            delete_object(file_record.object_key)
        except Exception:
            cleanup_failed = True

    if cleanup_failed:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "source_account_cleanup_pending",
                "message": "来源账号已停用，临时文件清理暂未完成，请稍后重试",
            },
        )

    db.execute(
        delete(ChatImportSenderSummary).where(
            ChatImportSenderSummary.tenant_id == principal.tenant.id,
            ChatImportSenderSummary.source_id == source.id,
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
    return {"status": "deleted", "source_account_id": str(source_account_id)}
