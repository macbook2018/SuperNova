from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Barrier
from uuid import uuid4

import pytest
from app.billing.metering import reserve_gateway_request
from app.db.base import Base
from app.models import __all__ as MODEL_EXPORTS
from app.models.billing import (
    BillingPlan,
    BillingPlanEntitlement,
    BillingReservation,
    TenantBillingPolicy,
    TenantSubscription,
)
from app.models.identity import Tenant
from fastapi import HTTPException
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.orm import sessionmaker


@pytest.fixture
def postgres_session_factory():
    database_url = os.getenv("SUPERNOVA_DATABASE_URL", "")
    if not database_url.startswith("postgresql"):
        pytest.skip("PostgreSQL concurrency integration test requires SUPERNOVA_DATABASE_URL")
    assert MODEL_EXPORTS

    admin_engine = create_engine(database_url, pool_pre_ping=True)
    schema = f"sn_metering_{uuid4().hex[:12]}"
    with admin_engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))

    engine = create_engine(
        database_url,
        connect_args={"options": f"-c search_path={schema},public"},
        pool_size=8,
        max_overflow=0,
        pool_pre_ping=True,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(
        bind=engine,
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
    )
    try:
        yield factory
    finally:
        engine.dispose()
        with admin_engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin_engine.dispose()


def test_postgres_row_lock_prevents_concurrent_quota_overbooking(
    postgres_session_factory,
) -> None:
    tenant_id = uuid4()
    plan_id = uuid4()
    now = datetime.now(UTC)
    with postgres_session_factory() as db:
        db.add(
            Tenant(
                id=tenant_id,
                name="Concurrency Test",
                status="active",
            )
        )
        db.flush()
        db.add(
            TenantBillingPolicy(
                tenant_id=tenant_id,
                currency="CNY",
                overage_enabled=False,
                overage_budget_minor=0,
            )
        )
        db.add(
            BillingPlan(
                id=plan_id,
                code="concurrency",
                revision=1,
                name="Concurrency",
                billing_interval="month",
                currency="CNY",
                subscription_price_minor=0,
                status="active",
            )
        )
        db.flush()
        db.add(
            BillingPlanEntitlement(
                id=uuid4(),
                plan_id=plan_id,
                resource_code="api_requests",
                unit="requests",
                included_units=1,
                hard_limit_units=1,
                overage_rate_minor_per_unit=0,
                overage_behavior="block",
            )
        )
        db.add(
            TenantSubscription(
                id=uuid4(),
                tenant_id=tenant_id,
                plan_id=plan_id,
                status="active",
                current_period_start=now - timedelta(minutes=1),
                current_period_end=now + timedelta(days=30),
            )
        )
        db.commit()

    barrier = Barrier(2)

    def attempt(key: str) -> str:
        barrier.wait(timeout=10)
        with postgres_session_factory() as db:
            try:
                reserve_gateway_request(
                    db,
                    tenant_id=tenant_id,
                    idempotency_key=key,
                    request_hash=(key.encode().hex() + "0" * 64)[:64],
                    model_source="byok",
                    input_token_reservation=0,
                    output_token_reservation=0,
                    price_version_id=None,
                )
                return "reserved"
            except HTTPException as exc:
                if exc.status_code == 429 and exc.detail["code"] == "quota_exceeded":
                    return "quota_exceeded"
                raise

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(attempt, ["parallel-a", "parallel-b"]))

    assert sorted(outcomes) == ["quota_exceeded", "reserved"]
    with postgres_session_factory() as db:
        reservations = db.scalar(
            select(func.count(BillingReservation.id)).where(
                BillingReservation.tenant_id == tenant_id
            )
        )
        assert reservations == 1
