import asyncio

import psycopg
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from redis.asyncio import Redis

router = APIRouter(tags=["health"])


async def _check_database(database_url: str) -> str:
    connection = None
    try:
        connection = await psycopg.AsyncConnection.connect(
            database_url,
            connect_timeout=2,
        )
        await connection.execute("SELECT 1")
        return "ok"
    except Exception:
        return "error"
    finally:
        if connection is not None:
            await connection.close()


async def _check_redis(redis_url: str) -> str:
    client = Redis.from_url(
        redis_url,
        socket_connect_timeout=2,
        socket_timeout=2,
    )
    try:
        await client.ping()
        return "ok"
    except Exception:
        return "error"
    finally:
        await client.aclose()


@router.get("/health/live")
async def liveness() -> dict[str, str]:
    return {"status": "alive"}


@router.get("/health/ready")
async def readiness(request: Request) -> JSONResponse:
    settings = request.app.state.settings
    database, redis = await asyncio.gather(
        _check_database(settings.database_url),
        _check_redis(settings.redis_url),
    )
    dependencies = {"postgres": database, "redis": redis}
    ready = all(status == "ok" for status in dependencies.values())

    return JSONResponse(
        status_code=200 if ready else 503,
        content={
            "status": "ready" if ready else "not_ready",
            "dependencies": dependencies,
        },
    )
