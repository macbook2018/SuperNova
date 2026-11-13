from __future__ import annotations

import argparse
import socket
import time
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import structlog
from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from app.chat_import.parsers import NormalizedChatMessage, parse_chat_export
from app.core.config import get_settings
from app.db.session import SessionLocal, engine
from app.knowledge.storage import get_object
from app.models.chat_import import (
    ChatConversation,
    ChatImportFile,
    ChatImportJob,
    ChatMessage,
    ChatParticipant,
    ChatSource,
)

logger = structlog.get_logger()


def _message_fingerprint(record: NormalizedChatMessage) -> str:
    if record.external_message_id:
        material = "\x1f".join(
            (
                record.conversation_external_id,
                "external-id",
                record.external_message_id,
            )
        )
    else:
        material = "\x1f".join(
            (
                record.conversation_external_id,
                record.sender_ref,
                record.timestamp.isoformat(),
                record.message_type,
                record.content,
            )
        )
    import hashlib

    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _persist_batch(
    *,
    tenant_id: UUID,
    source_id: UUID,
    import_file_id: UUID,
    records: list[NormalizedChatMessage],
) -> tuple[int, int]:
    if not records:
        return 0, 0
    imported = 0
    duplicates = 0
    with SessionLocal() as db:
        source = db.scalar(
            select(ChatSource)
            .where(
                ChatSource.id == source_id,
                ChatSource.tenant_id == tenant_id,
                ChatSource.status == "active",
            )
            .with_for_update()
        )
        if source is None:
            raise ValueError("chat_source_not_available")

        fingerprints = {_message_fingerprint(record) for record in records}
        existing = set(
            db.scalars(
                select(ChatMessage.fingerprint).where(
                    ChatMessage.tenant_id == tenant_id,
                    ChatMessage.source_id == source_id,
                    ChatMessage.fingerprint.in_(fingerprints),
                )
            ).all()
        )
        conversation_cache: dict[str, ChatConversation] = {}
        participant_cache: dict[tuple[UUID, str], ChatParticipant] = {}

        for record in records:
            fingerprint = _message_fingerprint(record)
            if fingerprint in existing:
                duplicates += 1
                continue
            existing.add(fingerprint)

            conversation = conversation_cache.get(record.conversation_external_id)
            if conversation is None:
                conversation = db.scalar(
                    select(ChatConversation).where(
                        ChatConversation.tenant_id == tenant_id,
                        ChatConversation.source_id == source_id,
                        ChatConversation.external_conversation_id
                        == record.conversation_external_id,
                    )
                )
                if conversation is None:
                    conversation = ChatConversation(
                        id=uuid4(),
                        tenant_id=tenant_id,
                        source_id=source_id,
                        external_conversation_id=record.conversation_external_id,
                        title=record.conversation_title,
                        conversation_type=record.conversation_type,
                        first_message_at=record.timestamp,
                        last_message_at=record.timestamp,
                        message_count=0,
                    )
                    db.add(conversation)
                    db.flush()
                conversation_cache[record.conversation_external_id] = conversation
            if record.conversation_title and not conversation.title:
                conversation.title = record.conversation_title
            if conversation.conversation_type == "unknown" and record.conversation_type != "unknown":
                conversation.conversation_type = record.conversation_type
            if conversation.first_message_at is None or record.timestamp < conversation.first_message_at:
                conversation.first_message_at = record.timestamp
            if conversation.last_message_at is None or record.timestamp > conversation.last_message_at:
                conversation.last_message_at = record.timestamp

            participant_key = (conversation.id, record.sender_ref)
            participant = participant_cache.get(participant_key)
            if participant is None:
                participant = db.scalar(
                    select(ChatParticipant).where(
                        ChatParticipant.tenant_id == tenant_id,
                        ChatParticipant.source_id == source_id,
                        ChatParticipant.conversation_id == conversation.id,
                        ChatParticipant.sender_ref == record.sender_ref,
                    )
                )
                if participant is None:
                    role = (
                        "unknown"
                        if source.identity_confirmed_at is None
                        else (
                            "target"
                            if record.sender_ref == source.target_sender_ref
                            else "other"
                        )
                    )
                    participant = ChatParticipant(
                        id=uuid4(),
                        tenant_id=tenant_id,
                        source_id=source_id,
                        conversation_id=conversation.id,
                        sender_ref=record.sender_ref,
                        sender_name=record.sender_name,
                        identity_role=role,
                        message_count=0,
                    )
                    db.add(participant)
                    db.flush()
                participant_cache[participant_key] = participant
            participant.sender_name = record.sender_name
            participant.message_count += 1
            conversation.message_count += 1
            identity_role = (
                "unknown"
                if source.identity_confirmed_at is None
                else (
                    "target"
                    if record.sender_ref == source.target_sender_ref
                    else "other"
                )
            )
            db.add(
                ChatMessage(
                    id=uuid4(),
                    tenant_id=tenant_id,
                    source_id=source_id,
                    conversation_id=conversation.id,
                    import_file_id=import_file_id,
                    external_message_id=record.external_message_id,
                    fingerprint=fingerprint,
                    sender_ref=record.sender_ref,
                    sender_name=record.sender_name,
                    receiver_ref=record.receiver_ref,
                    receiver_name=record.receiver_name,
                    content=record.content,
                    message_type=record.message_type,
                    timestamp=record.timestamp,
                    identity_role=identity_role,
                    metadata_json={
                        **record.metadata,
                        "source_file": record.source_file,
                    },
                )
            )
            imported += 1
        db.commit()
    return imported, duplicates


