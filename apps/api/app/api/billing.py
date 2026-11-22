from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID, uuid4

from fastapi import APIRouter, Header, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.api.deps import CurrentPrincipal, DatabaseSession
from app.api.platform_admin import record_platform_audit, require_platform_permission
from app.billing.entitlements import (
    _subscription_read,
    build_entitlement_snapshot,
    get_current_subscription,
)
from app.billing.metering import (
    mark_stale_reservations_unknown,
    release_unknown_reservation,
    run_billing_reconciliation,
    settle_unknown_reservation_with_usage,
)
from app.billing.money import currency_minor_unit_exponent
from app.models.billing import (
    BillingLedgerAccount,
    BillingLedgerPosting,
    BillingLedgerTransaction,
    BillingPlan,
    BillingPlanEntitlement,
    BillingRatedItem,
    BillingReconciliationRun,
    BillingReservation,
    BillingReservationItem,
    BillingUsageEvent,
    TenantBillingPolicy,
    TenantSubscription,
)
from app.models.identity import Tenant
from app.schemas.billing import (
    BillingEntitlementsRead,
    BillingLedgerPostingRead,
    BillingLedgerTransactionRead,
    BillingPlanCreate,
    BillingPlanEntitlementRead,
    BillingPlanRead,
    BillingReconciliationRunCreate,
    BillingReconciliationRunRead,
    BillingReservationRelease,
    BillingReservationSettlement,
    BillingShadowUsageSummaryRead,
    TenantSubscriptionCreate,
    TenantSubscriptionRead,
)

router = APIRouter(tags=["套餐与订阅"])


def _plan_read(db, plan: BillingPlan) -> BillingPlanRead:
    entitlements = db.scalars(
        select(BillingPlanEntitlement)
        .where(BillingPlanEntitlement.plan_id == plan.id)
        .order_by(BillingPlanEntitlement.resource_code.asc())
    ).all()
    return BillingPlanRead(
        id=plan.id,
        code=plan.code,
        revision=plan.revision,
        name=plan.name,
        billing_interval=plan.billing_interval,
        currency=plan.currency,
        subscription_price_minor=plan.subscription_price_minor,
        status=plan.status,
        entitlements=[BillingPlanEntitlementRead.model_validate(item) for item in entitlements],
        created_at=plan.created_at,
    )


@router.get("/billing/plans", response_model=list[BillingPlanRead])
def list_billing_plans(
    principal: CurrentPrincipal,
    db: DatabaseSession,
) -> list[BillingPlanRead]:
    plans = db.scalars(
        select(BillingPlan)
        .where(BillingPlan.status == "active")
        .order_by(BillingPlan.subscription_price_minor.asc(), BillingPlan.code.asc())
    ).all()
    return [_plan_read(db, plan) for plan in plans]


@router.get("/billing/subscription", response_model=TenantSubscriptionRead | None)
def get_billing_subscription(
    principal: CurrentPrincipal,
    db: DatabaseSession,
) -> TenantSubscriptionRead | None:
    current = get_current_subscription(db, principal.tenant.id)
    if current is None:
        return None
    return _subscription_read(*current)


@router.get("/billing/entitlements", response_model=BillingEntitlementsRead)
def get_billing_entitlements(
    principal: CurrentPrincipal,
    db: DatabaseSession,
) -> BillingEntitlementsRead:
    return build_entitlement_snapshot(db, principal.tenant.id)


@router.post(
    "/admin/billing/plans",
    response_model=BillingPlanRead,
    status_code=status.HTTP_201_CREATED,
)
def create_billing_plan(
    payload: BillingPlanCreate,
    principal: CurrentPrincipal,
    db: DatabaseSession,
    request_id: str | None = Header(default=None, alias="X-Request-ID"),
) -> BillingPlanRead:
    require_platform_permission(principal, "platform.billing.manage")
    plan = BillingPlan(
        id=uuid4(),
        code=payload.code,
        revision=payload.revision,
        name=payload.name,
        billing_interval=payload.billing_interval,
        currency=payload.currency,
        subscription_price_minor=payload.subscription_price_minor,
        status=payload.status,
    )
    db.add(plan)
    record_platform_audit(
        db, actor_user_id=principal.user.id, target_user_id=None,
        action="platform.billing.plan.create", resource_type="billing_plan",
        resource_id=f"{plan.code}:{plan.revision}",
        reason="创建套餐版本",
        request_id=request_id,
        details={
            "currency": plan.currency,
            "subscription_price_minor": plan.subscription_price_minor,
        },
    )
    try:
        db.flush()
        db.add_all(
            [
                BillingPlanEntitlement(
                    id=uuid4(),
                    plan_id=plan.id,
                    resource_code=item.resource_code,
                    unit=item.unit,
                    included_units=item.included_units,
                    hard_limit_units=item.hard_limit_units,
                    overage_rate_minor_per_unit=item.overage_rate_minor_per_unit,
                    overage_behavior=item.overage_behavior,
                )
                for item in payload.entitlements
            ]
        )
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail={
                "code": "billing_plan_revision_exists",
                "message": "该套餐编码和版本已经存在；请创建新的 revision",
            },
        ) from None
    db.refresh(plan)
    return _plan_read(db, plan)


