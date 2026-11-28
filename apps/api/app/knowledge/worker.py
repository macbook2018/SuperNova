from __future__ import annotations

import hashlib
import socket
import time
from datetime import UTC, datetime, timedelta
from pathlib import PurePosixPath
from urllib.parse import unquote, urlparse
from uuid import UUID, uuid4

import httpx
import structlog
from sqlalchemy import or_, select, update

from app.core.config import get_settings
from app.db.session import SessionLocal
from app.knowledge import index_worker
from app.knowledge.chunking import chunk_blocks
from app.knowledge.embeddings import embed_texts, resolve_embedding_config
from app.knowledge.indexing import lock_index_kb, prepare_legacy_change
from app.knowledge.legacy_revisions import record_revisions
from app.knowledge.network import validate_public_service_url
from app.knowledge.observations import observe_embedding_calls
from app.knowledge.parsers import parse_document, validate_document_bytes
from app.knowledge.storage import delete_object, get_object, put_object
from app.models.catalog import KnowledgeBase
from app.models.knowledge import (
    KnowledgeChunk,
    KnowledgeDocument,
    KnowledgeIndexChunk,
    KnowledgeIngestionJob,
    KnowledgeUsageRecord,
)

logger = structlog.get_logger()
WORKER_ID = f"knowledge-{socket.gethostname()}-{uuid4().hex[:8]}"


class LostLegacyLease(Exception):
    pass


def _document_is_current(document, job):
    if job.job_type == "delete":
        return document.status == "deleting"
    return (
        document.status not in {"deleted", "deleting"}
        and document.pending_index_version == job.index_version
    )


def _owned_job(job_id, attempt):
    return (
        KnowledgeIngestionJob.id == job_id,
        KnowledgeIngestionJob.worker_id == WORKER_ID,
        KnowledgeIngestionJob.attempts == attempt,
        KnowledgeIngestionJob.status == "running",
        KnowledgeIngestionJob.lease_expires_at > datetime.now(UTC),
    )


