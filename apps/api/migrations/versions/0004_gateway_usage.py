"""Create gateway token usage records.

Revision ID: 0004_gateway_usage
Revises: 0003_persona_provider_catalog
Create Date: 2026-11-12
"""

import sqlalchemy as sa
from alembic import op

revision = "0004_gateway_usage"
down_revision = "0003_persona_provider_catalog"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "usage_records",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("request_id", sa.String(length=36), nullable=False),
        sa.Column("tenant_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("user_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("api_key_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("persona_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("provider_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("persona_slug", sa.String(length=80), nullable=False),
        sa.Column("provider_type", sa.String(length=32), nullable=False),
        sa.Column("model_name", sa.String(length=120), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("prompt_tokens", sa.Integer(), nullable=True),
        sa.Column("completion_tokens", sa.Integer(), nullable=True),
        sa.Column("total_tokens", sa.Integer(), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=False),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["api_key_id"], ["api_keys.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["persona_id"], ["personas.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["provider_id"], ["model_providers.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_usage_records_tenant_created",
        "usage_records",
        ["tenant_id", "created_at"],
    )
    op.create_index(
        "ix_usage_records_user_created",
        "usage_records",
        ["user_id", "created_at"],
    )
    op.create_index(
        "ix_usage_records_request_id",
        "usage_records",
        ["request_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_usage_records_request_id", table_name="usage_records")
    op.drop_index("ix_usage_records_user_created", table_name="usage_records")
    op.drop_index("ix_usage_records_tenant_created", table_name="usage_records")
    op.drop_table("usage_records")
