"""Phase 5B platform model routing and usage attribution.

Revision ID: 0006_phase_5b_routing
Revises: 0005_hybrid_billing
Create Date: 2026-11-12
"""

import sqlalchemy as sa
from alembic import op

revision = "0006_phase_5b_routing"
down_revision = "0005_hybrid_billing"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Existing platform model rows were not seeded in Phase 5A. The temporary
    # default keeps this additive migration safe for any manually-created rows.
    with op.batch_alter_table("platform_models") as batch:
        batch.add_column(
            sa.Column("base_url", sa.String(length=2048), nullable=False, server_default="")
        )
        batch.alter_column("base_url", server_default=None)

    op.create_index(
        "ix_platform_models_enabled_provider",
        "platform_models",
        ["enabled", "provider_type"],
    )

    with op.batch_alter_table("personas") as batch:
        batch.create_check_constraint(
            "ck_personas_model_source",
            "model_source IN ('platform', 'byok')",
        )
        batch.create_check_constraint(
            "ck_personas_model_source_reference",
            "(model_source = 'platform' AND platform_model_id IS NOT NULL AND provider_id IS NULL) "
            "OR (model_source = 'byok' AND platform_model_id IS NULL)",
        )
        batch.create_index("ix_personas_platform_model", ["platform_model_id"])

    with op.batch_alter_table("usage_records") as batch:
        batch.add_column(
            sa.Column(
                "model_source",
                sa.String(length=16),
                nullable=False,
                server_default="byok",
            )
        )
        batch.add_column(sa.Column("platform_model_id", sa.Uuid(as_uuid=True), nullable=True))
        batch.create_foreign_key(
            "fk_usage_records_platform_model",
            "platform_models",
            ["platform_model_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch.create_check_constraint(
            "ck_usage_records_model_source",
            "model_source IN ('platform', 'byok')",
        )
        batch.create_index(
            "ix_usage_records_tenant_source_created",
            ["tenant_id", "model_source", "created_at"],
        )
        batch.alter_column("model_source", server_default=None)


def downgrade() -> None:
    with op.batch_alter_table("usage_records") as batch:
        batch.drop_index("ix_usage_records_tenant_source_created")
        batch.drop_constraint("ck_usage_records_model_source", type_="check")
        batch.drop_constraint("fk_usage_records_platform_model", type_="foreignkey")
        batch.drop_column("platform_model_id")
        batch.drop_column("model_source")

    with op.batch_alter_table("personas") as batch:
        batch.drop_index("ix_personas_platform_model")
        batch.drop_constraint("ck_personas_model_source_reference", type_="check")
        batch.drop_constraint("ck_personas_model_source", type_="check")

    with op.batch_alter_table("platform_models") as batch:
        batch.drop_column("base_url")
    op.drop_index("ix_platform_models_enabled_provider", table_name="platform_models")
