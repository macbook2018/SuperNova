from datetime import UTC, datetime
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.billing import (
    BillingPlan,
    BillingPlanEntitlement,
    TenantBillingPolicy,
    TenantSubscription,
)
from app.models.usage import UsageRecord
from app.schemas.billing import (
    BillingEntitlementsRead,
    BillingResourceUsageRead,
    TenantSubscriptionRead,
)


def _aware(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


def get_current_subscription(
    db: Session,
    tenant_id: UUID,
    now: datetime | None = None,
) -> tuple[TenantSubscription, BillingPlan] | None:
    current = now or datetime.now(UTC)
    row = db.execute(
        select(TenantSubscription, BillingPlan)
        .join(BillingPlan, BillingPlan.id == TenantSubscription.plan_id)
        .where(
            TenantSubscription.tenant_id == tenant_id,
            TenantSubscription.status.in_(("active", "trialing")),
            TenantSubscription.current_period_start <= current,
            TenantSubscription.current_period_end > current,
        )
        .order_by(TenantSubscription.created_at.desc())
    ).first()
    return (row[0], row[1]) if row else None


def _subscription_read(
    subscription: TenantSubscription,
    plan: BillingPlan,
) -> TenantSubscriptionRead:
    return TenantSubscriptionRead(
        id=subscription.id,
        tenant_id=subscription.tenant_id,
        plan_id=plan.id,
        plan_code=plan.code,
        plan_name=plan.name,
        status=subscription.status,
        current_period_start=subscription.current_period_start,
        current_period_end=subscription.current_period_end,
        cancel_at_period_end=subscription.cancel_at_period_end,
        created_at=subscription.created_at,
    )


def _period_usage(
    db: Session,
    tenant_id: UUID,
    start: datetime,
    end: datetime,
    resource_code: str,
) -> int:
    base = (
        UsageRecord.tenant_id == tenant_id,
        UsageRecord.created_at >= start,
        UsageRecord.created_at < end,
    )
    if resource_code == "api_requests":
        value = db.scalar(select(func.count(UsageRecord.id)).where(*base))
    elif resource_code == "platform_model_requests":
        value = db.scalar(
            select(func.count(UsageRecord.id)).where(
                *base, UsageRecord.model_source == "platform"
            )
        )
    elif resource_code == "platform_input_tokens":
        value = db.scalar(
            select(func.coalesce(func.sum(UsageRecord.prompt_tokens), 0)).where(
                *base,
                UsageRecord.model_source == "platform",
                UsageRecord.status == "succeeded",
            )
        )
    elif resource_code == "platform_output_tokens":
        value = db.scalar(
            select(func.coalesce(func.sum(UsageRecord.completion_tokens), 0)).where(
                *base,
                UsageRecord.model_source == "platform",
                UsageRecord.status == "succeeded",
            )
        )
    else:
        value = 0
    return int(value or 0)


def _effective_limit(entitlement: BillingPlanEntitlement) -> int:
    # Phase 5B deliberately blocks at included usage. Overage billing is not
    # active until Phase 5C/5D have reservations and settlement.
    if entitlement.hard_limit_units is None:
        return entitlement.included_units
    return min(entitlement.included_units, entitlement.hard_limit_units)


def _entitlement_map(db: Session, plan_id: UUID) -> dict[str, BillingPlanEntitlement]:
    rows = db.scalars(
        select(BillingPlanEntitlement).where(BillingPlanEntitlement.plan_id == plan_id)
    ).all()
    return {item.resource_code: item for item in rows}


def build_entitlement_snapshot(db: Session, tenant_id: UUID) -> BillingEntitlementsRead:
    policy = db.get(TenantBillingPolicy, tenant_id)
    if policy is None:
        return BillingEntitlementsRead(
            legacy_unmetered=True,
            subscription=None,
            resources=[],
        )
    current = get_current_subscription(db, tenant_id)
    if current is None:
        return BillingEntitlementsRead(
            legacy_unmetered=False,
            subscription=None,
            resources=[],
        )
    subscription, plan = current
    entitlements = _entitlement_map(db, plan.id)
    resources = []
    for code, item in sorted(entitlements.items()):
        used = _period_usage(
            db,
            tenant_id,
            subscription.current_period_start,
            subscription.current_period_end,
            code,
        )
        limit = _effective_limit(item)
        resources.append(
            BillingResourceUsageRead(
                resource_code=code,
                unit=item.unit,
                included_units=item.included_units,
                hard_limit_units=item.hard_limit_units,
                used_units=used,
                remaining_units=max(0, limit - used),
                overage_behavior="block",
            )
        )
    return BillingEntitlementsRead(
        legacy_unmetered=False,
        subscription=_subscription_read(subscription, plan),
        resources=resources,
    )


def check_gateway_entitlements(
    db: Session,
    *,
    tenant_id: UUID,
    model_source: str,
    max_output_tokens: int,
) -> None:
    policy = db.get(TenantBillingPolicy, tenant_id)
    if policy is None and model_source == "byok":
        # Pre-Phase-5B tenants keep their existing BYOK service uninterrupted.
        return

    current = get_current_subscription(db, tenant_id)
    if current is None:
        raise HTTPException(
            status_code=403,
            detail={
                "code": "subscription_required",
                "message": "当前工作空间没有有效订阅，请先开通或联系平台管理员分配套餐",
            },
        )
    subscription, plan = current
    if plan.status != "active":
        raise HTTPException(
            status_code=403,
            detail={
                "code": "subscription_plan_unavailable",
                "message": "当前订阅套餐不可用，请联系平台管理员",
            },
        )

    entitlements = _entitlement_map(db, plan.id)
    required = ["api_requests"]
    if model_source == "platform":
        required.extend(
            ["platform_model_requests", "platform_input_tokens", "platform_output_tokens"]
        )
    for code in required:
        item = entitlements.get(code)
        if item is None:
            raise HTTPException(
                status_code=403,
                detail={
                    "code": "entitlement_not_configured",
                    "message": f"当前套餐未配置所需资源权益：{code}",
                },
            )
        used = _period_usage(
            db,
            tenant_id,
            subscription.current_period_start,
            subscription.current_period_end,
            code,
        )
        limit = _effective_limit(item)
        requested = max_output_tokens if code == "platform_output_tokens" else 0
        if used >= limit or (requested and used + requested > limit):
            raise HTTPException(
                status_code=429,
                detail={
                    "code": "quota_exceeded",
                    "message": (
                        f"套餐资源额度不足：{code}。"
                        "当前阶段不支持超额结算，请升级套餐或等待额度重置"
                    ),
                },
            )
