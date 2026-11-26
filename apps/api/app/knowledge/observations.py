"""Durable per-provider-request telemetry, independent of index publication and billing."""

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from uuid import UUID, uuid4

import httpx
from sqlalchemy import update
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.orm import Session
from structlog.contextvars import get_contextvars

from app.db.session import observation_bind
from app.models.knowledge import KnowledgeIndexObservation


@dataclass(frozen=True)
class _CallScope:
    bind: Engine | Connection
    tenant_id: UUID
    knowledge_base_id: UUID
    operation: str
    request_id: str | None
    release_id: UUID | None
    document_id: UUID | None
    job_id: UUID | None


_scope: ContextVar[_CallScope | None] = ContextVar("knowledge_embedding_calls", default=None)


@contextmanager
def observe_embedding_calls(
    db: Session,
    *,
    tenant_id: UUID,
    knowledge_base_id: UUID,
    operation: str,
    request_id: str | None = None,
    release_id: UUID | None = None,
    document_id: UUID | None = None,
    job_id: UUID | None = None,
) -> Iterator[None]:
    scope = _CallScope(
        bind=observation_bind(db),
        tenant_id=tenant_id,
        knowledge_base_id=knowledge_base_id,
        operation=operation,
        request_id=request_id or get_contextvars().get("request_id"),
        release_id=release_id,
        document_id=document_id,
        job_id=job_id,
    )
    token = _scope.set(scope)
    try:
        yield
    finally:
        _scope.reset(token)


@dataclass
class EmbeddingAttempt:
    input_tokens: int | None = None


@contextmanager
def observe_embedding_attempt(model_name: str) -> Iterator[EmbeddingAttempt]:
    attempt = EmbeddingAttempt()
    scope = _scope.get()
    if scope is None:
        # Platform connectivity probes have no tenant/KB owner.
        yield attempt
        return

    attempt_id = uuid4()
    with Session(scope.bind) as db, db.begin():
        db.add(
            KnowledgeIndexObservation(
                id=attempt_id,
                tenant_id=scope.tenant_id,
                knowledge_base_id=scope.knowledge_base_id,
                release_id=scope.release_id,
                document_id=scope.document_id,
                job_id=scope.job_id,
                request_id=scope.request_id,
                operation=scope.operation,
                model_name=model_name,
                status="started",
            )
        )
    status = "unknown"
    try:
        yield attempt
        if attempt.input_tokens is not None:
            status = "succeeded"
    except httpx.TransportError:
        # The provider may already have consumed input when a connection is lost.
        raise
    except Exception:
        status = "failed"
        raise
    finally:
        with Session(scope.bind) as db, db.begin():
            db.execute(
                update(KnowledgeIndexObservation)
                .where(KnowledgeIndexObservation.id == attempt_id)
                .values(status=status, input_tokens=attempt.input_tokens)
            )
