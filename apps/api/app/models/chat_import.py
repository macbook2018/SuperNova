from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class ChatSource(Base):
    """A tenant-owned, independent source-account identity.

    The primary key is the SuperNova-generated source_account_id. Two rows are
    always independent, even when platform sender IDs or display names match.
    """

    __tablename__ = "chat_sources"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_chat_sources_tenant_id"),
        CheckConstraint(
            "status IN ('active', 'deleting', 'deleted')",
            name="ck_chat_sources_status",
        ),
        Index("ix_chat_sources_tenant_created", "tenant_id", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    platform: Mapped[str] = mapped_column(String(32), nullable=False, default="wechat")
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    export_tool: Mapped[str | None] = mapped_column(String(120))
    target_sender_ref: Mapped[str | None] = mapped_column(String(255))
    target_sender_name: Mapped[str | None] = mapped_column(String(160))
    identity_confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class ChatImportJob(Base):
    __tablename__ = "chat_import_jobs"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_chat_import_jobs_tenant_id"),
        UniqueConstraint(
            "tenant_id", "source_id", "id",
            name="uq_chat_import_jobs_tenant_source_id",
        ),
        UniqueConstraint("tenant_id", "idempotency_key", name="uq_chat_import_jobs_idempotency"),
        ForeignKeyConstraint(
            ["tenant_id", "source_id"],
            ["chat_sources.tenant_id", "chat_sources.id"],
            name="fk_chat_import_jobs_source_same_tenant",
            ondelete="CASCADE",
        ),
        CheckConstraint(
            "status IN ('queued', 'processing', 'awaiting_identity', 'ready_for_analysis', "
            "'succeeded', 'failed', 'cancelled')",
            name="ck_chat_import_jobs_status",
        ),
        CheckConstraint("progress BETWEEN 0 AND 100", name="ck_chat_import_jobs_progress"),
        Index("ix_chat_import_jobs_claim", "status", "created_at"),
        Index("ix_chat_import_jobs_tenant_created", "tenant_id", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    source_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    idempotency_key: Mapped[str | None] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="queued")
    progress: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_files: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    processed_files: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    parsed_text_messages: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    ignored_non_text_messages: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    unique_senders: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_summary: Mapped[str | None] = mapped_column(String(1000))
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    worker_id: Mapped[str | None] = mapped_column(String(120))
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=5)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class ChatImportFile(Base):
    """Temporary source upload. It is deleted after analysis or when its TTL expires."""

    __tablename__ = "chat_import_files"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_chat_import_files_tenant_id"),
        UniqueConstraint(
            "tenant_id", "source_id", "id",
            name="uq_chat_import_files_tenant_source_id",
        ),
        UniqueConstraint(
            "tenant_id", "source_id", "sha256",
            name="uq_chat_import_files_source_sha256",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "source_id"],
            ["chat_sources.tenant_id", "chat_sources.id"],
            name="fk_chat_import_files_source_same_tenant",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "source_id", "job_id"],
            ["chat_import_jobs.tenant_id", "chat_import_jobs.source_id", "chat_import_jobs.id"],
            name="fk_chat_import_files_job_same_source",
            ondelete="CASCADE",
        ),
        CheckConstraint(
            "status IN ('uploaded', 'processing', 'parsed', 'failed')",
            name="ck_chat_import_files_status",
        ),
        CheckConstraint("file_size_bytes > 0", name="ck_chat_import_files_size"),
        Index("ix_chat_import_files_job", "tenant_id", "job_id", "status"),
        Index("ix_chat_import_files_expiry", "expires_at"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    source_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    job_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    extension: Mapped[str] = mapped_column(String(12), nullable=False)
    mime_type: Mapped[str] = mapped_column(String(160), nullable=False)
    file_size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    object_key: Mapped[str] = mapped_column(String(512), nullable=False, unique=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="uploaded")
    parsed_text_message_count: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    ignored_non_text_message_count: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(80))
    error_message: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class ChatImportSenderSummary(Base):
    """Minimal sender counts used only for explicit account-owner confirmation."""

    __tablename__ = "chat_import_sender_summaries"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "job_id", "sender_ref",
            name="uq_chat_import_sender_summaries_job_sender",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "source_id"],
            ["chat_sources.tenant_id", "chat_sources.id"],
            name="fk_chat_import_sender_summaries_source_same_tenant",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "source_id", "job_id"],
            ["chat_import_jobs.tenant_id", "chat_import_jobs.source_id", "chat_import_jobs.id"],
            name="fk_chat_import_sender_summaries_job_same_source",
            ondelete="CASCADE",
        ),
        CheckConstraint("message_count > 0", name="ck_chat_import_sender_summaries_count"),
        Index(
            "ix_chat_import_sender_summaries_source",
            "tenant_id", "source_id", "sender_ref",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    source_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    job_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    sender_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    sender_name: Mapped[str] = mapped_column(String(160), nullable=False)
    message_count: Mapped[int] = mapped_column(BigInteger, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
