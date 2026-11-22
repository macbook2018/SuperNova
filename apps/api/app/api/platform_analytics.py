from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Query
from sqlalchemy import case, func, select

from app.api.deps import CurrentPrincipal, DatabaseSession
from app.api.platform_admin import require_platform_permission
from app.models.identity import Tenant, TenantMembership, User
from app.models.usage import UsageRecord

router = APIRouter(tags=["平台运营统计"])


@router.get("/admin/platform/analytics/overview")
def platform_analytics_overview(
    principal: CurrentPrincipal,
    db: DatabaseSession,
    days: int = Query(default=30, ge=7, le=90),
) -> dict:
    require_platform_permission(principal, "platform.analytics.read")
    now = datetime.now(UTC)
    period_start = now - timedelta(days=days)

    user_count = db.scalar(select(func.count(User.id))) or 0
    active_user_count = db.scalar(
        select(func.count(User.id)).where(User.status == "active")
    ) or 0
    tenant_count = db.scalar(select(func.count(Tenant.id))) or 0
    active_tenant_count = db.scalar(
        select(func.count(Tenant.id)).where(Tenant.status == "active")
    ) or 0
    active_membership_count = db.scalar(
        select(func.count(TenantMembership.id)).where(
            TenantMembership.status == "active"
        )
    ) or 0

    totals = db.execute(
        select(
            func.count(UsageRecord.id),
            func.coalesce(
                func.sum(case((UsageRecord.status == "succeeded", 1), else_=0)), 0
            ),
            func.coalesce(
                func.sum(case((UsageRecord.status != "succeeded", 1), else_=0)), 0
            ),
            func.coalesce(func.sum(UsageRecord.prompt_tokens), 0),
            func.coalesce(func.sum(UsageRecord.completion_tokens), 0),
            func.coalesce(func.sum(UsageRecord.total_tokens), 0),
        ).where(UsageRecord.created_at >= period_start)
    ).one()

    source_rows = db.execute(
        select(
            UsageRecord.model_source,
            func.count(UsageRecord.id),
            func.coalesce(func.sum(UsageRecord.total_tokens), 0),
        )
        .where(UsageRecord.created_at >= period_start)
        .group_by(UsageRecord.model_source)
        .order_by(UsageRecord.model_source)
    ).all()

    date_key = func.date(UsageRecord.created_at)
    daily_rows = db.execute(
        select(
            date_key,
            func.count(UsageRecord.id),
            func.coalesce(
                func.sum(case((UsageRecord.status == "succeeded", 1), else_=0)), 0
            ),
            func.coalesce(func.sum(UsageRecord.total_tokens), 0),
        )
        .where(UsageRecord.created_at >= period_start)
        .group_by(date_key)
        .order_by(date_key)
    ).all()

    model_rows = db.execute(
        select(
            UsageRecord.provider_type,
            UsageRecord.model_name,
            func.count(UsageRecord.id),
            func.coalesce(func.sum(UsageRecord.total_tokens), 0),
        )
        .where(UsageRecord.created_at >= period_start)
        .group_by(UsageRecord.provider_type, UsageRecord.model_name)
        .order_by(func.count(UsageRecord.id).desc(), UsageRecord.model_name)
        .limit(10)
    ).all()

    return {
        "period": {
            "days": days,
            "start": period_start.isoformat(),
            "end": now.isoformat(),
        },
        "accounts": {
            "users": int(user_count),
            "active_users": int(active_user_count),
            "tenants": int(tenant_count),
            "active_tenants": int(active_tenant_count),
            "active_memberships": int(active_membership_count),
        },
        "usage": {
            "requests": int(totals[0]),
            "successful_requests": int(totals[1]),
            "failed_requests": int(totals[2]),
            "prompt_tokens": int(totals[3]),
            "completion_tokens": int(totals[4]),
            "total_tokens": int(totals[5]),
        },
        "by_model_source": [
            {
                "model_source": row[0],
                "requests": int(row[1]),
                "total_tokens": int(row[2]),
            }
            for row in source_rows
        ],
        "daily": [
            {
                "date": str(row[0]),
                "requests": int(row[1]),
                "successful_requests": int(row[2]),
                "total_tokens": int(row[3]),
            }
            for row in daily_rows
        ],
        "top_models": [
            {
                "provider_type": row[0],
                "model_name": row[1],
                "requests": int(row[2]),
                "total_tokens": int(row[3]),
            }
            for row in model_rows
        ],
    }
