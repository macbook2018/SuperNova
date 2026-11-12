from uuid import UUID, uuid4

import structlog
from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api.api_keys import router as api_keys_router
from app.api.auth import router as auth_router
from app.api.billing import router as billing_router
from app.api.catalog import router as catalog_router
from app.api.gateway import router as gateway_router
from app.api.health import router as health_router
from app.api.platform_models import router as platform_models_router
from app.api.usage import router as usage_router
from app.core.config import get_settings
from app.core.logging import configure_logging

settings = get_settings()
configure_logging(settings.log_level)
logger = structlog.get_logger()

app = FastAPI(
    title=settings.app_name,
    version="0.4.0",
    description="SuperNova multi-tenant control plane and OpenAI-compatible gateway.",
)
app.state.settings = settings
app.include_router(health_router)
app.include_router(auth_router)
app.include_router(api_keys_router)
app.include_router(catalog_router)
app.include_router(platform_models_router)
app.include_router(billing_router)
app.include_router(gateway_router)
app.include_router(usage_router)


def _openai_error(status_code: int, code: str, message: str, param: str | None = None) -> dict:
    if status_code == 401:
        error_type = "authentication_error"
    elif status_code == 403:
        error_type = "permission_error"
    elif status_code == 429:
        error_type = "rate_limit_error"
    elif status_code >= 500:
        error_type = "server_error"
    else:
        error_type = "invalid_request_error"
    return {
        "error": {
            "message": message,
            "type": error_type,
            "param": param,
            "code": code,
        }
    }


@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException):
    if request.url.path.startswith("/v1/"):
        detail = exc.detail if isinstance(exc.detail, dict) else {}
        code = str(detail.get("code") or "request_failed")
        message = str(detail.get("message") or "请求失败")
        return JSONResponse(
            status_code=exc.status_code,
            headers=exc.headers,
            content=_openai_error(exc.status_code, code, message, detail.get("param")),
        )
    return JSONResponse(
        status_code=exc.status_code,
        headers=exc.headers,
        content={"detail": jsonable_encoder(exc.detail)},
    )


@app.exception_handler(RequestValidationError)
async def request_validation_exception_handler(request: Request, exc: RequestValidationError):
    if request.url.path.startswith("/v1/"):
        errors = exc.errors()
        first = errors[0] if errors else {}
        location = first.get("loc", ())
        param = ".".join(str(part) for part in location if part not in {"body", "query", "path"})
        message = str(first.get("msg") or "请求参数不符合要求")
        return JSONResponse(
            status_code=422,
            content=_openai_error(422, "invalid_request_error", message, param or None),
        )
    return JSONResponse(status_code=422, content={"detail": jsonable_encoder(exc.errors())})


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
    if request.url.path.startswith("/v1/"):
        return JSONResponse(
            status_code=500,
            headers=headers,
            content=_openai_error(500, "internal_error", "Internal server error"),
        )
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


# Wrap the complete FastAPI application so CORS headers are also attached to
# responses generated by Starlette's outer ServerErrorMiddleware.
asgi_app = CORSMiddleware(
    app=app,
    allow_origins=settings.allowed_origins,
    allow_credentials=False,
    allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-Request-ID", "Idempotency-Key"],
)
