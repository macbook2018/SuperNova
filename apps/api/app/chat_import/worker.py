from __future__ import annotations

import argparse
import socket
import time
from collections import Counter
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import structlog
from sqlalchemy import and_, delete, or_, select

from app.chat_import.parsers import is_text_message, parse_chat_export
from app.core.config import get_settings
from app.db.session import SessionLocal, engine
from app.knowledge.storage import delete_object, get_object
from app.models.chat_import import (
    ChatImportFile,
    ChatImportJob,
    ChatImportSenderSummary,
    ChatSource,
)

logger = structlog.get_logger()


def _job_cancelled(job_id: UUID) -> bool:
    with SessionLocal() as db:
        current_status = db.scalar(
            select(ChatImportJob.status).where(ChatImportJob.id == job_id)
        )
    return current_status == "cancelled"


def cleanup_job_temporary_uploads(job_id: UUID) -> bool:
    """Delete temporary upload objects and their metadata. Safe to retry."""
    with SessionLocal() as db:
        files = db.scalars(
            select(ChatImportFile).where(ChatImportFile.job_id == job_id)
        ).all()
        targets = [(item.id, item.object_key) for item in files]

    failed = False
    deleted_ids: list[UUID] = []
    for file_id, object_key in targets:
        try:
            delete_object(object_key)
            deleted_ids.append(file_id)
        except Exception:
            failed = True

    if deleted_ids:
        with SessionLocal() as db:
            db.execute(
                delete(ChatImportFile).where(ChatImportFile.id.in_(deleted_ids))
            )
            db.commit()
    return not failed


def cleanup_expired_chat_import_uploads() -> int:
    """Expire abandoned uploads and retry cleanup for terminal jobs."""
    now = datetime.now(UTC)
    with SessionLocal() as db:
        expired_job_ids = set(
            db.scalars(
                select(ChatImportFile.job_id).where(ChatImportFile.expires_at <= now)
            ).all()
        )
        terminal_job_ids = set(
            db.scalars(
                select(ChatImportFile.job_id)
                .join(ChatImportJob, ChatImportJob.id == ChatImportFile.job_id)
                .where(ChatImportJob.status.in_(("failed", "cancelled", "succeeded")))
            ).all()
        )

    cleaned = 0
    for job_id in expired_job_ids | terminal_job_ids:
        with SessionLocal() as db:
            job = db.get(ChatImportJob, job_id)
            if job is None:
                continue
            if job.status == "processing" and job.lease_expires_at is not None:
                lease = job.lease_expires_at
                if lease.tzinfo is None:
                    lease = lease.replace(tzinfo=UTC)
                if lease > now:
                    continue
            if job.status in {
                "queued", "awaiting_identity", "ready_for_analysis", "processing"
            } and job_id in expired_job_ids:
                job.status = "failed"
                job.progress = 100
                job.error_count += 1
                job.error_summary = "临时导入文件已过期，请重新上传"
                job.worker_id = None
                job.locked_at = None
                job.lease_expires_at = None
                db.execute(
                    delete(ChatImportSenderSummary).where(
                        ChatImportSenderSummary.job_id == job_id
                    )
                )
                db.commit()

        if cleanup_job_temporary_uploads(job_id):
            cleaned += 1
    return cleaned


