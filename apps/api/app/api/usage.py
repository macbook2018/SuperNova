from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Query
from sqlalchemy import case, func, select

from app.api.deps import CurrentPrincipal, DatabaseSession
from app.models.usage import UsageRecord
from app.schemas.usage import UsageRecordRead, UsageSummary, UsageTotals

router = APIRouter(prefix="/usage", tags=["Token 用量"])


def _totals(db: Any, tenant_id, user_id, since: datetime | None = None) -> UsageTotals:
    statement = select(
        func.count(UsageRecord.id),
        func.coalesce(func.sum(case((UsageRecord.status == "succeeded", 1), else_=0)), 0),
        func.coalesce(func.sum(case((UsageRecord.status != "succeeded", 1), else_=0)), 0),
        func.coalesce(func.sum(UsageRecord.prompt_tokens), 0),
        func.coalesce(func.sum(UsageRecord.completion_tokens), 0),
        func.coalesce(func.sum(UsageRecord.total_tokens), 0),
        func.coalesce(func.sum(case((UsageRecord.total_tokens.is_(None), 1), else_=0)), 0),
    ).where(
        UsageRecord.tenant_id == tenant_id,
        UsageRecord.user_id == user_id,
    )
    if since is not None:
        statement = statement.where(UsageRecord.created_at >= since)
    row = db.execute(statement).one()
    return UsageTotals(
        requests=int(row[0]),
        successful_requests=int(row[1]),
        failed_requests=int(row[2]),
        prompt_tokens=int(row[3]),
        completion_tokens=int(row[4]),
        total_tokens=int(row[5]),
        unmetered_requests=int(row[6]),
    )


@router.get("/summary", response_model=UsageSummary)
def usage_summary(
    principal: CurrentPrincipal,
    db: DatabaseSession,
) -> UsageSummary:
    now = datetime.now(UTC)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return UsageSummary(
        month_start=month_start,
        current_month=_totals(db, principal.tenant.id, principal.user.id, month_start),
        all_time=_totals(db, principal.tenant.id, principal.user.id),
    )


@router.get("/records", response_model=list[UsageRecordRead])
def usage_records(
    principal: CurrentPrincipal,
    db: DatabaseSession,
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0, le=100000),
) -> list[UsageRecordRead]:
    records = db.scalars(
        select(UsageRecord)
        .where(
            UsageRecord.tenant_id == principal.tenant.id,
            UsageRecord.user_id == principal.user.id,
        )
        .order_by(UsageRecord.created_at.desc())
        .limit(limit)
        .offset(offset)
    ).all()
    return [UsageRecordRead.model_validate(record) for record in records]
