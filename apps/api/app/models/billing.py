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

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    code: Mapped[str] = mapped_column(String(80), unique=True, nullable=False)
    display_name: Mapped[str] = mapped_column(String(100), nullable=False)
    provider_type: Mapped[str] = mapped_column(String(32), nullable=False)
    upstream_model_name: Mapped[str] = mapped_column(String(120), nullable=False)
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
        CheckConstraint("quantity_units >= 0", name="ck_billing_usage_quantity"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("tenants.id", ondelete="RESTRICT"), nullable=False
    )
    source_usage_record_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("usage_records.id", ondelete="SET NULL")
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
    __table_args__ = (Index("ix_billing_ledger_tx_created", "created_at"),)

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    idempotency_key: Mapped[str] = mapped_column(
        String(160), nullable=False, unique=True
    )
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
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
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("tenants.id", ondelete="RESTRICT"), nullable=False
    )
    event_key: Mapped[str] = mapped_column(String(160), nullable=False)
    max_amount_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    state: Mapped[str] = mapped_column(String(20), nullable=False, default="held")
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    settled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
