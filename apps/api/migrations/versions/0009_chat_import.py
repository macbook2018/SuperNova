"""Text-only chat import analysis foundation.

Revision ID: 0009_chat_import
Revises: 0008_phase_6_knowledge
Create Date: 2026-11-13
"""

import sqlalchemy as sa
from alembic import op

revision = "0009_chat_import"
down_revision = "0008_phase_6_knowledge"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "chat_sources",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("tenant_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("platform", sa.String(length=32), nullable=False, server_default="wechat"),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("export_tool", sa.String(length=120), nullable=True),
        sa.Column("target_sender_ref", sa.String(length=255), nullable=True),
        sa.Column("target_sender_name", sa.String(length=160), nullable=True),
        sa.Column("identity_confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="active"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "status IN ('active', 'deleting', 'deleted')",
            name="ck_chat_sources_status",
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_chat_sources_tenant_id"),
    )
    op.create_index(
        "ix_chat_sources_tenant_created", "chat_sources", ["tenant_id", "created_at"]
    )

    op.create_table(
        "chat_import_jobs",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("tenant_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("source_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=True),
        sa.Column("status", sa.String(length=24), nullable=False, server_default="queued"),
        sa.Column("progress", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_files", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("processed_files", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("parsed_text_messages", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column(
            "ignored_non_text_messages", sa.BigInteger(), nullable=False, server_default="0"
        ),
        sa.Column("unique_senders", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_summary", sa.String(length=1000), nullable=True),
        sa.Column("locked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("worker_id", sa.String(length=120), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="5"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "status IN ('queued', 'processing', 'awaiting_identity', 'ready_for_analysis', "
            "'succeeded', 'failed', 'cancelled')",
            name="ck_chat_import_jobs_status",
        ),
        sa.CheckConstraint("progress BETWEEN 0 AND 100", name="ck_chat_import_jobs_progress"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "source_id"],
            ["chat_sources.tenant_id", "chat_sources.id"],
            name="fk_chat_import_jobs_source_same_tenant",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_chat_import_jobs_tenant_id"),
        sa.UniqueConstraint(
            "tenant_id", "source_id", "id",
            name="uq_chat_import_jobs_tenant_source_id",
        ),
        sa.UniqueConstraint(
            "tenant_id", "idempotency_key", name="uq_chat_import_jobs_idempotency"
        ),
    )
    op.create_index(
        "ix_chat_import_jobs_claim", "chat_import_jobs", ["status", "created_at"]
    )
    op.create_index(
        "ix_chat_import_jobs_tenant_created",
        "chat_import_jobs",
        ["tenant_id", "created_at"],
    )

    op.create_table(
        "chat_import_files",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("tenant_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("source_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("job_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("original_filename", sa.String(length=255), nullable=False),
        sa.Column("extension", sa.String(length=12), nullable=False),
        sa.Column("mime_type", sa.String(length=160), nullable=False),
        sa.Column("file_size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("object_key", sa.String(length=512), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="uploaded"),
        sa.Column(
            "parsed_text_message_count", sa.BigInteger(), nullable=False, server_default="0"
        ),
        sa.Column(
            "ignored_non_text_message_count", sa.BigInteger(), nullable=False, server_default="0"
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("error_code", sa.String(length=80), nullable=True),
        sa.Column("error_message", sa.String(length=500), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "status IN ('uploaded', 'processing', 'parsed', 'failed')",
            name="ck_chat_import_files_status",
        ),
        sa.CheckConstraint("file_size_bytes > 0", name="ck_chat_import_files_size"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "source_id"],
            ["chat_sources.tenant_id", "chat_sources.id"],
            name="fk_chat_import_files_source_same_tenant",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "source_id", "job_id"],
            ["chat_import_jobs.tenant_id", "chat_import_jobs.source_id", "chat_import_jobs.id"],
            name="fk_chat_import_files_job_same_source",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_chat_import_files_tenant_id"),
        sa.UniqueConstraint(
            "tenant_id", "source_id", "id",
            name="uq_chat_import_files_tenant_source_id",
        ),
        sa.UniqueConstraint(
            "tenant_id", "source_id", "sha256",
            name="uq_chat_import_files_source_sha256",
        ),
        sa.UniqueConstraint("object_key", name="uq_chat_import_files_object_key"),
    )
    op.create_index(
        "ix_chat_import_files_job",
        "chat_import_files",
        ["tenant_id", "job_id", "status"],
    )
    op.create_index("ix_chat_import_files_expiry", "chat_import_files", ["expires_at"])

    op.create_table(
        "chat_import_sender_summaries",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("tenant_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("source_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("job_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("sender_ref", sa.String(length=255), nullable=False),
        sa.Column("sender_name", sa.String(length=160), nullable=False),
        sa.Column("message_count", sa.BigInteger(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("message_count > 0", name="ck_chat_import_sender_summaries_count"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "source_id"],
            ["chat_sources.tenant_id", "chat_sources.id"],
            name="fk_chat_import_sender_summaries_source_same_tenant",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "source_id", "job_id"],
            ["chat_import_jobs.tenant_id", "chat_import_jobs.source_id", "chat_import_jobs.id"],
            name="fk_chat_import_sender_summaries_job_same_source",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id", "job_id", "sender_ref",
            name="uq_chat_import_sender_summaries_job_sender",
        ),
    )
    op.create_index(
        "ix_chat_import_sender_summaries_source",
        "chat_import_sender_summaries",
        ["tenant_id", "source_id", "sender_ref"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_chat_import_sender_summaries_source",
        table_name="chat_import_sender_summaries",
    )
    op.drop_table("chat_import_sender_summaries")
    op.drop_index("ix_chat_import_files_expiry", table_name="chat_import_files")
    op.drop_index("ix_chat_import_files_job", table_name="chat_import_files")
    op.drop_table("chat_import_files")
    op.drop_index("ix_chat_import_jobs_tenant_created", table_name="chat_import_jobs")
    op.drop_index("ix_chat_import_jobs_claim", table_name="chat_import_jobs")
    op.drop_table("chat_import_jobs")
    op.drop_index("ix_chat_sources_tenant_created", table_name="chat_sources")
    op.drop_table("chat_sources")
