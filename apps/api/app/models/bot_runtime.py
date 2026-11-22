from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    JSON,
    String,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class BotInstance(Base):
    __tablename__ = "bot_instances"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_bot_instances_tenant_id"),
        UniqueConstraint(
            "tenant_id", "id", "persona_id", name="uq_bot_instances_tenant_id_persona"
        ),
        UniqueConstraint(
            "runtime_provider", "runtime_resource_id",
            name="uq_bot_instances_runtime_resource",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "persona_id"],
            ["personas.tenant_id", "personas.id"],
            name="fk_bot_instances_persona_same_tenant",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "created_by_user_id"],
            ["tenant_memberships.tenant_id", "tenant_memberships.user_id"],
            name="fk_bot_instances_creator_membership_same_tenant",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "desired_state IN ('running', 'stopped', 'deleting')",
            name="ck_bot_instances_desired_state",
        ),
        CheckConstraint(
            "actual_state IN ('pending', 'provisioning', 'starting', 'running', "
            "'stopping', 'stopped', 'unhealthy', 'failed', 'deleting')",
            name="ck_bot_instances_actual_state",
        ),
        CheckConstraint("config_version > 0", name="ck_bot_instances_config_version"),
        CheckConstraint(
            "runtime_provider IN ('docker', 'kubernetes')",
            name="ck_bot_instances_runtime_provider",
        ),
        Index("ix_bot_instances_tenant_created", "tenant_id", "created_at"),
        Index(
            "ix_bot_instances_runtime_state",
            "runtime_provider", "desired_state", "actual_state",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    persona_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    created_by_user_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    runtime_version: Mapped[str] = mapped_column(
        String(100), nullable=False, default="4.29.4", server_default="4.29.4"
    )
    desired_state: Mapped[str] = mapped_column(
        String(16), nullable=False, default="stopped", server_default="stopped"
    )
    actual_state: Mapped[str] = mapped_column(
        String(20), nullable=False, default="pending", server_default="pending"
    )
    config_version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default="1"
    )
    runtime_provider: Mapped[str] = mapped_column(
        String(32), nullable=False, default="docker", server_default="docker"
    )
    runtime_resource_id: Mapped[str | None] = mapped_column(String(255))
    last_heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error_code: Mapped[str | None] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class BotRuntimeCommand(Base):
    __tablename__ = "bot_runtime_commands"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_bot_runtime_commands_tenant_id"),
        UniqueConstraint(
            "tenant_id", "idempotency_key", name="uq_bot_runtime_commands_idempotency"
        ),
        ForeignKeyConstraint(
            ["tenant_id", "bot_instance_id"],
            ["bot_instances.tenant_id", "bot_instances.id"],
            name="fk_bot_runtime_commands_bot_same_tenant",
            ondelete="CASCADE",
        ),
        CheckConstraint(
            "command IN ('start', 'stop', 'restart', 'delete', 'update_config')",
            name="ck_bot_runtime_commands_command",
        ),
        CheckConstraint(
            "status IN ('queued', 'leased', 'succeeded', 'failed', 'cancelled')",
            name="ck_bot_runtime_commands_status",
        ),
        CheckConstraint(
            "attempts >= 0 AND max_attempts > 0 AND attempts <= max_attempts",
            name="ck_bot_runtime_commands_attempts",
        ),
        Index("ix_bot_runtime_commands_claim", "status", "available_at", "created_at"),
        Index(
            "ix_bot_runtime_commands_bot",
            "tenant_id", "bot_instance_id", "created_at",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    bot_instance_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    command: Mapped[str] = mapped_column(String(24), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="queued", server_default="queued"
    )
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    max_attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, default=5, server_default="5"
    )
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    worker_id: Mapped[str | None] = mapped_column(String(120))
    error_code: Mapped[str | None] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class BotRuntimeEvent(Base):
    __tablename__ = "bot_runtime_events"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_bot_runtime_events_tenant_id"),
        ForeignKeyConstraint(
            ["tenant_id", "bot_instance_id"],
            ["bot_instances.tenant_id", "bot_instances.id"],
            name="fk_bot_runtime_events_bot_same_tenant",
            ondelete="CASCADE",
        ),
        CheckConstraint("schema_version > 0", name="ck_bot_runtime_events_schema_version"),
        Index(
            "ix_bot_runtime_events_timeline",
            "tenant_id", "bot_instance_id", "created_at", "id",
        ),
        Index(
            "ix_bot_runtime_events_type",
            "tenant_id", "bot_instance_id", "event_type", "created_at",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    bot_instance_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    schema_version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default="1"
    )
    channel_account_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True))
    source_account_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True))
    platform: Mapped[str] = mapped_column(
        String(32), nullable=False, default="system", server_default="system"
    )
    correlation_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    payload_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class BotRuntimeCredential(Base):
    __tablename__ = "bot_runtime_credentials"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_bot_runtime_credentials_tenant_id"),
        UniqueConstraint("jti", name="uq_bot_runtime_credentials_jti"),
        UniqueConstraint("token_hash", name="uq_bot_runtime_credentials_token_hash"),
        ForeignKeyConstraint(
            ["tenant_id", "bot_instance_id", "persona_id"],
            ["bot_instances.tenant_id", "bot_instances.id", "bot_instances.persona_id"],
            name="fk_bot_runtime_credentials_bot_persona_same_tenant",
            ondelete="CASCADE",
        ),
        CheckConstraint(
            "scope IN ('chat.completions', 'runtime.heartbeat', 'runtime.events')",
            name="ck_bot_runtime_credentials_scope",
        ),
        CheckConstraint(
            "config_version > 0", name="ck_bot_runtime_credentials_config_version"
        ),
        Index(
            "ix_bot_runtime_credentials_scope_expiry",
            "tenant_id", "bot_instance_id", "scope", "expires_at",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    bot_instance_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    persona_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    jti: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False, default=uuid4)
    config_version: Mapped[int] = mapped_column(Integer, nullable=False)
    scope: Mapped[str] = mapped_column(String(64), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
