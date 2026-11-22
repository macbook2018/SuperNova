"""Isolated AstrBot bot instances, lifecycle commands, events and runtime credentials.

Revision ID: 0012_bot_runtime
Revises: 0011_chat_analysis_review
"""

import sqlalchemy as sa
from alembic import op

revision = "0012_bot_runtime"
down_revision = "0011_chat_analysis_review"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "bot_instances",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("tenant_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("persona_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("created_by_user_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("runtime_version", sa.String(length=100), nullable=False),
        sa.Column("desired_state", sa.String(length=16), nullable=False, server_default="stopped"),
        sa.Column("actual_state", sa.String(length=20), nullable=False, server_default="pending"),
        sa.Column("config_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column(
            "runtime_provider", sa.String(length=32), nullable=False, server_default="docker"
        ),
        sa.Column("runtime_resource_id", sa.String(length=255), nullable=True),
        sa.Column("last_heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error_code", sa.String(length=100), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "desired_state IN ('running', 'stopped', 'deleting')",
            name="ck_bot_instances_desired_state",
        ),
        sa.CheckConstraint(
            "actual_state IN ('pending', 'provisioning', 'starting', 'running', "
            "'stopping', 'stopped', 'unhealthy', 'failed', 'deleting')",
            name="ck_bot_instances_actual_state",
        ),
        sa.CheckConstraint("config_version > 0", name="ck_bot_instances_config_version"),
        sa.CheckConstraint(
            "runtime_provider IN ('docker', 'kubernetes')",
            name="ck_bot_instances_runtime_provider",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "persona_id"],
            ["personas.tenant_id", "personas.id"],
            name="fk_bot_instances_persona_same_tenant",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "created_by_user_id"],
            ["tenant_memberships.tenant_id", "tenant_memberships.user_id"],
            name="fk_bot_instances_creator_membership_same_tenant",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_bot_instances_tenant_id"),
        sa.UniqueConstraint(
            "tenant_id", "id", "persona_id", name="uq_bot_instances_tenant_id_persona"
        ),
        sa.UniqueConstraint(
            "runtime_provider", "runtime_resource_id",
            name="uq_bot_instances_runtime_resource",
        ),
    )
    op.create_index(
        "ix_bot_instances_tenant_created",
        "bot_instances",
        ["tenant_id", "created_at"],
    )
    op.create_index(
        "ix_bot_instances_runtime_state",
        "bot_instances",
        ["runtime_provider", "desired_state", "actual_state"],
    )

    op.create_table(
        "bot_runtime_commands",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("tenant_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("bot_instance_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("command", sa.String(length=24), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="queued"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="5"),
        sa.Column(
            "available_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("worker_id", sa.String(length=120), nullable=True),
        sa.Column("error_code", sa.String(length=100), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "command IN ('start', 'stop', 'restart', 'delete', 'update_config')",
            name="ck_bot_runtime_commands_command",
        ),
        sa.CheckConstraint(
            "status IN ('queued', 'leased', 'succeeded', 'failed', 'cancelled')",
            name="ck_bot_runtime_commands_status",
        ),
        sa.CheckConstraint(
            "attempts >= 0 AND max_attempts > 0 AND attempts <= max_attempts",
            name="ck_bot_runtime_commands_attempts",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "bot_instance_id"],
            ["bot_instances.tenant_id", "bot_instances.id"],
            name="fk_bot_runtime_commands_bot_same_tenant",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_bot_runtime_commands_tenant_id"),
        sa.UniqueConstraint(
            "tenant_id", "idempotency_key", name="uq_bot_runtime_commands_idempotency"
        ),
    )
    op.create_index(
        "ix_bot_runtime_commands_claim",
        "bot_runtime_commands",
        ["status", "available_at", "created_at"],
    )
    op.create_index(
        "ix_bot_runtime_commands_bot",
        "bot_runtime_commands",
        ["tenant_id", "bot_instance_id", "created_at"],
    )

    op.create_table(
        "bot_runtime_events",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("tenant_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("bot_instance_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("channel_account_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("source_account_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("platform", sa.String(length=32), nullable=False, server_default="system"),
        sa.Column("correlation_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("payload_json", sa.JSON(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("schema_version > 0", name="ck_bot_runtime_events_schema_version"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "bot_instance_id"],
            ["bot_instances.tenant_id", "bot_instances.id"],
            name="fk_bot_runtime_events_bot_same_tenant",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_bot_runtime_events_tenant_id"),
    )
    op.create_index(
        "ix_bot_runtime_events_timeline",
        "bot_runtime_events",
        ["tenant_id", "bot_instance_id", "created_at", "id"],
    )
    op.create_index(
        "ix_bot_runtime_events_type",
        "bot_runtime_events",
        ["tenant_id", "bot_instance_id", "event_type", "created_at"],
    )

    op.create_table(
        "bot_runtime_credentials",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("tenant_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("bot_instance_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("persona_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("jti", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("config_version", sa.Integer(), nullable=False),
        sa.Column("scope", sa.String(length=64), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "scope IN ('chat.completions', 'runtime.heartbeat', 'runtime.events')",
            name="ck_bot_runtime_credentials_scope",
        ),
        sa.CheckConstraint("config_version > 0", name="ck_bot_runtime_credentials_config_version"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "bot_instance_id", "persona_id"],
            ["bot_instances.tenant_id", "bot_instances.id", "bot_instances.persona_id"],
            name="fk_bot_runtime_credentials_bot_persona_same_tenant",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_bot_runtime_credentials_tenant_id"),
        sa.UniqueConstraint("jti", name="uq_bot_runtime_credentials_jti"),
        sa.UniqueConstraint("token_hash", name="uq_bot_runtime_credentials_token_hash"),
    )
    op.create_index(
        "ix_bot_runtime_credentials_scope_expiry",
        "bot_runtime_credentials",
        ["tenant_id", "bot_instance_id", "scope", "expires_at"],
    )

    op.add_column(
        "usage_records",
        sa.Column("bot_instance_id", sa.Uuid(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_usage_records_bot_instance",
        "usage_records",
        "bot_instances",
        ["bot_instance_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_usage_records_bot_created",
        "usage_records",
        ["tenant_id", "bot_instance_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_usage_records_bot_created", table_name="usage_records")
    op.drop_constraint("fk_usage_records_bot_instance", "usage_records", type_="foreignkey")
    op.drop_column("usage_records", "bot_instance_id")

    op.drop_index("ix_bot_runtime_credentials_scope_expiry", table_name="bot_runtime_credentials")
    op.drop_table("bot_runtime_credentials")

    op.drop_index("ix_bot_runtime_events_type", table_name="bot_runtime_events")
    op.drop_index("ix_bot_runtime_events_timeline", table_name="bot_runtime_events")
    op.drop_table("bot_runtime_events")

    op.drop_index("ix_bot_runtime_commands_bot", table_name="bot_runtime_commands")
    op.drop_index("ix_bot_runtime_commands_claim", table_name="bot_runtime_commands")
    op.drop_table("bot_runtime_commands")

    op.drop_index("ix_bot_instances_runtime_state", table_name="bot_instances")
    op.drop_index("ix_bot_instances_tenant_created", table_name="bot_instances")
    op.drop_table("bot_instances")