@router.post(
    "/admin/billing/tenants/{tenant_id}/subscription",
    response_model=TenantSubscriptionRead,
    status_code=status.HTTP_201_CREATED,
)
def assign_tenant_subscription(
    tenant_id: UUID,
    payload: TenantSubscriptionCreate,
    principal: CurrentPrincipal,
    db: DatabaseSession,
    request_id: str | None = Header(default=None, alias="X-Request-ID"),
) -> TenantSubscriptionRead:
    require_platform_permission(principal, "platform.billing.manage")
    tenant = db.get(Tenant, tenant_id)
    if tenant is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "tenant_not_found", "message": "工作空间不存在"},
        )
    plan = db.get(BillingPlan, payload.plan_id)
    if plan is None or plan.status != "active":
        raise HTTPException(
            status_code=409,
            detail={"code": "billing_plan_unavailable", "message": "只能分配已发布的套餐"},
        )

    now = datetime.now(UTC)
    existing = db.scalar(
        select(TenantSubscription).where(
            TenantSubscription.tenant_id == tenant_id,
            TenantSubscription.status.in_(("active", "trialing")),
            TenantSubscription.current_period_end > now,
        )
    )
    if existing is not None:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "tenant_subscription_exists",
                "message": "该工作空间已有有效或待生效订阅，请先处理现有订阅",
            },
        )

    expired = db.scalars(
        select(TenantSubscription).where(
            TenantSubscription.tenant_id == tenant_id,
            TenantSubscription.status.in_(("active", "trialing")),
            TenantSubscription.current_period_end <= now,
        )
    ).all()
    for item in expired:
        item.status = "expired"

    subscription = TenantSubscription(
        id=uuid4(),
        tenant_id=tenant_id,
        plan_id=plan.id,
        status=payload.status,
        current_period_start=payload.current_period_start,
        current_period_end=payload.current_period_end,
    )
    db.add(subscription)
    record_platform_audit(
        db, actor_user_id=principal.user.id, target_user_id=None,
        action="platform.billing.subscription.assign", resource_type="tenant_subscription",
        resource_id=str(subscription.id), reason="分配租户订阅",
        request_id=request_id,
        details={"tenant_id": str(tenant_id), "plan_id": str(plan.id), "status": payload.status},
    )
    policy = db.get(TenantBillingPolicy, tenant_id)
    if policy is None:
        policy = TenantBillingPolicy(tenant_id=tenant_id)
        db.add(policy)
    policy.currency = plan.currency
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail={
                "code": "tenant_subscription_exists",
                "message": "该工作空间已有有效订阅，无法重复分配",
            },
        ) from None
    db.refresh(subscription)
    return TenantSubscriptionRead(
        id=subscription.id,
        tenant_id=tenant_id,
        plan_id=plan.id,
        plan_code=plan.code,
        plan_name=plan.name,
        status=subscription.status,
        current_period_start=subscription.current_period_start,
        current_period_end=subscription.current_period_end,
        cancel_at_period_end=subscription.cancel_at_period_end,
        created_at=subscription.created_at,
    )