def _update_job(
    job_id: UUID,
    *,
    parsed_delta: int = 0,
    imported_delta: int = 0,
    duplicate_delta: int = 0,
    processed_files_delta: int = 0,
    error_delta: int = 0,
    progress: int | None = None,
    extend_lease: bool = True,
) -> None:
    settings = get_settings()
    with SessionLocal() as db:
        job = db.get(ChatImportJob, job_id)
        if job is None:
            return
        job.parsed_messages += parsed_delta
        job.imported_messages += imported_delta
        job.duplicate_messages += duplicate_delta
        job.processed_files += processed_files_delta
        job.error_count += error_delta
        if progress is not None:
            job.progress = max(job.progress, min(99, progress))
        if extend_lease and job.status == "processing":
            job.lease_expires_at = datetime.now(UTC) + timedelta(
                seconds=settings.chat_import_worker_lease_seconds
            )
        db.commit()


def process_chat_import_job(job_id: UUID) -> None:
    with SessionLocal() as db:
        job = db.get(ChatImportJob, job_id)
        if job is None or job.status != "processing":
            return
        source = db.scalar(
            select(ChatSource).where(
                ChatSource.id == job.source_id,
                ChatSource.tenant_id == job.tenant_id,
            )
        )
        if source is None or source.status != "active":
            job.status = "failed"
            job.error_summary = "聊天记录数据集不存在或已删除"
            job.progress = 100
            db.commit()
            return
        files = db.scalars(
            select(ChatImportFile)
            .where(
                ChatImportFile.tenant_id == job.tenant_id,
                ChatImportFile.source_id == job.source_id,
                ChatImportFile.job_id == job.id,
                ChatImportFile.status.in_(("uploaded", "failed")),
            )
            .order_by(ChatImportFile.created_at)
        ).all()
        job.total_files = len(files)
        job.status = "processing"
        db.commit()
        tenant_id = job.tenant_id
        source_id = job.source_id

    if not files:
        with SessionLocal() as db:
            job = db.get(ChatImportJob, job_id)
            if job is not None:
                job.status = "failed"
                job.error_summary = "没有可处理的导入文件"
                job.progress = 100
                db.commit()
        return

    settings = get_settings()
    completed = 0
    failed_files = 0
    for file_record in files:
        file_id = file_record.id
        object_key = file_record.object_key
        filename = file_record.original_filename
        file_size = file_record.file_size_bytes
        with SessionLocal() as db:
            current_file = db.get(ChatImportFile, file_id)
            if current_file is None:
                failed_files += 1
                continue
            current_file.status = "processing"
            current_file.error_code = None
            current_file.error_message = None
            db.commit()

        file_parsed = 0
        file_failed = False
        try:
            body = get_object(object_key, max_bytes=settings.chat_import_max_file_bytes)
            batch: list[NormalizedChatMessage] = []
            for record in parse_chat_export(filename, body):
                batch.append(record)
                if len(batch) >= settings.chat_import_batch_size:
                    imported, duplicates = _persist_batch(
                        tenant_id=tenant_id,
                        source_id=source_id,
                        import_file_id=file_id,
                        records=batch,
                    )
                    file_parsed += len(batch)
                    _update_job(
                        job_id,
                        parsed_delta=len(batch),
                        imported_delta=imported,
                        duplicate_delta=duplicates,
                        progress=5 + int(80 * completed / max(1, len(files))),
                    )
                    batch = []
            if batch:
                imported, duplicates = _persist_batch(
                    tenant_id=tenant_id,
                    source_id=source_id,
                    import_file_id=file_id,
                    records=batch,
                )
                file_parsed += len(batch)
                _update_job(
                    job_id,
                    parsed_delta=len(batch),
                    imported_delta=imported,
                    duplicate_delta=duplicates,
                    progress=5 + int(80 * completed / max(1, len(files))),
                )
        except ValueError as exc:
            file_failed = True
            error_code = str(exc)[:80]
            error_message = "文件格式不受支持或内容无法解析"
        except Exception:
            file_failed = True
            error_code = "chat_import_processing_failed"
            error_message = "文件处理失败，请检查文件或稍后重试"

        with SessionLocal() as db:
            current_file = db.get(ChatImportFile, file_id)
            if current_file is not None:
                current_file.status = "failed" if file_failed else "parsed"
                current_file.parsed_message_count = file_parsed
                current_file.error_code = error_code if file_failed else None
                current_file.error_message = error_message if file_failed else None
                db.commit()

        completed += 1
        if file_failed:
            failed_files += 1
        _update_job(
            job_id,
            processed_files_delta=1,
            error_delta=1 if file_failed else 0,
            progress=5 + int(85 * completed / max(1, len(files))),
        )

    with SessionLocal() as db:
        job = db.get(ChatImportJob, job_id)
        source = db.get(ChatSource, source_id)
        if job is None:
            return
        job.progress = 100
        job.worker_id = None
        job.locked_at = None
        job.lease_expires_at = None
        if failed_files:
            job.status = "failed"
            job.error_summary = f"{failed_files} 个文件处理失败，请检查文件错误信息后重试"
        elif source is not None and source.identity_confirmed_at is None:
            job.status = "awaiting_identity"
            job.error_summary = None
        else:
            job.status = "succeeded"
            job.error_summary = None
        db.commit()


