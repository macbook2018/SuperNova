"""Persona learning, FAQ extraction and human-reviewed publication.

Revision ID: 0011_chat_analysis_review
Revises: 0010_chat_import_text_only
"""

import sqlalchemy as sa
from alembic import op

revision = "0011_chat_analysis_review"
down_revision = "0010_chat_import_text_only"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "chat_analysis_jobs",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("tenant_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("source_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("import_job_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("owner_sender_ref", sa.String(length=255), nullable=False),
        sa.Column("model_source", sa.String(length=16), nullable=False),
        sa.Column("provider_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("platform_model_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("model_name", sa.String(length=120), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="queued"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="3"),
        sa.Column(
            "available_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("locked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("worker_id", sa.String(length=120), nullable=True),
        sa.Column("last_error", sa.String(length=500), nullable=True),
        sa.Column("owner_message_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("faq_candidate_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("input_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("output_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("request_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "model_source IN ('platform', 'byok')",
            name="ck_chat_analysis_jobs_model_source",
        ),
        sa.CheckConstraint(
            "(model_source = 'platform' AND platform_model_id IS NOT NULL AND provider_id IS NULL) "
            "OR (model_source = 'byok' AND provider_id IS NOT NULL AND platform_model_id IS NULL)",
            name="ck_chat_analysis_jobs_model_reference",
        ),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'retry_wait', 'succeeded', 'failed', 'cancelled')",
            name="ck_chat_analysis_jobs_status",
        ),
        sa.CheckConstraint(
            "attempts >= 0 AND max_attempts > 0",
            name="ck_chat_analysis_jobs_attempts",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "source_id"],
            ["chat_sources.tenant_id", "chat_sources.id"],
            name="fk_chat_analysis_jobs_source_same_tenant",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "source_id", "import_job_id"],
            ["chat_import_jobs.tenant_id", "chat_import_jobs.source_id", "chat_import_jobs.id"],
            name="fk_chat_analysis_jobs_import_same_source",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "provider_id"],
            ["model_providers.tenant_id", "model_providers.id"],
            name="fk_chat_analysis_jobs_provider_same_tenant",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["platform_model_id"],
            ["platform_models.id"],
            name="fk_chat_analysis_jobs_platform_model",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_chat_analysis_jobs_tenant_id"),
        sa.UniqueConstraint(
            "tenant_id", "source_id", "id",
            name="uq_chat_analysis_jobs_tenant_source_id",
        ),
        sa.UniqueConstraint(
            "tenant_id", "source_id", "import_job_id",
            name="uq_chat_analysis_jobs_import",
        ),
    )
    op.create_index(
        "ix_chat_analysis_jobs_claim",
        "chat_analysis_jobs",
        ["status", "available_at", "created_at"],
    )
    op.create_index(
        "ix_chat_analysis_jobs_source",
        "chat_analysis_jobs",
        ["tenant_id", "source_id", "created_at"],
    )

    op.create_table(
        "persona_drafts",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("tenant_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("source_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("analysis_job_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="pending_review"),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("profile_json", sa.JSON(), nullable=False),
        sa.Column("system_prompt_suggestion", sa.Text(), nullable=False),
        sa.Column("evidence_message_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("applied_persona_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rejected_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("applied_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('pending_review', 'approved', 'rejected', 'applied')",
            name="ck_persona_drafts_status",
        ),
        sa.CheckConstraint(
            "evidence_message_count >= 0",
            name="ck_persona_drafts_evidence_count",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "source_id"],
            ["chat_sources.tenant_id", "chat_sources.id"],
            name="fk_persona_drafts_source_same_tenant",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "source_id", "analysis_job_id"],
            [
                "chat_analysis_jobs.tenant_id",
                "chat_analysis_jobs.source_id",
                "chat_analysis_jobs.id",
            ],
            name="fk_persona_drafts_analysis_job_same_source",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "applied_persona_id"],
            ["personas.tenant_id", "personas.id"],
            name="fk_persona_drafts_applied_persona_same_tenant",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_persona_drafts_tenant_id"),
        sa.UniqueConstraint(
            "tenant_id", "source_id", "analysis_job_id",
            name="uq_persona_drafts_analysis_job",
        ),
    )
    op.create_index(
        "ix_persona_drafts_review",
        "persona_drafts",
        ["tenant_id", "source_id", "status", "created_at"],
    )

    op.create_table(
        "faq_candidates",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("tenant_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("source_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("analysis_job_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="pending_review"),
        sa.Column("title", sa.String(length=160), nullable=False),
        sa.Column("category", sa.String(length=60), nullable=False, server_default="general"),
        sa.Column("canonical_question", sa.Text(), nullable=False),
        sa.Column("question_variants", sa.JSON(), nullable=False),
        sa.Column("answer", sa.Text(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False, server_default="0.5"),
        sa.Column("occurrence_count", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("effective_date", sa.String(length=32), nullable=True),
        sa.Column("fingerprint", sa.String(length=64), nullable=False),
        sa.Column("published_document_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rejected_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('pending_review', 'approved', 'rejected', 'published')",
            name="ck_faq_candidates_status",
        ),
        sa.CheckConstraint(
            "confidence >= 0 AND confidence <= 1",
            name="ck_faq_candidates_confidence",
        ),
        sa.CheckConstraint(
            "occurrence_count > 0",
            name="ck_faq_candidates_occurrences",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "source_id"],
            ["chat_sources.tenant_id", "chat_sources.id"],
            name="fk_faq_candidates_source_same_tenant",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "source_id", "analysis_job_id"],
            [
                "chat_analysis_jobs.tenant_id",
                "chat_analysis_jobs.source_id",
                "chat_analysis_jobs.id",
            ],
            name="fk_faq_candidates_analysis_job_same_source",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "published_document_id"],
            ["knowledge_documents.tenant_id", "knowledge_documents.id"],
            name="fk_faq_candidates_published_document_same_tenant",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_faq_candidates_tenant_id"),
        sa.UniqueConstraint(
            "tenant_id", "analysis_job_id", "fingerprint",
            name="uq_faq_candidates_analysis_fingerprint",
        ),
    )
    op.create_index(
        "ix_faq_candidates_review",
        "faq_candidates",
        ["tenant_id", "source_id", "status", "created_at"],
    )

    op.create_table(
        "chat_analysis_review_events",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("tenant_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("source_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("actor_user_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("entity_type", sa.String(length=24), nullable=False),
        sa.Column("entity_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("action", sa.String(length=16), nullable=False),
        sa.Column("before_hash", sa.String(length=64), nullable=True),
        sa.Column("after_hash", sa.String(length=64), nullable=True),
        sa.Column("note", sa.String(length=500), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "entity_type IN ('persona_draft', 'faq_candidate')",
            name="ck_chat_analysis_review_events_entity_type",
        ),
        sa.CheckConstraint(
            "action IN ('edited', 'approved', 'rejected', 'applied', 'published')",
            name="ck_chat_analysis_review_events_action",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "source_id"],
            ["chat_sources.tenant_id", "chat_sources.id"],
            name="fk_chat_analysis_review_events_source_same_tenant",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["actor_user_id"],
            ["users.id"],
            name="fk_chat_analysis_review_events_actor",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_chat_analysis_review_events_entity",
        "chat_analysis_review_events",
        ["tenant_id", "entity_type", "entity_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_chat_analysis_review_events_entity",
        table_name="chat_analysis_review_events",
    )
    op.drop_table("chat_analysis_review_events")
    op.drop_index("ix_faq_candidates_review", table_name="faq_candidates")
    op.drop_table("faq_candidates")
    op.drop_index("ix_persona_drafts_review", table_name="persona_drafts")
    op.drop_table("persona_drafts")
    op.drop_index("ix_chat_analysis_jobs_source", table_name="chat_analysis_jobs")
    op.drop_index("ix_chat_analysis_jobs_claim", table_name="chat_analysis_jobs")
    op.drop_table("chat_analysis_jobs")
