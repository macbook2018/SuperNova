import asyncio
from datetime import UTC, datetime

from fastapi import APIRouter, Request
from sqlalchemy import func, select

from app.api.deps import CurrentPrincipal, DatabaseSession
from app.api.health import _check_database, _check_redis
from app.api.platform_admin import require_platform_permission
from app.core.config import get_settings
from app.models.chat_analysis import ChatAnalysisJob
from app.models.chat_import import ChatImportJob
from app.models.knowledge import KnowledgeIngestionJob

router = APIRouter(tags=["平台运行监控"])


def _queue_summary(db, model, *, stale_statuses: tuple[str, ...], now: datetime) -> dict:
    rows = db.execute(
        select(model.status, func.count(model.id)).group_by(model.status)
    ).all()
    counts = {str(status): int(count) for status, count in rows}
    stale_jobs = db.scalar(
        select(func.count(model.id)).where(
            model.status.in_(stale_statuses),
            model.lease_expires_at.is_not(None),
            model.lease_expires_at < now,
        )
    ) or 0
    return {
        "total": sum(counts.values()),
        "status_counts": counts,
        "stale_leases": int(stale_jobs),
    }


@router.get("/admin/platform/operations/overview")
async def platform_operations_overview(
    request: Request,
    principal: CurrentPrincipal,
    db: DatabaseSession,
) -> dict:
    require_platform_permission(principal, "platform.operations.read")
    settings = request.app.state.settings
    database, redis = await asyncio.gather(
        _check_database(settings.database_url),
        _check_redis(settings.redis_url),
    )
    now = datetime.now(UTC)
    queues = {
        "knowledge_ingestion": _queue_summary(
            db,
            KnowledgeIngestionJob,
            stale_statuses=("running",),
            now=now,
        ),
        "chat_import": _queue_summary(
            db,
            ChatImportJob,
            stale_statuses=("processing",),
            now=now,
        ),
        "chat_analysis": _queue_summary(
            db,
            ChatAnalysisJob,
            stale_statuses=("running",),
            now=now,
        ),
    }
    stale_total = sum(item["stale_leases"] for item in queues.values())
    dependencies = {"postgres": database, "redis": redis}
    overall = (
        "attention"
        if stale_total or any(value != "ok" for value in dependencies.values())
        else "healthy"
    )
    return {
        "checked_at": now.isoformat(),
        "overall": overall,
        "dependencies": dependencies,
        "queues": queues,
    }