@router.get("/billing/usage", response_model=BillingShadowUsageSummaryRead)
def billing_usage_summary(
    principal: CurrentPrincipal,
    db: DatabaseSession,
) -> BillingShadowUsageSummaryRead:
    now = datetime.now(UTC)
    current = get_current_subscription(db, principal.tenant.id, now)
    if current is not None:
        subscription, plan = current
        period_start = subscription.current_period_start
        period_end = subscription.current_period_end
        currency = plan.currency
    else:
        policy = db.get(TenantBillingPolicy, principal.tenant.id)
        currency = policy.currency if policy is not None else "CNY"
        period_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        if period_start.month == 12:
            period_end = period_start.replace(year=period_start.year + 1, month=1)
        else:
            period_end = period_start.replace(month=period_start.month + 1)

    event_totals = db.execute(
        select(
            BillingUsageEvent.resource_code,
            func.coalesce(func.sum(BillingUsageEvent.quantity_units), 0),
        )
        .where(
            BillingUsageEvent.tenant_id == principal.tenant.id,
            BillingUsageEvent.occurred_at >= period_start,
            BillingUsageEvent.occurred_at < period_end,
        )
        .group_by(BillingUsageEvent.resource_code)
    ).all()
    quantities = {row[0]: int(row[1]) for row in event_totals}

    estimated_amount = db.scalar(
        select(func.coalesce(func.sum(BillingRatedItem.amount_minor_decimal), 0))
        .join(
            BillingUsageEvent,
            BillingUsageEvent.id == BillingRatedItem.usage_event_id,
        )
        .where(
            BillingUsageEvent.tenant_id == principal.tenant.id,
            BillingUsageEvent.occurred_at >= period_start,
            BillingUsageEvent.occurred_at < period_end,
            BillingRatedItem.currency == currency,
            BillingRatedItem.state == "shadow",
        )
    )
    rated_items = int(
        db.scalar(
            select(func.count(BillingRatedItem.id))
            .join(
                BillingUsageEvent,
                BillingUsageEvent.id == BillingRatedItem.usage_event_id,
            )
            .where(
                BillingUsageEvent.tenant_id == principal.tenant.id,
                BillingUsageEvent.occurred_at >= period_start,
                BillingUsageEvent.occurred_at < period_end,
                BillingRatedItem.state == "shadow",
            )
        )
        or 0
    )
    unrated_events = int(
        db.scalar(
            select(func.count(BillingUsageEvent.id))
            .outerjoin(
                BillingRatedItem,
                BillingRatedItem.usage_event_id == BillingUsageEvent.id,
            )
            .where(
                BillingUsageEvent.tenant_id == principal.tenant.id,
                BillingUsageEvent.occurred_at >= period_start,
                BillingUsageEvent.occurred_at < period_end,
                (BillingRatedItem.id.is_(None) | (BillingRatedItem.state == "unrated")),
            )
        )
        or 0
    )
    unknown_reservations = int(
        db.scalar(
            select(func.count(BillingReservation.id)).where(
                BillingReservation.tenant_id == principal.tenant.id,
                BillingReservation.state == "unknown",
            )
        )
        or 0
    )
    return BillingShadowUsageSummaryRead(
        period_start=period_start,
        period_end=period_end,
        currency=currency,
        minor_unit_exponent=currency_minor_unit_exponent(currency),
        estimated_amount_minor=Decimal(estimated_amount or 0),
        shadow_rated_items=rated_items,
        platform_input_tokens=quantities.get("platform_input_tokens", 0),
        platform_output_tokens=quantities.get("platform_output_tokens", 0),
        byok_input_tokens=quantities.get("byok_input_tokens", 0),
        byok_output_tokens=quantities.get("byok_output_tokens", 0),
        unrated_events=unrated_events,
        unknown_reservations=unknown_reservations,
    )


@router.get("/billing/ledger", response_model=list[BillingLedgerTransactionRead])
def billing_ledger(
    principal: CurrentPrincipal,
    db: DatabaseSession,
    limit: int = Query(default=50, ge=1, le=100),
) -> list[BillingLedgerTransactionRead]:
    transactions = db.scalars(
        select(BillingLedgerTransaction)
        .join(
            BillingLedgerPosting,
            BillingLedgerPosting.transaction_id == BillingLedgerTransaction.id,
        )
        .join(
            BillingLedgerAccount,
            BillingLedgerAccount.id == BillingLedgerPosting.account_id,
        )
        .where(
            BillingLedgerAccount.tenant_id == principal.tenant.id,
            BillingLedgerTransaction.mode == "shadow",
        )
        .distinct()
        .order_by(BillingLedgerTransaction.created_at.desc())
        .limit(limit)
    ).all()
    result = []
    for transaction in transactions:
        rows = db.execute(
            select(BillingLedgerAccount.kind, BillingLedgerPosting.amount_minor)
            .join(
                BillingLedgerPosting,
                BillingLedgerPosting.account_id == BillingLedgerAccount.id,
            )
            .where(BillingLedgerPosting.transaction_id == transaction.id)
            .order_by(BillingLedgerPosting.created_at.asc())
        ).all()
        result.append(
            BillingLedgerTransactionRead(
                id=transaction.id,
                kind=transaction.kind,
                mode=transaction.mode,
                currency=transaction.currency,
                reference_id=transaction.reference_id,
                created_at=transaction.created_at,
                postings=[
                    BillingLedgerPostingRead(
                        account_kind=row[0],
                        amount_minor=row[1],
                    )
                    for row in rows
                ],
            )
        )
    return result


