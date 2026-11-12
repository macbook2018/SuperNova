from __future__ import annotations

import hashlib
import socket
import time
from datetime import UTC, datetime, timedelta
from pathlib import PurePosixPath
from urllib.parse import unquote, urlparse
from uuid import uuid4

import httpx
import structlog
from sqlalchemy import or_, select

from app.core.config import get_settings
from app.db.session import SessionLocal
from app.knowledge.chunking import chunk_blocks
from app.knowledge.embeddings import embed_texts, resolve_embedding_config
from app.knowledge.network import validate_public_service_url
from app.knowledge.parsers import parse_document, validate_document_bytes
from app.knowledge.storage import delete_object, get_object, put_object
from app.models.catalog import KnowledgeBase
from app.models.knowledge import (
    KnowledgeChunk,
    KnowledgeDocument,
    KnowledgeIngestionJob,
    KnowledgeUsageRecord,
)

logger = structlog.get_logger()
WORKER_ID = f"knowledge-{socket.gethostname()}-{uuid4().hex[:8]}"


def _claim_job() -> str | None:
    settings = get_settings()
    now = datetime.now(UTC)
    with SessionLocal() as db:
        job = db.scalar(
            select(KnowledgeIngestionJob)
            .where(
                or_(
                    (
                        KnowledgeIngestionJob.status.in_(("queued", "retry_wait"))
                        & (KnowledgeIngestionJob.available_at <= now)
                    ),
                    (
                        (KnowledgeIngestionJob.status == "running")
                        & (KnowledgeIngestionJob.lease_expires_at < now)
                    ),
                )
            )
            .order_by(KnowledgeIngestionJob.available_at, KnowledgeIngestionJob.created_at)
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        if job is None:
            return None
        job.status = "running"
        job.attempts += 1
        job.locked_at = now
        job.lease_expires_at = now + timedelta(seconds=settings.knowledge_worker_lease_seconds)
        job.worker_id = WORKER_ID
        job.last_error = None
        db.commit()
        return str(job.id)


def _extend_job_lease(job_id) -> None:
    settings = get_settings()
    with SessionLocal() as db:
        job = db.get(KnowledgeIngestionJob, job_id)
        if job is None or job.status != "running" or job.worker_id != WORKER_ID:
            return
        job.lease_expires_at = datetime.now(UTC) + timedelta(
            seconds=settings.knowledge_worker_lease_seconds
        )
        db.commit()


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
    db.commit()
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


def _process_ingest(db, job: KnowledgeIngestionJob, document: KnowledgeDocument, kb: KnowledgeBase) -> None:
    started = time.monotonic()
    settings = get_settings()
    if document.source_type == "url" and document.sha256 is None:
        body, response_type, filename = _fetch_url(document.source_url or "")
        mime_type = validate_document_bytes(filename, response_type, body)
        digest = hashlib.sha256(body).hexdigest()
        duplicate = db.scalar(
            select(KnowledgeDocument.id).where(
                KnowledgeDocument.tenant_id == document.tenant_id,
                KnowledgeDocument.knowledge_base_id == document.knowledge_base_id,
                KnowledgeDocument.sha256 == digest,
                KnowledgeDocument.id != document.id,
                KnowledgeDocument.status != "deleted",
            ).limit(1)
        )
        if duplicate is not None:
            raise ValueError("duplicate_document_content")
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
    batch = embed_texts(
        embedding_config,
        [item["content"] for item in chunks],
        on_batch=lambda: _extend_job_lease(job.id),
    )
    if len(batch.vectors) != len(chunks):
        raise ValueError("embedding_count_mismatch")

    # Serialize the final index switch with delete requests. If deletion won the
    # race while embeddings were being generated, discard this batch.
    latest_document = db.scalar(
        select(KnowledgeDocument)
        .where(
            KnowledgeDocument.id == document.id,
            KnowledgeDocument.tenant_id == job.tenant_id,
            KnowledgeDocument.knowledge_base_id == job.knowledge_base_id,
        )
        .with_for_update()
    )
    if latest_document is None or latest_document.status == "deleting":
        job.status = "cancelled"
        job.worker_id = None
        job.lease_expires_at = None
        db.commit()
        return
    document = latest_document

    db.query(KnowledgeChunk).filter(
        KnowledgeChunk.tenant_id == job.tenant_id,
        KnowledgeChunk.knowledge_base_id == job.knowledge_base_id,
        KnowledgeChunk.document_id == document.id,
        KnowledgeChunk.index_version == job.index_version,
    ).delete(synchronize_session=False)
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
    db.add_all(records)
    db.flush()
    db.query(KnowledgeChunk).filter(
        KnowledgeChunk.tenant_id == job.tenant_id,
        KnowledgeChunk.knowledge_base_id == job.knowledge_base_id,
        KnowledgeChunk.document_id == document.id,
        KnowledgeChunk.index_version != job.index_version,
    ).delete(synchronize_session=False)

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


def _handle_failure(job_id: str, error: Exception) -> None:
    settings = get_settings()
    with SessionLocal() as db:
        job = db.get(KnowledgeIngestionJob, job_id)
        if job is None:
            return
        document = db.get(KnowledgeDocument, job.document_id)
        message = str(error).splitlines()[0][:500] or error.__class__.__name__
        final = job.attempts >= job.max_attempts or isinstance(error, ValueError)
        job.status = "dead_letter" if final else "retry_wait"
        job.last_error = message
        job.worker_id = None
        job.lease_expires_at = None
        if not final:
            delay = min(300, 2 ** min(job.attempts, 8))
            job.available_at = datetime.now(UTC) + timedelta(seconds=delay)
        if document is not None and job.job_type != "delete":
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
            job_id=job_id,
            tenant_id=str(job.tenant_id),
            knowledge_base_id=str(job.knowledge_base_id),
            attempts=job.attempts,
            final=final,
            error_type=error.__class__.__name__,
        )


def process_job(job_id: str) -> None:
    try:
        with SessionLocal() as db:
            job = db.get(KnowledgeIngestionJob, job_id)
            if job is None or job.status != "running" or job.worker_id != WORKER_ID:
                return
            document = db.get(KnowledgeDocument, job.document_id)
            kb = db.get(KnowledgeBase, job.knowledge_base_id)
            if document is None or kb is None:
                raise ValueError("knowledge_resource_not_found")
            if document.status == "deleting" and job.job_type != "delete":
                job.status = "cancelled"
                job.worker_id = None
                job.lease_expires_at = None
                db.commit()
                return
            if job.job_type == "delete":
                _process_delete(db, job, document)
            else:
                if job.job_type != "reindex" or document.active_index_version == 0:
                    document.status = "processing"
                    db.commit()
                _process_ingest(db, job, document, kb)
    except Exception as exc:
        _handle_failure(job_id, exc)


def run_forever() -> None:
    settings = get_settings()
    logger.info("knowledge_worker_started", worker_id=WORKER_ID)
    while True:
        job_id = _claim_job()
        if job_id is None:
            time.sleep(settings.knowledge_worker_poll_seconds)
            continue
        process_job(job_id)


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