def claim_next_chat_import_job(worker_id: str) -> UUID | None:
    now = datetime.now(UTC)
    with SessionLocal() as db:
        job = db.scalar(
            select(ChatImportJob)
            .where(
                or_(
                    ChatImportJob.status == "queued",
                    and_(
                        ChatImportJob.status == "processing",
                        ChatImportJob.lease_expires_at < now,
                    ),
                )
            )
            .order_by(ChatImportJob.created_at)
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        if job is None:
            return None
        if job.attempts >= job.max_attempts:
            job.status = "failed"
            job.error_summary = "任务超过最大重试次数"
            job.progress = 100
            db.commit()
            return None
        job.status = "processing"
        job.worker_id = worker_id
        job.locked_at = now
        job.lease_expires_at = now + timedelta(
            seconds=get_settings().chat_import_worker_lease_seconds
        )
        job.attempts += 1
        db.commit()
        return job.id


def run_forever() -> None:
    worker_id = f"{socket.gethostname()}:{uuid4().hex[:12]}"
    poll_seconds = get_settings().chat_import_worker_poll_seconds
    logger.info("chat_import_worker_started", worker_id=worker_id)
    while True:
        job_id = claim_next_chat_import_job(worker_id)
        if job_id is None:
            time.sleep(poll_seconds)
            continue
        try:
            process_chat_import_job(job_id)
        except Exception:
            logger.exception("chat_import_job_unhandled_failure", job_id=str(job_id))
            with SessionLocal() as db:
                job = db.get(ChatImportJob, job_id)
                if job is not None:
                    job.status = "failed" if job.attempts >= job.max_attempts else "queued"
                    job.error_summary = "任务执行异常，请查看服务端日志"
                    job.worker_id = None
                    job.locked_at = None
                    job.lease_expires_at = None
                    db.commit()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    if args.check:
        with engine.connect() as connection:
            connection.exec_driver_sql("SELECT 1")
        return
    run_forever()


if __name__ == "__main__":
    main()
