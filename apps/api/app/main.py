from uuid import UUID, uuid4

import structlog
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.api.health import router as health_router
from app.core.config import get_settings
from app.core.logging import configure_logging

settings = get_settings()
configure_logging(settings.log_level)
logger = structlog.get_logger()

app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    description="SuperNova control plane and OpenAI-compatible gateway.",
)
app.state.settings = settings
app.include_router(health_router)


@app.middleware("http")
async def request_id_middleware(request: Request, call_next):
    supplied_request_id = request.headers.get("X-Request-ID")
    try:
        request_id = str(UUID(supplied_request_id)) if supplied_request_id else str(uuid4())
    except ValueError:
        request_id = str(uuid4())

    structlog.contextvars.clear_contextvars()
    structlog.contextvars.bind_contextvars(request_id=request_id)
    request.state.request_id = request_id

    response = await call_next(request)
    response.headers["X-Request-ID"] = request_id
    return response


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    request_id = getattr(request.state, "request_id", None)
    logger.exception(
        "unhandled_exception",
        path=request.url.path,
        request_id=request_id,
    )
    headers = {"X-Request-ID": request_id} if request_id else None
    return JSONResponse(
        status_code=500,
        headers=headers,
        content={
            "error": {
                "code": "internal_error",
                "message": "Internal server error",
                "request_id": request_id,
            }
        },
    )
