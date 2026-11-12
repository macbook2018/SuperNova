"""Non-destructive billing foundation.

Revision ID: 0005_hybrid_billing
Revises: 0004_gateway_usage
Create Date: 2026-11-12

This migration intentionally does not enroll tenants, enable platform models,
backfill historical charges, or move any existing API credentials.
"""

import sqlalchemy as sa
from alembic import op

revision = "0005_hybrid_billing"
down_revision = "0004_gateway_usage"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "billing_plans",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("code", sa.String(48), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("billing_interval", sa.String(12), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("subscription_price_minor", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint("code", "revision", name="uq_billing_plan_code_revision"),
        sa.CheckConstraint("subscription_price_minor >= 0", name="ck_billing_plan_price"),
        sa.CheckConstraint(
            "billing_interval IN ('month', 'year')", name="ck_billing_plan_interval"
        ),
    )
    op.create_table(
        "billing_plan_entitlements",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("plan_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("resource_code", sa.String(64), nullable=False),
        sa.Column("unit", sa.String(40), nullable=False),
        sa.Column("included_units", sa.BigInteger(), nullable=False),
        sa.Column("hard_limit_units", sa.BigInteger()),
        sa.Column("overage_rate_minor_per_unit", sa.Numeric(24, 10), nullable=False),
        sa.Column("overage_behavior", sa.String(16), nullable=False),
        sa.ForeignKeyConstraint(["plan_id"], ["billing_plans.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("plan_id", "resource_code", name="uq_billing_plan_resource"),
        sa.CheckConstraint("included_units >= 0", name="ck_billing_entitlement_included"),
        sa.CheckConstraint(
            "overage_rate_minor_per_unit >= 0", name="ck_billing_entitlement_rate"
        ),
    )
    op.create_table(
        "tenant_billing_policies",
        sa.Column("tenant_id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column(
            "default_model_source", sa.String(16), nullable=False, server_default="platform"
        ),
        sa.Column("currency", sa.String(3), nullable=False, server_default="CNY"),
        sa.Column("overage_enabled", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("overage_budget_minor", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.CheckConstraint("overage_budget_minor >= 0", name="ck_tenant_billing_budget"),
    )
    op.create_table(
        "tenant_subscriptions",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("plan_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("current_period_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("current_period_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "cancel_at_period_end", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["plan_id"], ["billing_plans.id"], ondelete="RESTRICT"),
    )
    op.create_index(
        "ix_tenant_subscriptions_tenant_status",
        "tenant_subscriptions", ["tenant_id", "status"],
    )
    op.create_index(
        "uq_tenant_subscriptions_one_current",
        "tenant_subscriptions", ["tenant_id"], unique=True,
        postgresql_where=sa.text("status IN ('active', 'trialing')"),
        sqlite_where=sa.text("status IN ('active', 'trialing')"),
    )
    op.create_table(
        "platform_models",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("code", sa.String(80), nullable=False, unique=True),
        sa.Column("display_name", sa.String(100), nullable=False),
        sa.Column("provider_type", sa.String(32), nullable=False),
        sa.Column("upstream_model_name", sa.String(120), nullable=False),
        sa.Column("credential_ref", sa.String(255)),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_table(
        "platform_model_prices",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("platform_model_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("input_per_million_minor", sa.Numeric(24, 8), nullable=False),
        sa.Column("output_per_million_minor", sa.Numeric(24, 8), nullable=False),
        sa.Column("effective_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("effective_until", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(["platform_model_id"], ["platform_models.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint(
            "platform_model_id", "revision", name="uq_platform_model_price_revision"
        ),
        sa.CheckConstraint("input_per_million_minor >= 0", name="ck_model_price_input"),
        sa.CheckConstraint("output_per_million_minor >= 0", name="ck_model_price_output"),
    )
    op.create_table(
        "billing_usage_events",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("source_usage_record_id", sa.Uuid(as_uuid=True)),
        sa.Column("event_key", sa.String(160), nullable=False),
        sa.Column("resource_code", sa.String(64), nullable=False),
        sa.Column("model_source", sa.String(16), nullable=False),
        sa.Column("quantity_units", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["source_usage_record_id"], ["usage_records.id"], ondelete="SET NULL"
        ),
        sa.UniqueConstraint("tenant_id", "event_key", name="uq_billing_usage_tenant_event"),
        sa.CheckConstraint("quantity_units >= 0", name="ck_billing_usage_quantity"),
    )
    op.create_index(
        "ix_billing_usage_tenant_time", "billing_usage_events", ["tenant_id", "occurred_at"]
    )
    op.create_table(
        "billing_rated_items",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("usage_event_id", sa.Uuid(as_uuid=True), nullable=False, unique=True),
        sa.Column("plan_id", sa.Uuid(as_uuid=True)),
        sa.Column("price_version_id", sa.Uuid(as_uuid=True)),
        sa.Column("included_units", sa.BigInteger(), nullable=False),
        sa.Column("overage_units", sa.BigInteger(), nullable=False),
        sa.Column("amount_minor_decimal", sa.Numeric(24, 8), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("rated_at", sa.DateTime(timezone=True)),
        sa.ForeignKeyConstraint(
            ["usage_event_id"], ["billing_usage_events.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["plan_id"], ["billing_plans.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["price_version_id"], ["platform_model_prices.id"], ondelete="RESTRICT"
        ),
        sa.CheckConstraint("included_units >= 0", name="ck_billing_rated_included"),
        sa.CheckConstraint("overage_units >= 0", name="ck_billing_rated_overage"),
        sa.CheckConstraint("amount_minor_decimal >= 0", name="ck_billing_rated_amount"),
    )
    op.create_table(
        "billing_ledger_accounts",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("account_key", sa.String(140), nullable=False, unique=True),
        sa.Column("tenant_id", sa.Uuid(as_uuid=True)),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="RESTRICT"),
    )
    op.create_index("ix_billing_ledger_account_tenant", "billing_ledger_accounts", ["tenant_id"])
    op.create_table(
        "billing_ledger_transactions",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("idempotency_key", sa.String(160), nullable=False, unique=True),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("reference_id", sa.String(160)),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index(
        "ix_billing_ledger_tx_created", "billing_ledger_transactions", ["created_at"]
    )
    op.create_table(
        "billing_ledger_postings",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("transaction_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("account_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("amount_minor", sa.BigInteger(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["transaction_id"], ["billing_ledger_transactions.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["account_id"], ["billing_ledger_accounts.id"], ondelete="RESTRICT"
        ),
        sa.CheckConstraint("amount_minor != 0", name="ck_billing_ledger_nonzero"),
    )
    op.create_index(
        "ix_billing_ledger_postings_account", "billing_ledger_postings", ["account_id"]
    )
    op.create_table(
        "billing_reservations",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("event_key", sa.String(160), nullable=False),
        sa.Column("max_amount_minor", sa.BigInteger(), nullable=False),
        sa.Column("state", sa.String(20), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("settled_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("tenant_id", "event_key", name="uq_billing_reservation_event"),
        sa.CheckConstraint("max_amount_minor >= 0", name="ck_billing_reservation_amount"),
    )
    # Existing personas are BYOK; changing their source automatically would break
    # existing provider links and could unexpectedly charge the customer.
    with op.batch_alter_table("personas") as batch:
        batch.add_column(
            sa.Column(
                "model_source", sa.String(16), nullable=False, server_default="byok"
            )
        )
        batch.add_column(sa.Column("platform_model_id", sa.Uuid(as_uuid=True)))
        batch.create_foreign_key(
            "fk_personas_platform_model",
            "platform_models",
            ["platform_model_id"],
            ["id"],
            ondelete="RESTRICT",
        )


def downgrade() -> None:
    with op.batch_alter_table("personas") as batch:
        batch.drop_constraint("fk_personas_platform_model", type_="foreignkey")
        batch.drop_column("platform_model_id")
        batch.drop_column("model_source")
    op.drop_table("billing_reservations")
    op.drop_index("ix_billing_ledger_postings_account", table_name="billing_ledger_postings")
    op.drop_table("billing_ledger_postings")
    op.drop_index("ix_billing_ledger_tx_created", table_name="billing_ledger_transactions")
    op.drop_table("billing_ledger_transactions")
    op.drop_index("ix_billing_ledger_account_tenant", table_name="billing_ledger_accounts")
    op.drop_table("billing_ledger_accounts")
    op.drop_table("billing_rated_items")
    op.drop_index("ix_billing_usage_tenant_time", table_name="billing_usage_events")
    op.drop_table("billing_usage_events")
    op.drop_table("platform_model_prices")
    op.drop_table("platform_models")
    op.drop_index("uq_tenant_subscriptions_one_current", table_name="tenant_subscriptions")
    op.drop_index("ix_tenant_subscriptions_tenant_status", table_name="tenant_subscriptions")
    op.drop_table("tenant_subscriptions")
    op.drop_table("tenant_billing_policies")
    op.drop_table("billing_plan_entitlements")
    op.drop_table("billing_plans")