def process_chat_import_job(job_id: UUID) -> None:
    with SessionLocal() as db:
        job = db.get(ChatImportJob, job_id)
        if job is None or job.status != "processing":
            return
        source = db.scalar(
            select(ChatSource).where(
                ChatSource.id == job.source_id,
                ChatSource.tenant_id == job.tenant_id,
                ChatSource.status == "active",
            )
        )
        if source is None:
            job.status = "failed"
            job.error_summary = "来源账号不存在或已停用"
            job.progress = 100
            db.commit()
            cleanup_job_temporary_uploads(job_id)
            return

        files = db.scalars(
            select(ChatImportFile)
            .where(
                ChatImportFile.tenant_id == job.tenant_id,
                ChatImportFile.source_id == job.source_id,
                ChatImportFile.job_id == job.id,
            )
            .order_by(ChatImportFile.created_at)
        ).all()
        if not files:
            job.status = "failed"
            job.error_summary = "没有可处理的临时导入文件，请重新上传"
            job.progress = 100
            db.commit()
            return

        tenant_id = job.tenant_id
        source_id = job.source_id
        job.total_files = len(files)
        job.status = "processing"
        db.commit()
        file_items = [(item.id, item.object_key, item.original_filename) for item in files]

    sender_counts: Counter[str] = Counter()
    sender_names: dict[str, str] = {}
    parsed_text_messages = 0
    ignored_non_text_messages = 0
    processed_files = 0
    failed_files = 0
    settings = get_settings()

    for file_id, object_key, filename in file_items:
        if _job_cancelled(job_id):
            cleanup_job_temporary_uploads(job_id)
            return

        with SessionLocal() as db:
            file_record = db.get(ChatImportFile, file_id)
            if file_record is None:
                failed_files += 1
                continue
            file_record.status = "processing"
            file_record.error_code = None
            file_record.error_message = None
            db.commit()

        file_text_count = 0
        file_ignored_count = 0
        file_error_code: str | None = None
        file_error_message: str | None = None

        try:
            body = get_object(object_key, max_bytes=settings.chat_import_max_file_bytes)
            for record in parse_chat_export(filename, body):
                if _job_cancelled(job_id):
                    cleanup_job_temporary_uploads(job_id)
                    return
                if not is_text_message(record):
                    file_ignored_count += 1
                    continue

                file_text_count += 1
                sender_counts[record.sender_ref] += 1
                sender_names[record.sender_ref] = record.sender_name
                # record.content is intentionally consumed only in worker memory.
                # It is never added to a database model, API response, or log field.
        except ValueError as exc:
            file_error_code = str(exc)[:80]
            if file_error_code == "wekeep_archive_requires_zip":
                file_error_message = "检测到 WeKeep 离线归档，请上传包含完整文件夹的 ZIP 压缩包"
            elif "wekeep" in file_error_code:
                file_error_message = "WeKeep 导出包结构不完整或消息分片损坏，请重新导出完整 ZIP"
            else:
                file_error_message = "文件格式不受支持或内容无法解析"
        except Exception:
            file_error_code = "chat_import_processing_failed"
            file_error_message = "文件处理失败，请检查文件或稍后重新上传"

        with SessionLocal() as db:
            file_record = db.get(ChatImportFile, file_id)
            if file_record is not None:
                file_record.status = "failed" if file_error_code else "parsed"
                file_record.parsed_text_message_count = file_text_count
                file_record.ignored_non_text_message_count = file_ignored_count
                file_record.error_code = file_error_code
                file_record.error_message = file_error_message
                db.commit()

        parsed_text_messages += file_text_count
        ignored_non_text_messages += file_ignored_count
        processed_files += 1
        if file_error_code:
            failed_files += 1

        with SessionLocal() as db:
            job = db.get(ChatImportJob, job_id)
            if job is None or job.status == "cancelled":
                cleanup_job_temporary_uploads(job_id)
                return
            job.progress = min(95, int(95 * processed_files / max(1, len(file_items))))
            job.processed_files = processed_files
            job.parsed_text_messages = parsed_text_messages
            job.ignored_non_text_messages = ignored_non_text_messages
            job.error_count = failed_files
            job.lease_expires_at = datetime.now(UTC) + timedelta(
                seconds=settings.chat_import_worker_lease_seconds
            )
            db.commit()

    with SessionLocal() as db:
        job = db.get(ChatImportJob, job_id)
        source = db.scalar(
            select(ChatSource).where(
                ChatSource.id == source_id,
                ChatSource.tenant_id == tenant_id,
            )
        )
        if job is None or job.status == "cancelled":
            cleanup_job_temporary_uploads(job_id)
            return

        job.progress = 100
        job.worker_id = None
        job.locked_at = None
        job.lease_expires_at = None
        job.processed_files = processed_files
        job.parsed_text_messages = parsed_text_messages
        job.ignored_non_text_messages = ignored_non_text_messages
        job.unique_senders = len(sender_counts)

        if failed_files:
            job.status = "failed"
            job.error_summary = f"{failed_files} 个文件处理失败，请检查文件后重新上传"
            db.execute(
                delete(ChatImportSenderSummary).where(
                    ChatImportSenderSummary.tenant_id == tenant_id,
                    ChatImportSenderSummary.job_id == job.id,
                )
            )
        elif source is None or source.status != "active":
            job.status = "failed"
            job.error_summary = "来源账号不存在或已停用"
        elif source.identity_confirmed_at is None:
            db.execute(
                delete(ChatImportSenderSummary).where(
                    ChatImportSenderSummary.tenant_id == tenant_id,
                    ChatImportSenderSummary.job_id == job.id,
                )
            )
            for sender_ref, message_count in sender_counts.items():
                db.add(
                    ChatImportSenderSummary(
                        id=uuid4(),
                        tenant_id=tenant_id,
                        source_id=source_id,
                        job_id=job.id,
                        sender_ref=sender_ref,
                        sender_name=sender_names[sender_ref],
                        message_count=message_count,
                    )
                )
            job.status = "awaiting_identity"
            job.error_summary = None
        else:
            job.status = "ready_for_analysis"
            job.error_summary = None
        db.commit()

    if failed_files or source is None:
        cleanup_job_temporary_uploads(job_id)


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
            job.error_summary = "任务超过最大重试次数，请重新上传"
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
    last_cleanup = 0.0
    logger.info("chat_import_worker_started", worker_id=worker_id)
    while True:
        now_monotonic = time.monotonic()
        if now_monotonic - last_cleanup >= 30:
            try:
                cleanup_expired_chat_import_uploads()
            except Exception:
                logger.exception("chat_import_temp_cleanup_failed")
            last_cleanup = now_monotonic

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
                    job.error_summary = "任务执行异常，请重新上传或稍后重试"
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
