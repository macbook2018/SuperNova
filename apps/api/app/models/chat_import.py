from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    JSON,
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class ChatSource(Base):
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
            "status IN ('queued', 'processing', 'awaiting_identity', "
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
    parsed_messages: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    imported_messages: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    duplicate_messages: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
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
            "status IN ('uploaded', 'processing', 'parsed', 'failed', 'deleted')",
            name="ck_chat_import_files_status",
        ),
        CheckConstraint("file_size_bytes > 0", name="ck_chat_import_files_size"),
        Index("ix_chat_import_files_job", "tenant_id", "job_id", "status"),
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
    parsed_message_count: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    error_code: Mapped[str | None] = mapped_column(String(80))
    error_message: Mapped[str | None] = mapped_column(String(500))
    metadata_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class ChatConversation(Base):
    __tablename__ = "chat_conversations"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_chat_conversations_tenant_id"),
        UniqueConstraint(
            "tenant_id", "source_id", "id",
            name="uq_chat_conversations_tenant_source_id",
        ),
        UniqueConstraint(
            "tenant_id", "source_id", "external_conversation_id",
            name="uq_chat_conversations_external_id",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "source_id"],
            ["chat_sources.tenant_id", "chat_sources.id"],
            name="fk_chat_conversations_source_same_tenant",
            ondelete="CASCADE",
        ),
        CheckConstraint(
            "conversation_type IN ('direct', 'group', 'unknown')",
            name="ck_chat_conversations_type",
        ),
        Index("ix_chat_conversations_tenant_source", "tenant_id", "source_id"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    source_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    external_conversation_id: Mapped[str] = mapped_column(String(255), nullable=False)
    title: Mapped[str | None] = mapped_column(String(255))
    conversation_type: Mapped[str] = mapped_column(String(16), nullable=False, default="unknown")
    first_message_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_message_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    message_count: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class ChatParticipant(Base):
    __tablename__ = "chat_participants"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_chat_participants_tenant_id"),
        UniqueConstraint(
            "tenant_id", "source_id", "conversation_id", "sender_ref",
            name="uq_chat_participants_sender",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "source_id", "conversation_id"],
            [
                "chat_conversations.tenant_id",
                "chat_conversations.source_id",
                "chat_conversations.id",
            ],
            name="fk_chat_participants_conversation_same_tenant",
            ondelete="CASCADE",
        ),
        CheckConstraint(
            "identity_role IN ('unknown', 'target', 'other')",
            name="ck_chat_participants_identity_role",
        ),
        Index("ix_chat_participants_sender", "tenant_id", "source_id", "sender_ref"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    source_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    conversation_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    sender_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    sender_name: Mapped[str] = mapped_column(String(160), nullable=False)
    identity_role: Mapped[str] = mapped_column(String(16), nullable=False, default="unknown")
    message_count: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class ChatMessage(Base):
    __tablename__ = "chat_messages"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "source_id", "fingerprint",
            name="uq_chat_messages_source_fingerprint",
        ),
        UniqueConstraint(
            "tenant_id", "source_id", "conversation_id", "external_message_id",
            name="uq_chat_messages_external_id",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "source_id", "conversation_id"],
            [
                "chat_conversations.tenant_id",
                "chat_conversations.source_id",
                "chat_conversations.id",
            ],
            name="fk_chat_messages_conversation_same_tenant",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "source_id", "import_file_id"],
            ["chat_import_files.tenant_id", "chat_import_files.source_id", "chat_import_files.id"],
            name="fk_chat_messages_file_same_tenant",
            ondelete="CASCADE",
        ),
        CheckConstraint(
            "identity_role IN ('unknown', 'target', 'other')",
            name="ck_chat_messages_identity_role",
        ),
        Index(
            "ix_chat_messages_conversation_time",
            "tenant_id",
            "source_id",
            "conversation_id",
            "timestamp",
        ),
        Index("ix_chat_messages_sender_time", "tenant_id", "source_id", "sender_ref", "timestamp"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    source_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    conversation_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    import_file_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    external_message_id: Mapped[str | None] = mapped_column(String(255))
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    sender_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    sender_name: Mapped[str] = mapped_column(String(160), nullable=False)
    receiver_ref: Mapped[str | None] = mapped_column(String(255))
    receiver_name: Mapped[str | None] = mapped_column(String(160))
    content: Mapped[str] = mapped_column(Text, nullable=False)
    message_type: Mapped[str] = mapped_column(String(32), nullable=False, default="text")
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    identity_role: Mapped[str] = mapped_column(String(16), nullable=False, default="unknown")
    metadata_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
