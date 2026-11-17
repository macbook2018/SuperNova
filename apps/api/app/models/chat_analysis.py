from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    Float,
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


class ChatAnalysisJob(Base):
    __tablename__ = "chat_analysis_jobs"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_chat_analysis_jobs_tenant_id"),
        UniqueConstraint(
            "tenant_id",
            "source_id",
            "id",
            name="uq_chat_analysis_jobs_tenant_source_id",
        ),
        UniqueConstraint(
            "tenant_id",
            "source_id",
            "import_job_id",
            name="uq_chat_analysis_jobs_import",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "source_id"],
            ["chat_sources.tenant_id", "chat_sources.id"],
            name="fk_chat_analysis_jobs_source_same_tenant",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "source_id", "import_job_id"],
            ["chat_import_jobs.tenant_id", "chat_import_jobs.source_id", "chat_import_jobs.id"],
            name="fk_chat_analysis_jobs_import_same_source",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "provider_id"],
            ["model_providers.tenant_id", "model_providers.id"],
            name="fk_chat_analysis_jobs_provider_same_tenant",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["platform_model_id"],
            ["platform_models.id"],
            name="fk_chat_analysis_jobs_platform_model",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "model_source IN ('platform', 'byok')",
            name="ck_chat_analysis_jobs_model_source",
        ),
        CheckConstraint(
            "(model_source = 'platform' AND platform_model_id IS NOT NULL AND provider_id IS NULL) "
            "OR (model_source = 'byok' AND provider_id IS NOT NULL AND platform_model_id IS NULL)",
            name="ck_chat_analysis_jobs_model_reference",
        ),
        CheckConstraint(
            "status IN ('queued', 'running', 'retry_wait', 'succeeded', 'failed', 'cancelled')",
            name="ck_chat_analysis_jobs_status",
        ),
        CheckConstraint(
            "attempts >= 0 AND max_attempts > 0",
            name="ck_chat_analysis_jobs_attempts",
        ),
        Index(
            "ix_chat_analysis_jobs_claim",
            "status",
            "available_at",
            "created_at",
        ),
        Index(
            "ix_chat_analysis_jobs_source",
            "tenant_id",
            "source_id",
            "created_at",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    source_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    import_job_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    owner_sender_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    model_source: Mapped[str] = mapped_column(String(16), nullable=False)
    provider_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True))
    platform_model_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True))
    model_name: Mapped[str] = mapped_column(String(120), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="queued")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    worker_id: Mapped[str | None] = mapped_column(String(120))
    last_error: Mapped[str | None] = mapped_column(String(500))
    owner_message_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    faq_candidate_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    request_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class PersonaDraft(Base):
    __tablename__ = "persona_drafts"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_persona_drafts_tenant_id"),
        UniqueConstraint(
            "tenant_id",
            "source_id",
            "analysis_job_id",
            name="uq_persona_drafts_analysis_job",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "source_id"],
            ["chat_sources.tenant_id", "chat_sources.id"],
            name="fk_persona_drafts_source_same_tenant",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "source_id", "analysis_job_id"],
            [
                "chat_analysis_jobs.tenant_id",
                "chat_analysis_jobs.source_id",
                "chat_analysis_jobs.id",
            ],
            name="fk_persona_drafts_analysis_job_same_source",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "applied_persona_id"],
            ["personas.tenant_id", "personas.id"],
            name="fk_persona_drafts_applied_persona_same_tenant",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "status IN ('pending_review', 'approved', 'rejected', 'applied')",
            name="ck_persona_drafts_status",
        ),
        CheckConstraint("evidence_message_count >= 0", name="ck_persona_drafts_evidence_count"),
        Index(
            "ix_persona_drafts_review",
            "tenant_id",
            "source_id",
            "status",
            "created_at",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    source_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    analysis_job_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending_review")
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    profile_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    system_prompt_suggestion: Mapped[str] = mapped_column(Text, nullable=False)
    evidence_message_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    applied_persona_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    rejected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    applied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class FaqCandidate(Base):
    __tablename__ = "faq_candidates"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_faq_candidates_tenant_id"),
        UniqueConstraint(
            "tenant_id",
            "analysis_job_id",
            "fingerprint",
            name="uq_faq_candidates_analysis_fingerprint",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "source_id"],
            ["chat_sources.tenant_id", "chat_sources.id"],
            name="fk_faq_candidates_source_same_tenant",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "source_id", "analysis_job_id"],
            [
                "chat_analysis_jobs.tenant_id",
                "chat_analysis_jobs.source_id",
                "chat_analysis_jobs.id",
            ],
            name="fk_faq_candidates_analysis_job_same_source",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "published_document_id"],
            ["knowledge_documents.tenant_id", "knowledge_documents.id"],
            name="fk_faq_candidates_published_document_same_tenant",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "status IN ('pending_review', 'approved', 'rejected', 'published')",
            name="ck_faq_candidates_status",
        ),
        CheckConstraint("confidence >= 0 AND confidence <= 1", name="ck_faq_candidates_confidence"),
        CheckConstraint("occurrence_count > 0", name="ck_faq_candidates_occurrences"),
        Index(
            "ix_faq_candidates_review",
            "tenant_id",
            "source_id",
            "status",
            "created_at",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    source_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    analysis_job_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending_review")
    title: Mapped[str] = mapped_column(String(160), nullable=False)
    category: Mapped[str] = mapped_column(String(60), nullable=False, default="general")
    canonical_question: Mapped[str] = mapped_column(Text, nullable=False)
    question_variants: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    answer: Mapped[str] = mapped_column(Text, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.5)
    occurrence_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    effective_date: Mapped[str | None] = mapped_column(String(32))
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    published_document_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    rejected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class ChatAnalysisReviewEvent(Base):
    __tablename__ = "chat_analysis_review_events"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "source_id"],
            ["chat_sources.tenant_id", "chat_sources.id"],
            name="fk_chat_analysis_review_events_source_same_tenant",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["actor_user_id"],
            ["users.id"],
            name="fk_chat_analysis_review_events_actor",
            ondelete="SET NULL",
        ),
        CheckConstraint(
            "entity_type IN ('persona_draft', 'faq_candidate')",
            name="ck_chat_analysis_review_events_entity_type",
        ),
        CheckConstraint(
            "action IN ('edited', 'approved', 'rejected', 'applied', 'published')",
            name="ck_chat_analysis_review_events_action",
        ),
        Index(
            "ix_chat_analysis_review_events_entity",
            "tenant_id",
            "entity_type",
            "entity_id",
            "created_at",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    source_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    actor_user_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True))
    entity_type: Mapped[str] = mapped_column(String(24), nullable=False)
    entity_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    action: Mapped[str] = mapped_column(String(16), nullable=False)
    before_hash: Mapped[str | None] = mapped_column(String(64))
    after_hash: Mapped[str | None] = mapped_column(String(64))
    note: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
