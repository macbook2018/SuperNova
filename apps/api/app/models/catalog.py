from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
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


class ModelProvider(Base):
    __tablename__ = "model_providers"
    __table_args__ = (
        UniqueConstraint("tenant_id", "name", name="uq_model_providers_tenant_name"),
        UniqueConstraint("tenant_id", "id", name="uq_model_providers_tenant_id"),
        Index("ix_model_providers_tenant_created", "tenant_id", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    provider_type: Mapped[str] = mapped_column(String(32), nullable=False)
    base_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    api_key_ciphertext: Mapped[str | None] = mapped_column(Text)
    api_key_last4: Mapped[str | None] = mapped_column(String(4))
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class KnowledgeBase(Base):
    __tablename__ = "knowledge_bases"
    __table_args__ = (
        UniqueConstraint("tenant_id", "name", name="uq_knowledge_bases_tenant_name"),
        UniqueConstraint("tenant_id", "id", name="uq_knowledge_bases_tenant_id"),
        ForeignKeyConstraint(
            ["tenant_id", "embedding_provider_id"],
            ["model_providers.tenant_id", "model_providers.id"],
            name="fk_knowledge_bases_embedding_provider_same_tenant",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "embedding_source IN ('platform', 'byok')",
            name="ck_knowledge_bases_embedding_source",
        ),
        CheckConstraint(
            "(embedding_source = 'platform' AND embedding_provider_id IS NULL) "
            "OR (embedding_source = 'byok' AND platform_embedding_model_id IS NULL)",
            name="ck_knowledge_bases_embedding_source_reference",
        ),
        CheckConstraint(
            "embedding_dimensions = 1536",
            name="ck_knowledge_bases_embedding_dimensions",
        ),
        CheckConstraint(
            "chunk_size BETWEEN 100 AND 4000 AND chunk_overlap >= 0 AND chunk_overlap < chunk_size",
            name="ck_knowledge_bases_chunk_settings",
        ),
        CheckConstraint(
            "retrieval_top_k BETWEEN 1 AND 20",
            name="ck_knowledge_bases_retrieval_top_k",
        ),
        Index("ix_knowledge_bases_tenant_created", "tenant_id", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    embedding_source: Mapped[str] = mapped_column(
        String(16), nullable=False, default="platform", server_default="platform"
    )
    platform_embedding_model_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("platform_embedding_models.id", ondelete="RESTRICT"),
    )
    embedding_provider_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True))
    embedding_model_name: Mapped[str | None] = mapped_column(String(120))
    embedding_dimensions: Mapped[int] = mapped_column(Integer, nullable=False, default=1536, server_default="1536")
    chunk_size: Mapped[int] = mapped_column(Integer, nullable=False, default=800, server_default="800")
    chunk_overlap: Mapped[int] = mapped_column(Integer, nullable=False, default=120, server_default="120")
    retrieval_top_k: Mapped[int] = mapped_column(Integer, nullable=False, default=5, server_default="5")
    retrieval_score_threshold: Mapped[float] = mapped_column(Float, nullable=False, default=0.15, server_default="0.15")
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class Persona(Base):
    __tablename__ = "personas"
    __table_args__ = (
        UniqueConstraint("tenant_id", "slug", name="uq_personas_tenant_slug"),
        UniqueConstraint("tenant_id", "id", name="uq_personas_tenant_id"),
        CheckConstraint("model_source IN ('platform', 'byok')", name="ck_personas_model_source"),
        CheckConstraint(
            "(model_source = 'platform' AND platform_model_id IS NOT NULL AND provider_id IS NULL) "
            "OR (model_source = 'byok' AND platform_model_id IS NULL)",
            name="ck_personas_model_source_reference",
        ),
        Index("ix_personas_platform_model", "platform_model_id"),
        ForeignKeyConstraint(
            ["tenant_id", "provider_id"],
            ["model_providers.tenant_id", "model_providers.id"],
            name="fk_personas_provider_same_tenant",
            ondelete="RESTRICT",
        ),
        Index("ix_personas_tenant_enabled", "tenant_id", "enabled"),
        Index("ix_personas_tenant_created", "tenant_id", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    slug: Mapped[str] = mapped_column(String(80), nullable=False)
    description: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    system_prompt: Mapped[str] = mapped_column(Text, nullable=False)
    provider_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True))
    # Historic personas keep their BYOK provider configuration unchanged.
    model_source: Mapped[str] = mapped_column(
        String(16), nullable=False, default="byok", server_default="byok"
    )
    platform_model_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("platform_models.id", ondelete="RESTRICT", name="fk_personas_platform_model"),
    )
    model_name: Mapped[str] = mapped_column(String(120), nullable=False)
    temperature: Mapped[float] = mapped_column(Float, nullable=False, default=0.7)
    top_p: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    max_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=2048)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class PersonaKnowledgeBase(Base):
    __tablename__ = "persona_knowledge_bases"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "persona_id"],
            ["personas.tenant_id", "personas.id"],
            name="fk_persona_knowledge_persona_same_tenant",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "knowledge_base_id"],
            ["knowledge_bases.tenant_id", "knowledge_bases.id"],
            name="fk_persona_knowledge_base_same_tenant",
            ondelete="CASCADE",
        ),
        Index("ix_persona_knowledge_bases_knowledge_base", "tenant_id", "knowledge_base_id"),
    )

    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), primary_key=True
    )
    persona_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    knowledge_base_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
