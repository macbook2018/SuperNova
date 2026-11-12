"""Billing foundation models.

These are non-operational schema primitives. Monetary writes and gateway charging
remain disabled until the reservation, settlement and idempotency services are vetted.
"""

from datetime import datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    Uuid,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class BillingPlan(Base):
    __tablename__ = "billing_plans"
    __table_args__ = (
        UniqueConstraint("code", "revision", name="uq_billing_plan_code_revision"),
        CheckConstraint("subscription_price_minor >= 0", name="ck_billing_plan_price"),
        CheckConstraint(
            "billing_interval IN ('month', 'year')",
            name="ck_billing_plan_interval",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    code: Mapped[str] = mapped_column(String(48), nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    billing_interval: Mapped[str] = mapped_column(String(12), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    subscription_price_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="draft")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class BillingPlanEntitlement(Base):
    __tablename__ = "billing_plan_entitlements"
    __table_args__ = (
        UniqueConstraint("plan_id", "resource_code", name="uq_billing_plan_resource"),
        CheckConstraint("included_units >= 0", name="ck_billing_entitlement_included"),
        CheckConstraint(
            "overage_rate_minor_per_unit >= 0", name="ck_billing_entitlement_rate"
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    plan_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("billing_plans.id", ondelete="RESTRICT"), nullable=False
    )
    resource_code: Mapped[str] = mapped_column(String(64), nullable=False)
    unit: Mapped[str] = mapped_column(String(40), nullable=False)
    included_units: Mapped[int] = mapped_column(BigInteger, nullable=False)
    hard_limit_units: Mapped[int | None] = mapped_column(BigInteger)
    overage_rate_minor_per_unit: Mapped[Decimal] = mapped_column(
        Numeric(24, 10), nullable=False
    )
    overage_behavior: Mapped[str] = mapped_column(
        String(16), nullable=False, default="block"
    )


class TenantBillingPolicy(Base):
    __tablename__ = "tenant_billing_policies"
    __table_args__ = (
        CheckConstraint("overage_budget_minor >= 0", name="ck_tenant_billing_budget"),
    )

    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), primary_key=True
    )
    default_model_source: Mapped[str] = mapped_column(
        String(16), nullable=False, default="platform", server_default="platform"
    )
    currency: Mapped[str] = mapped_column(
        String(3), nullable=False, default="CNY", server_default="CNY"
    )
    overage_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    overage_budget_minor: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default=text("0")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class TenantSubscription(Base):
    __tablename__ = "tenant_subscriptions"
    __table_args__ = (
        Index("ix_tenant_subscriptions_tenant_status", "tenant_id", "status"),
        Index(
            "uq_tenant_subscriptions_one_current",
            "tenant_id",
            unique=True,
            postgresql_where=text("status IN ('active', 'trialing')"),
            sqlite_where=text("status IN ('active', 'trialing')"),
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("tenants.id", ondelete="RESTRICT"), nullable=False
    )
    plan_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("billing_plans.id", ondelete="RESTRICT"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    current_period_start: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    current_period_end: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    cancel_at_period_end: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class PlatformModel(Base):
    __tablename__ = "platform_models"
    __table_args__ = (
        Index("ix_platform_models_enabled_provider", "enabled", "provider_type"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    code: Mapped[str] = mapped_column(String(80), unique=True, nullable=False)
    display_name: Mapped[str] = mapped_column(String(100), nullable=False)
    provider_type: Mapped[str] = mapped_column(String(32), nullable=False)
    upstream_model_name: Mapped[str] = mapped_column(String(120), nullable=False)
    base_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    credential_ref: Mapped[str | None] = mapped_column(String(255))
    enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class PlatformModelPrice(Base):
    __tablename__ = "platform_model_prices"
    __table_args__ = (
        UniqueConstraint(
            "platform_model_id", "revision", name="uq_platform_model_price_revision"
        ),
        CheckConstraint("input_per_million_minor >= 0", name="ck_model_price_input"),
        CheckConstraint("output_per_million_minor >= 0", name="ck_model_price_output"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    platform_model_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("platform_models.id", ondelete="RESTRICT"), nullable=False
    )
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    input_per_million_minor: Mapped[Decimal] = mapped_column(
        Numeric(24, 8), nullable=False
    )
    output_per_million_minor: Mapped[Decimal] = mapped_column(
        Numeric(24, 8), nullable=False
    )
    effective_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    effective_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class BillingUsageEvent(Base):
    __tablename__ = "billing_usage_events"
    __table_args__ = (
        UniqueConstraint("tenant_id", "event_key", name="uq_billing_usage_tenant_event"),
        Index("ix_billing_usage_tenant_time", "tenant_id", "occurred_at"),
        Index("ix_billing_usage_reservation", "reservation_id"),
        CheckConstraint("quantity_units >= 0", name="ck_billing_usage_quantity"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("tenants.id", ondelete="RESTRICT"), nullable=False
    )
    source_usage_record_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("usage_records.id", ondelete="SET NULL")
    )
    reservation_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("billing_reservations.id", ondelete="SET NULL")
    )
    event_key: Mapped[str] = mapped_column(String(160), nullable=False)
    resource_code: Mapped[str] = mapped_column(String(64), nullable=False)
    model_source: Mapped[str] = mapped_column(String(16), nullable=False)
    quantity_units: Mapped[int] = mapped_column(BigInteger, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="observed")
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class BillingRatedItem(Base):
    __tablename__ = "billing_rated_items"
    __table_args__ = (
        CheckConstraint(
            "state IN ('shadow', 'included', 'unmetered', 'unrated')",
            name="ck_billing_rated_state",
        ),
        Index("ix_billing_rated_state", "state"),
        CheckConstraint("included_units >= 0", name="ck_billing_rated_included"),
        CheckConstraint("overage_units >= 0", name="ck_billing_rated_overage"),
        CheckConstraint("amount_minor_decimal >= 0", name="ck_billing_rated_amount"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    usage_event_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("billing_usage_events.id", ondelete="RESTRICT"),
        nullable=False,
        unique=True,
    )
    plan_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("billing_plans.id", ondelete="RESTRICT")
    )
    price_version_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("platform_model_prices.id", ondelete="RESTRICT")
    )
    included_units: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    overage_units: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    amount_minor_decimal: Mapped[Decimal] = mapped_column(
        Numeric(24, 8), nullable=False
    )
    unit_price_minor_per_unit: Mapped[Decimal | None] = mapped_column(Numeric(24, 16))
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    state: Mapped[str] = mapped_column(String(16), nullable=False, default="unrated")
    rated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class BillingLedgerAccount(Base):
    __tablename__ = "billing_ledger_accounts"
    __table_args__ = (Index("ix_billing_ledger_account_tenant", "tenant_id"),)

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    account_key: Mapped[str] = mapped_column(String(140), unique=True, nullable=False)
    tenant_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("tenants.id", ondelete="RESTRICT")
    )
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class BillingLedgerTransaction(Base):
    __tablename__ = "billing_ledger_transactions"
    __table_args__ = (
        CheckConstraint("mode IN ('shadow', 'live')", name="ck_billing_ledger_transaction_mode"),
        Index("ix_billing_ledger_tx_created", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    idempotency_key: Mapped[str] = mapped_column(
        String(160), nullable=False, unique=True
    )
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    mode: Mapped[str] = mapped_column(
        String(16), nullable=False, default="shadow", server_default="shadow"
    )
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    reference_id: Mapped[str | None] = mapped_column(String(160))
    metadata_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class BillingLedgerPosting(Base):
    __tablename__ = "billing_ledger_postings"
    __table_args__ = (
        CheckConstraint("amount_minor != 0", name="ck_billing_ledger_nonzero"),
        Index("ix_billing_ledger_postings_account", "account_id"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    transaction_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("billing_ledger_transactions.id", ondelete="RESTRICT"),
        nullable=False,
    )
    account_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("billing_ledger_accounts.id", ondelete="RESTRICT"),
        nullable=False
    )
    amount_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class BillingReservation(Base):
    __tablename__ = "billing_reservations"
    __table_args__ = (
        UniqueConstraint("tenant_id", "event_key", name="uq_billing_reservation_event"),
        CheckConstraint("max_amount_minor >= 0", name="ck_billing_reservation_amount"),
        CheckConstraint(
            "state IN ('held', 'settled', 'released', 'unknown')",
            name="ck_billing_reservation_state",
        ),
        Index("ix_billing_reservations_tenant_state", "tenant_id", "state", "expires_at"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("tenants.id", ondelete="RESTRICT"), nullable=False
    )
    event_key: Mapped[str] = mapped_column(String(160), nullable=False)
    request_hash: Mapped[str | None] = mapped_column(String(64))
    model_source: Mapped[str | None] = mapped_column(String(16))
    subscription_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("tenant_subscriptions.id", ondelete="SET NULL")
    )
    plan_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("billing_plans.id", ondelete="RESTRICT")
    )
    price_version_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("platform_model_prices.id", ondelete="RESTRICT")
    )
    period_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    period_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    usage_record_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("usage_records.id", ondelete="SET NULL")
    )
    response_status: Mapped[int | None] = mapped_column(Integer)
    response_json: Mapped[dict | None] = mapped_column(JSON)
    max_amount_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    state: Mapped[str] = mapped_column(String(20), nullable=False, default="held")
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    settled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class BillingReservationItem(Base):
    __tablename__ = "billing_reservation_items"
    __table_args__ = (
        UniqueConstraint("reservation_id", "resource_code", name="uq_billing_reservation_resource"),
        CheckConstraint("reserved_units >= 0", name="ck_billing_reservation_item_reserved"),
        CheckConstraint("consumed_units >= 0", name="ck_billing_reservation_item_consumed"),
        CheckConstraint(
            "consumed_units <= reserved_units",
            name="ck_billing_reservation_item_consumed_le_reserved",
        ),
        CheckConstraint(
            "state IN ('held', 'settled', 'released', 'unknown')",
            name="ck_billing_reservation_item_state",
        ),
        Index("ix_billing_reservation_items_resource_state", "resource_code", "state"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    reservation_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("billing_reservations.id", ondelete="CASCADE"),
        nullable=False,
    )
    resource_code: Mapped[str] = mapped_column(String(64), nullable=False)
    reserved_units: Mapped[int] = mapped_column(BigInteger, nullable=False)
    consumed_units: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    state: Mapped[str] = mapped_column(String(20), nullable=False, default="held")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class BillingAuditEvent(Base):
    __tablename__ = "billing_audit_events"
    __table_args__ = (
        Index("ix_billing_audit_tenant_created", "tenant_id", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("tenants.id", ondelete="SET NULL")
    )
    actor_user_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    subject_type: Mapped[str] = mapped_column(String(64), nullable=False)
    subject_id: Mapped[str] = mapped_column(String(160), nullable=False)
    reason: Mapped[str | None] = mapped_column(String(500))
    metadata_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class BillingReconciliationRun(Base):
    __tablename__ = "billing_reconciliation_runs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('completed', 'needs_review')",
            name="ck_billing_reconciliation_status",
        ),
        Index("ix_billing_reconciliation_created", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    period_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    period_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    report_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_by_user_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
