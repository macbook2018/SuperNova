from __future__ import annotations

import argparse
import hashlib
import socket
import time
from datetime import UTC, datetime, timedelta
from pathlib import PurePosixPath
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

import structlog
from sqlalchemy import and_, delete, or_, select

from app.chat_import.media import extract_wekeep_media
from app.chat_import.parsers import NormalizedChatMessage, parse_chat_export
from app.core.config import get_settings
from app.db.session import SessionLocal, engine
from app.knowledge.storage import delete_object, get_object, put_object
from app.models.chat_import import (
    ChatConversation,
    ChatImportFile,
    ChatImportJob,
    ChatMediaAsset,
    ChatMediaExtractionJob,
    ChatMessage,
    ChatParticipant,
    ChatSource,
)

logger = structlog.get_logger()


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


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
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _persist_batch(
    *,
    job_id: UUID,
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
        job = db.scalar(
            select(ChatImportJob)
            .where(
                ChatImportJob.id == job_id,
                ChatImportJob.tenant_id == tenant_id,
                ChatImportJob.source_id == source_id,
            )
            .with_for_update()
        )
        if job is None or job.status != "processing":
            raise ValueError("chat_import_job_not_processing")
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
        existing_messages = db.scalars(
            select(ChatMessage).where(
                ChatMessage.tenant_id == tenant_id,
                ChatMessage.source_id == source_id,
                ChatMessage.fingerprint.in_(fingerprints),
            )
        ).all()
        existing_by_fingerprint = {
            item.fingerprint: item for item in existing_messages
        }
        existing = set(existing_by_fingerprint)
        file_record = db.get(ChatImportFile, import_file_id)
        if file_record is not None and any(
            record.metadata.get("parser") == "wekeep-export-v1"
            for record in records
        ):
            file_metadata = (
                file_record.metadata_json
                if isinstance(file_record.metadata_json, dict)
                else {}
            )
            file_record.metadata_json = {
                **file_metadata,
                "parser": "wekeep-export-v1",
            }
        conversation_cache: dict[str, ChatConversation] = {}
        participant_cache: dict[tuple[UUID, str], ChatParticipant] = {}

        for record in records:
            fingerprint = _message_fingerprint(record)
            if fingerprint in existing:
                duplicates += 1
                existing_message = existing_by_fingerprint.get(fingerprint)
                if (
                    existing_message is not None
                    and record.metadata.get("parser") == "wekeep-export-v1"
                ):
                    existing_metadata = (
                        existing_message.metadata_json
                        if isinstance(existing_message.metadata_json, dict)
                        else {}
                    )
                    existing_message.metadata_json = {
                        **existing_metadata,
                        **record.metadata,
                        "source_file": record.source_file,
                        "media_import_file_id": str(import_file_id),
                    }
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
            if (
                conversation.conversation_type == "unknown"
                and record.conversation_type != "unknown"
            ):
                conversation.conversation_type = record.conversation_type
            first_message_at = _as_utc(conversation.first_message_at)
            last_message_at = _as_utc(conversation.last_message_at)
            if first_message_at is None or record.timestamp < first_message_at:
                conversation.first_message_at = record.timestamp
            if last_message_at is None or record.timestamp > last_message_at:
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
            message_metadata = {
                **record.metadata,
                "source_file": record.source_file,
            }
            if record.metadata.get("parser") == "wekeep-export-v1":
                message_metadata["media_import_file_id"] = str(import_file_id)
            message = ChatMessage(
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
                metadata_json=message_metadata,
            )
            db.add(message)
            existing_by_fingerprint[fingerprint] = message
            imported += 1
        job.parsed_messages += len(records)
        job.imported_messages += imported
        job.duplicate_messages += duplicates
        db.commit()
    return imported, duplicates


def _update_job(
    job_id: UUID,
    *,
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
        job.processed_files += processed_files_delta
        job.error_count += error_delta
        if progress is not None:
            job.progress = max(job.progress, min(99, progress))
        if extend_lease and job.status == "processing":
            job.lease_expires_at = datetime.now(UTC) + timedelta(
                seconds=settings.chat_import_worker_lease_seconds
            )
        db.commit()


def _job_cancelled(job_id: UUID) -> bool:
    with SessionLocal() as db:
        status = db.scalar(
            select(ChatImportJob.status).where(ChatImportJob.id == job_id)
        )
    return status == "cancelled"


def _release_file_for_cancel(file_id: UUID) -> None:
    with SessionLocal() as db:
        file_record = db.get(ChatImportFile, file_id)
        if file_record is not None and file_record.status == "processing":
            file_record.status = "uploaded"
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
        all_files = db.scalars(
            select(ChatImportFile)
            .where(
                ChatImportFile.tenant_id == job.tenant_id,
                ChatImportFile.source_id == job.source_id,
                ChatImportFile.job_id == job.id,
            )
            .order_by(ChatImportFile.created_at)
        ).all()
        files = [
            item for item in all_files
            if item.status in {"uploaded", "failed", "processing"}
        ]
        job.total_files = len(all_files)
        job.processed_files = sum(item.status == "parsed" for item in all_files)
        job.status = "processing"
        db.commit()
        tenant_id = job.tenant_id
        source_id = job.source_id

    if not all_files:
        with SessionLocal() as db:
            job = db.get(ChatImportJob, job_id)
            if job is not None:
                job.status = "failed"
                job.error_summary = "没有可处理的导入文件"
                job.progress = 100
                db.commit()
        return

    if not files:
        with SessionLocal() as db:
            job = db.get(ChatImportJob, job_id)
            source = db.get(ChatSource, source_id)
            if job is None or job.status == "cancelled":
                return
            if source is not None and source.identity_confirmed_at is None:
                job.status = "awaiting_identity"
            else:
                job.status = "succeeded"
            job.progress = 100
            job.worker_id = None
            job.locked_at = None
            job.lease_expires_at = None
            db.commit()
        return

    settings = get_settings()
    completed = 0
    failed_files = 0
    for file_record in files:
        if _job_cancelled(job_id):
            return
        file_id = file_record.id
        object_key = file_record.object_key
        filename = file_record.original_filename
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
                if _job_cancelled(job_id):
                    _release_file_for_cancel(file_id)
                    return
                batch.append(record)
                if len(batch) >= settings.chat_import_batch_size:
                    _persist_batch(
                        job_id=job_id,
                        tenant_id=tenant_id,
                        source_id=source_id,
                        import_file_id=file_id,
                        records=batch,
                    )
                    file_parsed += len(batch)
                    _update_job(
                        job_id,
                        progress=5 + int(80 * completed / max(1, len(files))),
                    )
                    batch = []
            if batch:
                _persist_batch(
                    job_id=job_id,
                    tenant_id=tenant_id,
                    source_id=source_id,
                    import_file_id=file_id,
                    records=batch,
                )
                file_parsed += len(batch)
                _update_job(
                    job_id,
                    progress=5 + int(80 * completed / max(1, len(files))),
                )
        except ValueError as exc:
            file_failed = True
            error_code = str(exc)[:80]
            if error_code == "wekeep_archive_requires_zip":
                error_message = "检测到 WeKeep 离线归档，请上传包含完整文件夹的 ZIP 压缩包"
            elif "wekeep" in error_code:
                error_message = "WeKeep 导出包结构不完整或消息分片损坏，请重新导出完整 ZIP"
            else:
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
        if job is None or job.status == "cancelled":
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




def process_chat_media_extraction_job(job_id: UUID) -> None:
    with SessionLocal() as db:
        job = db.get(ChatMediaExtractionJob, job_id)
        if job is None or job.status != "processing":
            return
        source = db.scalar(
            select(ChatSource).where(
                ChatSource.id == job.source_id,
                ChatSource.tenant_id == job.tenant_id,
                ChatSource.status == "active",
            )
        )
        file_record = db.scalar(
            select(ChatImportFile).where(
                ChatImportFile.id == job.import_file_id,
                ChatImportFile.tenant_id == job.tenant_id,
                ChatImportFile.source_id == job.source_id,
                ChatImportFile.status == "parsed",
            )
        )
        if source is None or file_record is None or file_record.extension != ".zip":
            job.status = "failed"
            job.progress = 100
            job.error_summary = "找不到已解析的 ZIP 文件或聊天数据源已删除"
            job.worker_id = None
            job.locked_at = None
            job.lease_expires_at = None
            db.commit()
            return

        tenant_id = job.tenant_id
        source_id = job.source_id
        import_file_id = job.import_file_id
        object_key = file_record.object_key
        participants = db.scalars(
            select(ChatParticipant).where(
                ChatParticipant.tenant_id == tenant_id,
                ChatParticipant.source_id == source_id,
            )
        ).all()
        old_assets = db.scalars(
            select(ChatMediaAsset).where(
                ChatMediaAsset.tenant_id == tenant_id,
                ChatMediaAsset.source_id == source_id,
                ChatMediaAsset.import_file_id == import_file_id,
            )
        ).all()
        old_object_keys = [item.object_key for item in old_assets if item.object_key]

    settings = get_settings()
    uploaded_keys: list[str] = []
    try:
        for old_key in old_object_keys:
            delete_object(old_key)

        if old_assets:
            with SessionLocal() as db:
                db.execute(
                    delete(ChatMediaAsset).where(
                        ChatMediaAsset.tenant_id == tenant_id,
                        ChatMediaAsset.source_id == source_id,
                        ChatMediaAsset.import_file_id == import_file_id,
                    )
                )
                db.commit()

        archive_body = get_object(
            object_key,
            max_bytes=settings.chat_import_max_file_bytes,
        )
        with SessionLocal() as db:
            messages = db.scalars(
                select(ChatMessage)
                .where(
                    ChatMessage.tenant_id == tenant_id,
                    ChatMessage.source_id == source_id,
                )
                .order_by(ChatMessage.timestamp, ChatMessage.id)
                .execution_options(yield_per=500)
            )
            extracted = extract_wekeep_media(
                archive_body,
                messages,
                participants,
                import_file_id=import_file_id,
                max_asset_bytes=settings.chat_media_max_asset_bytes,
                max_total_bytes=settings.chat_media_max_total_bytes,
            )

        assets: list[ChatMediaAsset] = []
        extracted_count = 0
        missing_count = 0
        error_count = 0
        for asset_index, item in enumerate(extracted):
            asset_id = uuid5(
                NAMESPACE_URL,
                f"supernova-chat-media:{import_file_id}:{asset_index}",
            )
            stored_key = None
            body = item.body
            file_size = len(body) if body is not None else 0
            if item.status == "available" and body is not None:
                suffix = PurePosixPath(item.archive_path or item.original_name or "").suffix
                if not suffix or len(suffix) > 12 or not suffix[1:].isalnum():
                    suffix = ".bin"
                stored_key = (
                    f"tenant/{tenant_id}/chat-media/{source_id}/"
                    f"{import_file_id.hex}/{asset_id.hex}{suffix.casefold()}"
                )
                put_object(
                    stored_key,
                    body,
                    item.mime_type or "application/octet-stream",
                )
                uploaded_keys.append(stored_key)
                extracted_count += 1
            elif item.status == "missing":
                missing_count += 1
            else:
                error_count += 1

            assets.append(
                ChatMediaAsset(
                    id=asset_id,
                    tenant_id=tenant_id,
                    source_id=source_id,
                    conversation_id=item.conversation_id,
                    import_file_id=import_file_id,
                    message_id=item.message_id,
                    participant_id=item.participant_id,
                    asset_type=item.asset_type,
                    role=item.role,
                    status=item.status,
                    archive_path=item.archive_path,
                    original_name=item.original_name,
                    mime_type=item.mime_type,
                    file_size_bytes=file_size,
                    sha256=item.sha256,
                    object_key=stored_key,
                    metadata_json=item.metadata,
                )
            )

        with SessionLocal() as db:
            current_job = db.get(ChatMediaExtractionJob, job_id)
            if current_job is None or current_job.status != "processing":
                raise ValueError("chat_media_extraction_job_not_processing")
            db.add_all(assets)
            current_job.total_assets = len(assets)
            current_job.extracted_assets = extracted_count
            current_job.missing_assets = missing_count
            current_job.error_count = error_count
            current_job.progress = 100
            current_job.status = "partial" if missing_count or error_count else "succeeded"
            current_job.error_summary = (
                f"已提取 {extracted_count} 个资源，"
                f"{missing_count} 个附件缺失，{error_count} 个资源无法提取"
                if missing_count or error_count
                else None
            )
            current_job.worker_id = None
            current_job.locked_at = None
            current_job.lease_expires_at = None
            db.commit()

        logger.info(
            "chat_media_extraction_completed",
            job_id=str(job_id),
            source_id=str(source_id),
            import_file_id=str(import_file_id),
            total_assets=len(assets),
            extracted_assets=extracted_count,
            missing_assets=missing_count,
            error_count=error_count,
        )
    except Exception:
        for stored_key in uploaded_keys:
            try:
                delete_object(stored_key)
            except Exception:
                logger.exception(
                    "chat_media_object_rollback_failed",
                    job_id=str(job_id),
                    object_key=stored_key,
                )
        raise


def claim_next_chat_media_job(worker_id: str) -> UUID | None:
    now = datetime.now(UTC)
    with SessionLocal() as db:
        job = db.scalar(
            select(ChatMediaExtractionJob)
            .where(
                or_(
                    ChatMediaExtractionJob.status == "queued",
                    and_(
                        ChatMediaExtractionJob.status == "processing",
                        ChatMediaExtractionJob.lease_expires_at < now,
                    ),
                )
            )
            .order_by(ChatMediaExtractionJob.created_at)
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        if job is None:
            return None
        if job.attempts >= job.max_attempts:
            job.status = "failed"
            job.error_summary = "媒体提取任务超过最大重试次数"
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
        import_job_id = claim_next_chat_import_job(worker_id)
        if import_job_id is not None:
            try:
                process_chat_import_job(import_job_id)
            except Exception:
                logger.exception(
                    "chat_import_job_unhandled_failure",
                    job_id=str(import_job_id),
                )
                with SessionLocal() as db:
                    job = db.get(ChatImportJob, import_job_id)
                    if job is not None:
                        job.status = (
                            "failed" if job.attempts >= job.max_attempts else "queued"
                        )
                        job.error_summary = "任务执行异常，请查看服务端日志"
                        job.worker_id = None
                        job.locked_at = None
                        job.lease_expires_at = None
                        db.commit()
            continue

        media_job_id = claim_next_chat_media_job(worker_id)
        if media_job_id is not None:
            try:
                process_chat_media_extraction_job(media_job_id)
            except Exception:
                logger.exception(
                    "chat_media_extraction_unhandled_failure",
                    job_id=str(media_job_id),
                )
                with SessionLocal() as db:
                    job = db.get(ChatMediaExtractionJob, media_job_id)
                    if job is not None:
                        job.status = (
                            "failed" if job.attempts >= job.max_attempts else "queued"
                        )
                        job.error_summary = "媒体资源提取异常，请查看服务端日志后重试"
                        job.worker_id = None
                        job.locked_at = None
                        job.lease_expires_at = None
                        db.commit()
            continue

        time.sleep(poll_seconds)


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
