from decimal import Decimal

import pytest
from app.billing.pricing import (
    quote_model_tokens,
    quote_resource_overage,
    round_invoice_minor,
    validate_balanced_postings,
)
from app.db.base import Base
from app.models.billing import (
    BillingLedgerAccount,
    BillingPlan,
    BillingReservation,
    BillingUsageEvent,
    PlatformModel,
    TenantBillingPolicy,
)
from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool


def test_model_pricing_platform_and_byok_are_distinct() -> None:
    kwargs = {
        "input_tokens": 2_000_000,
        "output_tokens": 1_000_000,
        "input_per_million_minor": Decimal("120"),
        "output_per_million_minor": Decimal("300"),
    }
    platform = quote_model_tokens(model_source="platform", **kwargs)
    byok = quote_model_tokens(model_source="byok", **kwargs)

    assert platform.amount_minor_exact == Decimal("540")
    assert byok.amount_minor_exact == Decimal("0")
    assert byok.input_tokens == 2_000_000


def test_fractional_charges_round_only_when_settled() -> None:
    quotes = [
        quote_model_tokens(
            model_source="platform",
            input_tokens=1000,
            output_tokens=1000,
            input_per_million_minor=Decimal("100"),
            output_per_million_minor=Decimal("200"),
        ).amount_minor_exact
        for _ in range(3)
    ]
    assert quotes[0] == Decimal("0.3")
    assert round_invoice_minor(quotes) == 1


def test_resource_overage_crossing_included_limit() -> None:
    quote = quote_resource_overage(
        previous_units=95,
        requested_units=15,
        included_units=100,
        overage_rate_minor_per_unit=Decimal("0.25"),
    )
    assert quote.overage_units == 10
    assert quote.amount_minor_exact == Decimal("2.5")

    repeated_period = quote_resource_overage(
        previous_units=110,
        requested_units=10,
        included_units=100,
        overage_rate_minor_per_unit=Decimal("0.25"),
    )
    assert repeated_period.overage_units == 10


@pytest.mark.parametrize("source", ["PLATFORM", "", "unknown"])
def test_model_source_is_explicit(source: str) -> None:
    with pytest.raises(ValueError):
        quote_model_tokens(
            model_source=source,
            input_tokens=1,
            output_tokens=2,
            input_per_million_minor=Decimal("1"),
            output_per_million_minor=Decimal("1"),
        )


def test_non_decimal_prices_or_negative_usage_are_rejected() -> None:
    with pytest.raises(ValueError):
        quote_model_tokens(
            model_source="platform",
            input_tokens=-1,
            output_tokens=2,
            input_per_million_minor=Decimal("1"),
            output_per_million_minor=Decimal("1"),
        )
    with pytest.raises(ValueError):
        quote_model_tokens(
            model_source="platform",
            input_tokens=1,
            output_tokens=2,
            input_per_million_minor=1.2,  # type: ignore[arg-type]
            output_per_million_minor=Decimal("1"),
        )
    with pytest.raises(ValueError):
        quote_resource_overage(
            previous_units=10,
            requested_units=2,
            included_units=5,
            overage_rate_minor_per_unit=Decimal("NaN"),
        )


def test_money_rounding_rejects_invalid_numbers() -> None:
    with pytest.raises(ValueError):
        round_invoice_minor([Decimal("Infinity")])
    with pytest.raises(ValueError):
        round_invoice_minor([Decimal("-1")])


def test_ledger_journal_must_balance() -> None:
    validate_balanced_postings([-200, 150, 50])
    with pytest.raises(ValueError):
        validate_balanced_postings([100, -80])
    with pytest.raises(ValueError):
        validate_balanced_postings([0, 0])
    with pytest.raises(ValueError):
        validate_balanced_postings([100])


def test_billing_schema_is_inert_and_has_new_tables() -> None:
    engine = create_engine(
        "sqlite+pysqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    try:
        Base.metadata.create_all(engine)
        tables = set(inspect(engine).get_table_names())
        assert {
            "billing_plans",
            "billing_plan_entitlements",
            "tenant_billing_policies",
            "tenant_subscriptions",
            "platform_models",
            "platform_model_prices",
            "billing_usage_events",
            "billing_rated_items",
            "billing_ledger_accounts",
            "billing_ledger_transactions",
            "billing_ledger_postings",
            "billing_reservations",
            "personas",
        }.issubset(tables)
        with Session(engine) as session:
            assert session.query(BillingPlan).count() == 0
            assert session.query(BillingUsageEvent).count() == 0
            assert session.query(PlatformModel).count() == 0
            assert session.query(TenantBillingPolicy).count() == 0
            assert session.query(BillingReservation).count() == 0
            assert session.query(BillingLedgerAccount).count() == 0
    finally:
        engine.dispose()
