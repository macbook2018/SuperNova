"""Phase 5C idempotent metering, reservations and shadow settlement.

Revision ID: 0007_phase_5c_metering
Revises: 0006_phase_5b_routing
Create Date: 2026-11-12
"""

import sqlalchemy as sa
from alembic import op

revision = "0007_phase_5c_metering"
down_revision = "0006_phase_5b_routing"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("billing_reservations") as batch:
        batch.add_column(sa.Column("request_hash", sa.String(length=64), nullable=True))
        batch.add_column(sa.Column("model_source", sa.String(length=16), nullable=True))
        batch.add_column(sa.Column("subscription_id", sa.Uuid(as_uuid=True), nullable=True))
        batch.add_column(sa.Column("plan_id", sa.Uuid(as_uuid=True), nullable=True))
        batch.add_column(sa.Column("price_version_id", sa.Uuid(as_uuid=True), nullable=True))
        batch.add_column(sa.Column("period_start", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("period_end", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("usage_record_id", sa.Uuid(as_uuid=True), nullable=True))
        batch.add_column(sa.Column("response_status", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("response_json", sa.JSON(), nullable=True))
        batch.create_foreign_key(
            "fk_billing_reservations_subscription",
            "tenant_subscriptions",
            ["subscription_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch.create_foreign_key(
            "fk_billing_reservations_plan",
            "billing_plans",
            ["plan_id"],
            ["id"],
            ondelete="RESTRICT",
        )
        batch.create_foreign_key(
            "fk_billing_reservations_price_version",
            "platform_model_prices",
            ["price_version_id"],
            ["id"],
            ondelete="RESTRICT",
        )
        batch.create_foreign_key(
            "fk_billing_reservations_usage_record",
            "usage_records",
            ["usage_record_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch.create_check_constraint(
            "ck_billing_reservation_state",
            "state IN ('held', 'settled', 'released', 'unknown')",
        )
        batch.create_index(
            "ix_billing_reservations_tenant_state",
            ["tenant_id", "state", "expires_at"],
        )

    with op.batch_alter_table("billing_usage_events") as batch:
        batch.add_column(sa.Column("reservation_id", sa.Uuid(as_uuid=True), nullable=True))
        batch.create_foreign_key(
            "fk_billing_usage_reservation",
            "billing_reservations",
            ["reservation_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch.create_index("ix_billing_usage_reservation", ["reservation_id"])

    with op.batch_alter_table("billing_rated_items") as batch:
        batch.add_column(
            sa.Column("unit_price_minor_per_unit", sa.Numeric(24, 16), nullable=True)
        )
        batch.create_check_constraint(
            "ck_billing_rated_state",
            "state IN ('shadow', 'included', 'unmetered', 'unrated')",
        )
        batch.create_index("ix_billing_rated_state", ["state"])

    with op.batch_alter_table("billing_ledger_transactions") as batch:
        batch.add_column(
            sa.Column(
                "mode",
                sa.String(length=16),
                nullable=False,
                server_default="shadow",
            )
        )
        batch.create_check_constraint(
            "ck_billing_ledger_transaction_mode",
            "mode IN ('shadow', 'live')",
        )

    op.create_table(
        "billing_reservation_items",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("reservation_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("resource_code", sa.String(length=64), nullable=False),
        sa.Column("reserved_units", sa.BigInteger(), nullable=False),
        sa.Column(
            "consumed_units", sa.BigInteger(), nullable=False, server_default="0"
        ),
        sa.Column("state", sa.String(length=20), nullable=False, server_default="held"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["reservation_id"], ["billing_reservations.id"], ondelete="CASCADE"
        ),
        sa.UniqueConstraint(
            "reservation_id", "resource_code", name="uq_billing_reservation_resource"
        ),
        sa.CheckConstraint("reserved_units >= 0", name="ck_billing_reservation_item_reserved"),
        sa.CheckConstraint("consumed_units >= 0", name="ck_billing_reservation_item_consumed"),
        sa.CheckConstraint(
            "consumed_units <= reserved_units",
            name="ck_billing_reservation_item_consumed_le_reserved",
        ),
        sa.CheckConstraint(
            "state IN ('held', 'settled', 'released', 'unknown')",
            name="ck_billing_reservation_item_state",
        ),
    )
    op.create_index(
        "ix_billing_reservation_items_resource_state",
        "billing_reservation_items",
        ["resource_code", "state"],
    )

    op.create_table(
        "billing_audit_events",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("actor_user_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column("subject_type", sa.String(length=64), nullable=False),
        sa.Column("subject_id", sa.String(length=160), nullable=False),
        sa.Column("reason", sa.String(length=500), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["actor_user_id"], ["users.id"], ondelete="SET NULL"),
    )
    op.create_index(
        "ix_billing_audit_tenant_created",
        "billing_audit_events",
        ["tenant_id", "created_at"],
    )

    op.create_table(
        "billing_reconciliation_runs",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("period_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("period_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("report_json", sa.JSON(), nullable=False),
        sa.Column("created_by_user_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"], ["users.id"], ondelete="SET NULL"
        ),
        sa.CheckConstraint(
            "status IN ('completed', 'needs_review')",
            name="ck_billing_reconciliation_status",
        ),
    )
    op.create_index(
        "ix_billing_reconciliation_created",
        "billing_reconciliation_runs",
        ["created_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_billing_reconciliation_created", table_name="billing_reconciliation_runs"
    )
    op.drop_table("billing_reconciliation_runs")
    op.drop_index("ix_billing_audit_tenant_created", table_name="billing_audit_events")
    op.drop_table("billing_audit_events")
    op.drop_index(
        "ix_billing_reservation_items_resource_state",
        table_name="billing_reservation_items",
    )
    op.drop_table("billing_reservation_items")

    with op.batch_alter_table("billing_ledger_transactions") as batch:
        batch.drop_constraint("ck_billing_ledger_transaction_mode", type_="check")
        batch.drop_column("mode")

    with op.batch_alter_table("billing_rated_items") as batch:
        batch.drop_index("ix_billing_rated_state")
        batch.drop_constraint("ck_billing_rated_state", type_="check")
        batch.drop_column("unit_price_minor_per_unit")

    with op.batch_alter_table("billing_usage_events") as batch:
        batch.drop_index("ix_billing_usage_reservation")
        batch.drop_constraint("fk_billing_usage_reservation", type_="foreignkey")
        batch.drop_column("reservation_id")

    with op.batch_alter_table("billing_reservations") as batch:
        batch.drop_index("ix_billing_reservations_tenant_state")
        batch.drop_constraint("ck_billing_reservation_state", type_="check")
        batch.drop_constraint("fk_billing_reservations_usage_record", type_="foreignkey")
        batch.drop_constraint("fk_billing_reservations_price_version", type_="foreignkey")
        batch.drop_constraint("fk_billing_reservations_plan", type_="foreignkey")
        batch.drop_constraint("fk_billing_reservations_subscription", type_="foreignkey")
        batch.drop_column("response_json")
        batch.drop_column("response_status")
        batch.drop_column("usage_record_id")
        batch.drop_column("period_end")
        batch.drop_column("period_start")
        batch.drop_column("price_version_id")
        batch.drop_column("plan_id")
        batch.drop_column("subscription_id")
        batch.drop_column("model_source")
        batch.drop_column("request_hash")
