from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, String, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class UsageRecord(Base):
    __tablename__ = "usage_records"
    __table_args__ = (
        CheckConstraint(
            "model_source IN ('platform', 'byok')",
            name="ck_usage_records_model_source",
        ),
        Index("ix_usage_records_tenant_created", "tenant_id", "created_at"),
        Index("ix_usage_records_user_created", "user_id", "created_at"),
        Index("ix_usage_records_request_id", "request_id"),
        Index("ix_usage_records_tenant_source_created", "tenant_id", "model_source", "created_at"),
        Index("ix_usage_records_bot_created", "tenant_id", "bot_instance_id", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    request_id: Mapped[str] = mapped_column(String(36), nullable=False)
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    api_key_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("api_keys.id", ondelete="SET NULL")
    )
    bot_instance_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("bot_instances.id", ondelete="SET NULL")
    )
    persona_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("personas.id", ondelete="SET NULL")
    )
    provider_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("model_providers.id", ondelete="SET NULL")
    )
    platform_model_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("platform_models.id", ondelete="SET NULL")
    )
    model_source: Mapped[str] = mapped_column(
        String(16), nullable=False, default="byok", server_default="byok"
    )
    persona_slug: Mapped[str] = mapped_column(String(80), nullable=False)
    provider_type: Mapped[str] = mapped_column(String(32), nullable=False)
    model_name: Mapped[str] = mapped_column(String(120), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    prompt_tokens: Mapped[int | None] = mapped_column(Integer)
    completion_tokens: Mapped[int | None] = mapped_column(Integer)
    total_tokens: Mapped[int | None] = mapped_column(Integer)
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