@router.post(
    "/admin/billing/reconciliation/run",
    response_model=BillingReconciliationRunRead,
    status_code=status.HTTP_201_CREATED,
)
def create_billing_reconciliation(
    payload: BillingReconciliationRunCreate,
    principal: CurrentPrincipal,
    db: DatabaseSession,
    request_id: str | None = Header(default=None, alias="X-Request-ID"),
) -> BillingReconciliationRunRead:
    require_platform_permission(principal, "platform.billing.manage")
    run = run_billing_reconciliation(
        db,
        actor_user_id=principal.user.id,
        period_start=payload.period_start,
        period_end=payload.period_end,
    )
    record_platform_audit(
        db, actor_user_id=principal.user.id, target_user_id=None,
        action="platform.billing.reconciliation.run", resource_type="billing_reconciliation",
        resource_id=str(run.id), reason="执行账务对账", request_id=request_id,
        details={
            "period_start": payload.period_start.isoformat(),
            "period_end": payload.period_end.isoformat(),
            "status": run.status,
        },
    )
    db.commit()
    return BillingReconciliationRunRead.model_validate(run)


@router.get(
    "/admin/billing/reconciliation/runs",
    response_model=list[BillingReconciliationRunRead],
)
def list_billing_reconciliation_runs(
    principal: CurrentPrincipal,
    db: DatabaseSession,
    limit: int = Query(default=20, ge=1, le=100),
) -> list[BillingReconciliationRunRead]:
    require_platform_permission(principal, "platform.billing.read")
    rows = db.scalars(
        select(BillingReconciliationRun)
        .order_by(BillingReconciliationRun.created_at.desc())
        .limit(limit)
    ).all()
    return [BillingReconciliationRunRead.model_validate(item) for item in rows]


@router.post("/admin/billing/reservations/mark-expired-unknown")
def mark_expired_billing_reservations(
    principal: CurrentPrincipal,
    db: DatabaseSession,
) -> dict[str, int]:
    require_platform_permission(principal, "platform.billing.manage")
    count = mark_stale_reservations_unknown(
        db,
        actor_user_id=principal.user.id,
    )
    return {"marked_unknown": count}


@router.get("/admin/billing/reservations/unknown")
def list_unknown_billing_reservations(
    principal: CurrentPrincipal,
    db: DatabaseSession,
    limit: int = Query(default=50, ge=1, le=200),
) -> list[dict]:
    require_platform_permission(principal, "platform.billing.read")
    reservations = db.scalars(
        select(BillingReservation)
        .where(BillingReservation.state == "unknown")
        .order_by(BillingReservation.created_at.asc())
        .limit(limit)
    ).all()
    result = []
    for reservation in reservations:
        items = db.scalars(
            select(BillingReservationItem).where(
                BillingReservationItem.reservation_id == reservation.id
            )
        ).all()
        result.append(
            {
                "id": str(reservation.id),
                "tenant_id": str(reservation.tenant_id),
                "event_key": reservation.event_key,
                "model_source": reservation.model_source,
                "created_at": reservation.created_at.isoformat(),
                "expires_at": reservation.expires_at.isoformat(),
                "resources": [
                    {
                        "resource_code": item.resource_code,
                        "reserved_units": item.reserved_units,
                        "consumed_units": item.consumed_units,
                        "state": item.state,
                    }
                    for item in items
                ],
            }
        )
    return result


@router.post("/admin/billing/reservations/{reservation_id}/settle")
def settle_unknown_billing_reservation(
    reservation_id: UUID,
    payload: BillingReservationSettlement,
    principal: CurrentPrincipal,
    db: DatabaseSession,
) -> dict[str, str]:
    require_platform_permission(principal, "platform.billing.manage")
    settle_unknown_reservation_with_usage(
        db,
        reservation_id=reservation_id,
        actor_user_id=principal.user.id,
        prompt_tokens=payload.prompt_tokens,
        completion_tokens=payload.completion_tokens,
        reason=payload.reason,
    )
    return {"status": "settled"}


@router.post("/admin/billing/reservations/{reservation_id}/release")
def release_unknown_billing_reservation(
    reservation_id: UUID,
    payload: BillingReservationRelease,
    principal: CurrentPrincipal,
    db: DatabaseSession,
) -> dict[str, str]:
    require_platform_permission(principal, "platform.billing.manage")
    release_unknown_reservation(
        db,
        reservation_id=reservation_id,
        actor_user_id=principal.user.id,
        reason=payload.reason,
    )
    return {"status": "released"}
