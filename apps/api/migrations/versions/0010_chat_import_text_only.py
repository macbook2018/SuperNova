"""Bring legacy chat-import schema forward to the text-only analysis model.

Revision ID: 0010_chat_import_text_only
Revises: 0009_chat_import

This migration supports both the original persisted-chat 0009 schema and the
revised text-only 0009 schema. Legacy message counters are not relabeled as
text-only counts because their original semantics included non-text messages.
"""

import sqlalchemy as sa
from alembic import op

revision = "0010_chat_import_text_only"
down_revision = "0009_chat_import"
branch_labels = None
depends_on = None


def _inspector():
    return sa.inspect(op.get_bind())


def _has_table(table_name: str) -> bool:
    return _inspector().has_table(table_name)


def _columns(table_name: str) -> set[str]:
    return {column["name"] for column in _inspector().get_columns(table_name)}


def _has_check_constraint(table_name: str, name: str) -> bool:
    return any(
        constraint.get("name") == name
        for constraint in _inspector().get_check_constraints(table_name)
    )


def _has_index(table_name: str, name: str) -> bool:
    return any(index.get("name") == name for index in _inspector().get_indexes(table_name))


def _replace_check_constraint(table_name: str, name: str, condition: str) -> None:
    if _has_check_constraint(table_name, name):
        op.drop_constraint(name, table_name, type_="check")
    op.create_check_constraint(name, table_name, condition)


def _ensure_text_counter(
    table_name: str,
    legacy_name: str,
    current_name: str,
) -> None:
    columns = _columns(table_name)
    if current_name not in columns:
        op.add_column(
            table_name,
            sa.Column(current_name, sa.BigInteger(), nullable=False, server_default="0"),
        )
    else:
        op.execute(
            sa.text(
                f'UPDATE "{table_name}" SET "{current_name}" = 0 '
                f'WHERE "{current_name}" IS NULL'
            )
        )
        op.alter_column(
            table_name,
            current_name,
            existing_type=sa.BigInteger(),
            nullable=False,
            server_default="0",
        )

    if legacy_name in _columns(table_name):
        op.drop_column(table_name, legacy_name)


def _ensure_defaulted_column(
    table_name: str,
    column_name: str,
    column_type: sa.types.TypeEngine,
) -> None:
    if column_name not in _columns(table_name):
        op.add_column(
            table_name,
            sa.Column(column_name, column_type, nullable=False, server_default="0"),
        )
    else:
        op.execute(
            sa.text(
                f'UPDATE "{table_name}" SET "{column_name}" = 0 '
                f'WHERE "{column_name}" IS NULL'
            )
        )
        op.alter_column(
            table_name,
            column_name,
            existing_type=column_type,
            nullable=False,
            server_default="0",
        )


def _create_sender_summaries() -> None:
    if not _has_table("chat_import_sender_summaries"):
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
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
            sa.CheckConstraint(
                "message_count > 0",
                name="ck_chat_import_sender_summaries_count",
            ),
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
                "tenant_id",
                "job_id",
                "sender_ref",
                name="uq_chat_import_sender_summaries_job_sender",
            ),
        )

    if not _has_index(
        "chat_import_sender_summaries",
        "ix_chat_import_sender_summaries_source",
    ):
        op.create_index(
            "ix_chat_import_sender_summaries_source",
            "chat_import_sender_summaries",
            ["tenant_id", "source_id", "sender_ref"],
        )


def upgrade() -> None:
    required_tables = {"chat_sources", "chat_import_jobs", "chat_import_files"}
    missing_tables = required_tables - set(_inspector().get_table_names())
    if missing_tables:
        missing = ", ".join(sorted(missing_tables))
        raise RuntimeError(f"Cannot upgrade chat import schema; missing tables: {missing}")

    if "platform" not in _columns("chat_sources"):
        op.add_column(
            "chat_sources",
            sa.Column(
                "platform",
                sa.String(length=32),
                nullable=False,
                server_default="wechat",
            ),
        )
    else:
        op.execute(
            sa.text("UPDATE chat_sources SET platform = 'wechat' WHERE platform IS NULL")
        )
        op.alter_column(
            "chat_sources",
            "platform",
            existing_type=sa.String(length=32),
            nullable=False,
            server_default="wechat",
        )

    _ensure_text_counter(
        "chat_import_jobs",
        "parsed_messages",
        "parsed_text_messages",
    )
    for legacy_column in ("imported_messages", "duplicate_messages"):
        if legacy_column in _columns("chat_import_jobs"):
            op.drop_column("chat_import_jobs", legacy_column)

    _ensure_defaulted_column(
        "chat_import_jobs",
        "ignored_non_text_messages",
        sa.BigInteger(),
    )
    _ensure_defaulted_column(
        "chat_import_jobs",
        "unique_senders",
        sa.Integer(),
    )
    _replace_check_constraint(
        "chat_import_jobs",
        "ck_chat_import_jobs_status",
        "status IN ('queued', 'processing', 'awaiting_identity', "
        "'ready_for_analysis', 'succeeded', 'failed', 'cancelled')",
    )

    _ensure_text_counter(
        "chat_import_files",
        "parsed_message_count",
        "parsed_text_message_count",
    )
    _ensure_defaulted_column(
        "chat_import_files",
        "ignored_non_text_message_count",
        sa.BigInteger(),
    )

    file_columns = _columns("chat_import_files")
    if "expires_at" not in file_columns:
        op.add_column(
            "chat_import_files",
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        )
    op.execute(
        sa.text(
            "UPDATE chat_import_files "
            "SET expires_at = COALESCE(expires_at, created_at + INTERVAL '24 hours')"
        )
    )
    op.execute(
        sa.text(
            "UPDATE chat_import_files "
            "SET status = 'failed', "
            "error_code = COALESCE(error_code, 'legacy_file_deleted'), "
            "expires_at = NOW() "
            "WHERE status = 'deleted'"
        )
    )
    op.alter_column(
        "chat_import_files",
        "expires_at",
        existing_type=sa.DateTime(timezone=True),
        nullable=False,
    )

    if "metadata_json" in _columns("chat_import_files"):
        op.drop_column("chat_import_files", "metadata_json")

    _replace_check_constraint(
        "chat_import_files",
        "ck_chat_import_files_status",
        "status IN ('uploaded', 'processing', 'parsed', 'failed')",
    )

    if not _has_index("chat_import_files", "ix_chat_import_files_expiry"):
        op.create_index(
            "ix_chat_import_files_expiry",
            "chat_import_files",
            ["expires_at"],
        )

    _create_sender_summaries()

    # Legacy tables persisted full chat content. The current product retains
    # sender counts and generated, approved assets instead of chat archives.
    for legacy_table in ("chat_messages", "chat_participants", "chat_conversations"):
        if _has_table(legacy_table):
            op.drop_table(legacy_table)


def downgrade() -> None:
    raise NotImplementedError(
        "This migration removes legacy persisted chat archives. "
        "Restore a database backup to return to the pre-0010 schema."
    )
