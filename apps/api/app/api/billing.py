from datetime import UTC, datetime
from uuid import UUID, uuid4

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.api.deps import CurrentPrincipal, DatabaseSession
from app.api.platform_admin import require_platform_admin
from app.billing.entitlements import (
    _subscription_read,
    build_entitlement_snapshot,
    get_current_subscription,
)
from app.models.billing import (
    BillingPlan,
    BillingPlanEntitlement,
    TenantBillingPolicy,
    TenantSubscription,
)
from app.models.identity import Tenant
from app.schemas.billing import (
    BillingEntitlementsRead,
    BillingPlanCreate,
    BillingPlanEntitlementRead,
    BillingPlanRead,
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
) -> BillingPlanRead:
    require_platform_admin(principal)
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
) -> TenantSubscriptionRead:
    require_platform_admin(principal)
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
