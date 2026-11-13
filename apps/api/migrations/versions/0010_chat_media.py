"""Chat media extraction and private asset storage.

Revision ID: 0010_chat_media
Revises: 0009_chat_import
Create Date: 2026-11-13
"""

import sqlalchemy as sa
from alembic import op

revision = "0010_chat_media"
down_revision = "0009_chat_import"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_unique_constraint(
        "uq_chat_messages_tenant_source_conversation_id",
        "chat_messages",
        ["tenant_id", "source_id", "conversation_id", "id"],
    )
    op.create_unique_constraint(
        "uq_chat_participants_tenant_source_conversation_id",
        "chat_participants",
        ["tenant_id", "source_id", "conversation_id", "id"],
    )

    op.create_table(
        "chat_media_extraction_jobs",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("tenant_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("source_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("import_file_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False, server_default="queued"),
        sa.Column("progress", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_assets", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("extracted_assets", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("missing_assets", sa.Integer(), nullable=False, server_default="0"),
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
            "status IN ('queued', 'processing', 'succeeded', 'partial', 'failed', 'cancelled')",
            name="ck_chat_media_extraction_jobs_status",
        ),
        sa.CheckConstraint(
            "progress BETWEEN 0 AND 100",
            name="ck_chat_media_extraction_jobs_progress",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "source_id"],
            ["chat_sources.tenant_id", "chat_sources.id"],
            name="fk_chat_media_extraction_jobs_source_same_tenant",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "source_id", "import_file_id"],
            [
                "chat_import_files.tenant_id",
                "chat_import_files.source_id",
                "chat_import_files.id",
            ],
            name="fk_chat_media_extraction_jobs_file_same_source",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id", "source_id", "id",
            name="uq_chat_media_extraction_jobs_tenant_source_id",
        ),
        sa.UniqueConstraint(
            "tenant_id", "source_id", "import_file_id",
            name="uq_chat_media_extraction_jobs_source_file",
        ),
    )
    op.create_index(
        "ix_chat_media_extraction_jobs_claim",
        "chat_media_extraction_jobs",
        ["status", "created_at"],
    )
    op.create_index(
        "ix_chat_media_extraction_jobs_tenant_created",
        "chat_media_extraction_jobs",
        ["tenant_id", "created_at"],
    )

    op.create_table(
        "chat_media_assets",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("tenant_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("source_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("conversation_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("import_file_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("message_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("participant_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("asset_type", sa.String(length=16), nullable=False),
        sa.Column("role", sa.String(length=24), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("archive_path", sa.String(length=512), nullable=True),
        sa.Column("original_name", sa.String(length=255), nullable=True),
        sa.Column("mime_type", sa.String(length=160), nullable=True),
        sa.Column("file_size_bytes", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("sha256", sa.String(length=64), nullable=True),
        sa.Column("object_key", sa.String(length=512), nullable=True),
        sa.Column("width", sa.Integer(), nullable=True),
        sa.Column("height", sa.Integer(), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "asset_type IN ('image', 'voice', 'video', 'file', 'avatar', 'emoji', 'other')",
            name="ck_chat_media_assets_type",
        ),
        sa.CheckConstraint(
            "role IN ('original', 'thumbnail', 'preview', 'avatar', 'attachment')",
            name="ck_chat_media_assets_role",
        ),
        sa.CheckConstraint(
            "status IN ('available', 'missing', 'encrypted', 'unsupported', 'corrupt', 'skipped')",
            name="ck_chat_media_assets_status",
        ),
        sa.CheckConstraint(
            "(message_id IS NOT NULL AND participant_id IS NULL) OR "
            "(message_id IS NULL AND participant_id IS NOT NULL)",
            name="ck_chat_media_assets_owner",
        ),
        sa.CheckConstraint(
            "file_size_bytes >= 0",
            name="ck_chat_media_assets_file_size",
        ),
        sa.CheckConstraint(
            "(status = 'available' AND object_key IS NOT NULL AND sha256 IS NOT NULL) OR "
            "(status <> 'available' AND object_key IS NULL)",
            name="ck_chat_media_assets_storage_state",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "source_id", "conversation_id"],
            [
                "chat_conversations.tenant_id",
                "chat_conversations.source_id",
                "chat_conversations.id",
            ],
            name="fk_chat_media_assets_conversation_same_source",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "source_id", "import_file_id"],
            [
                "chat_import_files.tenant_id",
                "chat_import_files.source_id",
                "chat_import_files.id",
            ],
            name="fk_chat_media_assets_file_same_source",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "source_id", "conversation_id", "message_id"],
            [
                "chat_messages.tenant_id",
                "chat_messages.source_id",
                "chat_messages.conversation_id",
                "chat_messages.id",
            ],
            name="fk_chat_media_assets_message_same_conversation",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "source_id", "conversation_id", "participant_id"],
            [
                "chat_participants.tenant_id",
                "chat_participants.source_id",
                "chat_participants.conversation_id",
                "chat_participants.id",
            ],
            name="fk_chat_media_assets_participant_same_conversation",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("object_key", name="uq_chat_media_assets_object_key"),
    )
    op.create_index(
        "ix_chat_media_assets_message",
        "chat_media_assets",
        ["tenant_id", "source_id", "message_id"],
    )
    op.create_index(
        "ix_chat_media_assets_participant",
        "chat_media_assets",
        ["tenant_id", "source_id", "participant_id"],
    )
    op.create_index(
        "ix_chat_media_assets_file",
        "chat_media_assets",
        ["tenant_id", "source_id", "import_file_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_chat_media_assets_file", table_name="chat_media_assets")
    op.drop_index("ix_chat_media_assets_participant", table_name="chat_media_assets")
    op.drop_index("ix_chat_media_assets_message", table_name="chat_media_assets")
    op.drop_table("chat_media_assets")
    op.drop_index(
        "ix_chat_media_extraction_jobs_tenant_created",
        table_name="chat_media_extraction_jobs",
    )
    op.drop_index(
        "ix_chat_media_extraction_jobs_claim",
        table_name="chat_media_extraction_jobs",
    )
    op.drop_table("chat_media_extraction_jobs")
    op.drop_constraint(
        "uq_chat_participants_tenant_source_conversation_id",
        "chat_participants",
        type_="unique",
    )
    op.drop_constraint(
        "uq_chat_messages_tenant_source_conversation_id",
        "chat_messages",
        type_="unique",
    )