def _locked_target(db, job, attempt):
    kb = lock_index_kb(db, job.tenant_id, job.knowledge_base_id)
    current = db.scalar(
        select(KnowledgeIngestionJob)
        .where(*_owned_job(job.id, attempt))
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if current is None:
        raise LostLegacyLease
    doc = db.scalar(
        select(KnowledgeDocument)
        .where(
            KnowledgeDocument.id == current.document_id,
            KnowledgeDocument.tenant_id == current.tenant_id,
            KnowledgeDocument.knowledge_base_id == current.knowledge_base_id,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if (
        doc is None
        or not _document_is_current(doc, current)
        or (current.job_type != "delete" and doc.active_index_version >= current.index_version)
    ):
        current.status = "cancelled"
        current.worker_id = None
        current.lease_expires_at = None
        db.commit()
        raise LostLegacyLease
    return kb, doc


def _claim_job() -> UUID | None:
    settings = get_settings()
    now = datetime.now(UTC)
    with SessionLocal() as db:
        eligible = or_(
            KnowledgeIngestionJob.status.in_(("queued", "retry_wait"))
            & (KnowledgeIngestionJob.available_at <= now),
            (KnowledgeIngestionJob.status == "running")
            & (KnowledgeIngestionJob.lease_expires_at <= now),
        )
        job = db.scalar(
            select(KnowledgeIngestionJob)
            .where(eligible)
            .order_by(KnowledgeIngestionJob.available_at, KnowledgeIngestionJob.created_at)
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        if job is None:
            return None
        if job.attempts >= job.max_attempts:
            job_id, tenant_id, kb_id = job.id, job.tenant_id, job.knowledge_base_id
            db.rollback()
            # All worker finalizers and source writers acquire the KB before a job/document.
            try:
                lock_index_kb(db, tenant_id, kb_id)
            except LookupError:
                return None
            job = db.scalar(
                select(KnowledgeIngestionJob)
                .where(
                    KnowledgeIngestionJob.id == job_id,
                    eligible,
                    KnowledgeIngestionJob.attempts >= KnowledgeIngestionJob.max_attempts,
                )
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            if job is None:
                return None
            job.status = "dead_letter"
            job.last_error = "knowledge_max_attempts_exceeded"
            job.worker_id = None
            job.locked_at = None
            job.lease_expires_at = None
            document = db.scalar(
                select(KnowledgeDocument)
                .where(
                    KnowledgeDocument.id == job.document_id,
                    KnowledgeDocument.tenant_id == job.tenant_id,
                    KnowledgeDocument.knowledge_base_id == job.knowledge_base_id,
                )
                .with_for_update()
            )
            if document is not None and _document_is_current(document, job):
                if job.job_type != "delete":
                    if job.job_type == "reindex" and document.active_index_version > 0:
                        document.status = "ready"
                    else:
                        document.status = "failed"
                document.error_code = "MaxAttemptsExceeded"
                document.error_message = "知识库任务超过最大重试次数，请手动重试"
            db.commit()
            return None
        job.status = "running"
        job.attempts += 1
        job.locked_at = now
        job.lease_expires_at = now + timedelta(seconds=settings.knowledge_worker_lease_seconds)
        job.worker_id = WORKER_ID
        job.last_error = None
        db.commit()
        return job.id


def _extend_job_lease(job_id, attempt) -> None:
    settings = get_settings()
    with SessionLocal.begin() as db:
        changed = db.execute(
            update(KnowledgeIngestionJob)
            .where(*_owned_job(job_id, attempt))
            .values(
                lease_expires_at=datetime.now(UTC)
                + timedelta(seconds=settings.knowledge_worker_lease_seconds),
            )
        )
        if changed.rowcount != 1:
            raise LostLegacyLease


def _filename_from_response(url: str, content_type: str) -> str:
    parsed = urlparse(url)
    path_name = unquote(PurePosixPath(parsed.path).name)
    suffix = PurePosixPath(path_name).suffix.lower()
    if suffix in {".pdf", ".docx", ".pptx", ".xlsx", ".txt", ".md", ".csv", ".html", ".htm"}:
        return path_name[:255] or "web-page" + suffix
    media = content_type.split(";", 1)[0].strip().lower()
    extensions = {
        "application/pdf": ".pdf",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
        "application/vnd.openxmlformats-officedocument.presentationml.presentation": ".pptx",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
        "text/plain": ".txt",
        "text/markdown": ".md",
        "text/csv": ".csv",
        "text/html": ".html",
    }
    extension = extensions.get(media)
    if extension is None:
        raise ValueError("url_content_type_not_supported")
    return ("web-page" + extension)[:255]


def _fetch_url(url: str) -> tuple[bytes, str, str]:
    settings = get_settings()
    validate_public_service_url(url)
    timeout = httpx.Timeout(30.0, connect=8.0)
    with httpx.Client(
        timeout=timeout,
        follow_redirects=False,
        trust_env=False,
        headers={"User-Agent": "SuperNovaKnowledgeBot/1.0"},
    ) as client:
        with client.stream("GET", url) as response:
            if 300 <= response.status_code < 400:
                raise ValueError("url_redirect_blocked")
            if response.status_code != 200:
                raise ValueError(f"url_fetch_http_{response.status_code}")
            content_type = response.headers.get("content-type", "text/html")
            declared_length = response.headers.get("content-length")
            if declared_length and declared_length.isdigit():
                if int(declared_length) > settings.knowledge_max_url_bytes:
                    raise ValueError("url_content_too_large")
            body = bytearray()
            for part in response.iter_bytes():
                body.extend(part)
                if len(body) > settings.knowledge_max_url_bytes:
                    raise ValueError("url_content_too_large")
    filename = _filename_from_response(url, content_type)
    return bytes(body), content_type, filename


def _process_delete(db, job: KnowledgeIngestionJob, document: KnowledgeDocument) -> None:
    db.query(KnowledgeChunk).filter(
        KnowledgeChunk.tenant_id == job.tenant_id,
        KnowledgeChunk.knowledge_base_id == job.knowledge_base_id,
        KnowledgeChunk.document_id == document.id,
    ).delete(synchronize_session=False)
    db.query(KnowledgeIndexChunk).filter(
        KnowledgeIndexChunk.tenant_id == job.tenant_id,
        KnowledgeIndexChunk.knowledge_base_id == job.knowledge_base_id,
        KnowledgeIndexChunk.document_id == document.id,
    ).delete(synchronize_session=False)
    delete_object(document.object_key)
    document.status = "deleted"
    document.sha256 = None
    document.chunk_count = 0
    document.error_code = None
    document.error_message = None
    job.status = "succeeded"
    job.lease_expires_at = None
    job.worker_id = None
    db.commit()


def _process_ingest(
    db,
    job: KnowledgeIngestionJob,
    document: KnowledgeDocument,
    kb: KnowledgeBase,
) -> None:
    attempt = job.attempts
    started = time.monotonic()
    settings = get_settings()
    if document.source_type == "url" and document.sha256 is None:
        body, response_type, filename = _fetch_url(document.source_url or "")
        mime_type = validate_document_bytes(filename, response_type, body)
        digest = hashlib.sha256(body).hexdigest()
        duplicate = db.scalar(
            select(KnowledgeDocument.id)
            .where(
                KnowledgeDocument.tenant_id == document.tenant_id,
                KnowledgeDocument.knowledge_base_id == document.knowledge_base_id,
                KnowledgeDocument.sha256 == digest,
                KnowledgeDocument.id != document.id,
                KnowledgeDocument.status != "deleted",
            )
            .limit(1)
        )
        if duplicate is not None:
            raise ValueError("duplicate_document_content")
        _locked_target(db, job, attempt)
        put_object(document.object_key, body, mime_type)
        document.original_filename = filename
        document.mime_type = mime_type
        document.file_size_bytes = len(body)
        document.sha256 = digest
        db.commit()
    else:
        body = get_object(document.object_key, settings.knowledge_max_upload_bytes)
        mime_type = validate_document_bytes(document.original_filename, document.mime_type, body)

    blocks = parse_document(document.original_filename, mime_type, body)
    chunks = chunk_blocks(
        blocks,
        chunk_size=kb.chunk_size,
        chunk_overlap=kb.chunk_overlap,
    )
    if len(chunks) > 5000:
        raise ValueError("document_chunk_limit_exceeded")

    embedding_config = resolve_embedding_config(db, kb)
    db.commit()
    _extend_job_lease(job.id, attempt)
    with observe_embedding_calls(
        db,
        tenant_id=job.tenant_id,
        knowledge_base_id=job.knowledge_base_id,
        operation="index_embedding",
        request_id=str(job.id),
        document_id=document.id,
    ):
        batch = embed_texts(
            embedding_config,
            [item["content"] for item in chunks],
            on_batch=lambda: _extend_job_lease(job.id, attempt),
        )
    if len(batch.vectors) != len(chunks):
        raise ValueError("embedding_count_mismatch")

    kb, document = _locked_target(db, job, attempt)
    prepare_legacy_change(db, job.tenant_id, job.knowledge_base_id)

    records = []
    for index, (item, vector) in enumerate(zip(chunks, batch.vectors, strict=True)):
        records.append(
            KnowledgeChunk(
                id=uuid4(),
                tenant_id=job.tenant_id,
                knowledge_base_id=job.knowledge_base_id,
                document_id=document.id,
                index_version=job.index_version,
                chunk_index=index,
                content=item["content"],
                content_hash=item["content_hash"],
                embedding=vector,
                metadata_json=item["metadata"],
            )
        )
    # Keep previously published v1 rows and their citation IDs; retrieval filters by active version.
    record_revisions(
        db, document, kb, embedding_config, new_version=job.index_version, new_count=len(records)
    )
    db.add_all(records)
    document.active_index_version = job.index_version
    document.pending_index_version = job.index_version
    document.chunk_count = len(records)
    document.status = "ready"
    document.error_code = None
    document.error_message = None
    db.add(
        KnowledgeUsageRecord(
            id=uuid4(),
            tenant_id=job.tenant_id,
            knowledge_base_id=job.knowledge_base_id,
            document_id=document.id,
            operation=job.job_type,
            embedding_model_name=embedding_config.model_name,
            input_tokens=batch.input_tokens,
            request_count=batch.request_count,
            storage_bytes=document.file_size_bytes,
            chunk_count=len(records),
            latency_ms=max(0, int((time.monotonic() - started) * 1000)),
            status="succeeded" if batch.usage_known else "usage_unknown",
        )
    )
    job.status = "succeeded"
    job.lease_expires_at = None
    job.worker_id = None
    job.last_error = None
    db.commit()


def _handle_failure(job_id: UUID, attempt: int, error: Exception) -> None:
    with SessionLocal() as db:
        job = db.get(KnowledgeIngestionJob, job_id)
        if job is None:
            return
        try:
            _, document = _locked_target(db, job, attempt)
        except (LookupError, LostLegacyLease):
            return
        message = (
            str(error).splitlines()[0][:500]
            if isinstance(error, (ValueError, RuntimeError))
            else error.__class__.__name__
        )
        final = job.attempts >= job.max_attempts or isinstance(error, ValueError)
        job.status = "dead_letter" if final else "retry_wait"
        job.last_error = message
        job.worker_id = None
        job.lease_expires_at = None
        if not final:
            delay = min(300, 2 ** min(job.attempts, 8))
            job.available_at = datetime.now(UTC) + timedelta(seconds=delay)
        if document is not None:
            if job.job_type != "delete":
                if job.job_type == "reindex" and document.active_index_version > 0:
                    document.status = "ready"
                elif final:
                    document.status = "failed"
                else:
                    document.status = "queued"
            document.error_code = error.__class__.__name__[:80]
            document.error_message = message
        db.commit()
        logger.warning(
            "knowledge_ingestion_job_failed",
            job_id=str(job_id),
            tenant_id=str(job.tenant_id),
            knowledge_base_id=str(job.knowledge_base_id),
            attempts=job.attempts,
            final=final,
            error_type=error.__class__.__name__,
        )


def process_job(job_id: UUID) -> None:
    attempt = None
    try:
        with SessionLocal() as db:
            job = db.get(KnowledgeIngestionJob, job_id)
            if job is None or job.status != "running" or job.worker_id != WORKER_ID:
                return
            attempt = job.attempts
            kb, document = _locked_target(db, job, attempt)
            if job.job_type == "delete":
                prepare_legacy_change(db, job.tenant_id, job.knowledge_base_id)
                _process_delete(db, job, document)
            else:
                if job.job_type != "reindex" or document.active_index_version == 0:
                    document.status = "processing"
                db.commit()
                _process_ingest(db, job, document, kb)
    except (LookupError, LostLegacyLease):
        return
    except Exception as exc:
        if attempt is not None:
            _handle_failure(job_id, attempt, exc)


def run_forever() -> None:
    settings = get_settings()
    logger.info("knowledge_worker_started", worker_id=WORKER_ID)
    while True:
        job_id = _claim_job()
        if job_id is not None:
            process_job(job_id)
        candidate = index_worker.claim()
        if candidate is not None:
            index_worker.process(*candidate)
        if job_id is None and candidate is None:
            time.sleep(settings.knowledge_worker_poll_seconds)


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    if args.check:
        with SessionLocal() as db:
            db.execute(select(1))
        print("knowledge worker database check passed")
        return
    run_forever()


if __name__ == "__main__":
    main()
