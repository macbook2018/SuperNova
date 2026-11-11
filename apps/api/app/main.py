from uuid import UUID, uuid4

import structlog
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api.health import router as health_router
from app.core.config import get_settings
from app.core.logging import configure_logging

settings = get_settings()
configure_logging(settings.log_level)
logger = structlog.get_logger()

HTTP_ERROR_MESSAGES = {
    400: "请求内容不正确，请检查后重试。",
    401: "登录状态无效或已过期，请重新登录。",
    403: "你没有权限执行此操作。",
    404: "请求的接口或资源不存在。",
    405: "当前请求方式不受支持。",
    409: "请求与当前资源状态冲突，请刷新后重试。",
    422: "请求参数不符合要求，请检查必填项、字段类型和格式。",
    429: "请求过于频繁，请稍后重试。",
}


app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    description=(
        "SuperNova 是基于 AstrBot 构建的多租户智能体服务平台。"
        "平台支持独立人格、知识库、模型配置、微信渠道和兼容 OpenAI 的统一接口。"
    ),
    openapi_tags=[
        {
            "name": "运行状态",
            "description": "检查 SuperNova API 服务及其依赖组件的运行状态。",
        }
    ],
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


@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(
    request: Request,
    exc: StarletteHTTPException,
) -> JSONResponse:
    request_id = getattr(request.state, "request_id", None)
    message = HTTP_ERROR_MESSAGES.get(exc.status_code)
    if message is None:
        message = str(exc.detail) if isinstance(exc.detail, str) else "请求未能完成，请稍后重试。"

    headers = dict(exc.headers or {})
    if request_id:
        headers["X-Request-ID"] = request_id

    return JSONResponse(
        status_code=exc.status_code,
        headers=headers,
        content={"detail": message, "request_id": request_id},
    )


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
                "message": "服务器内部发生错误，请稍后重试；如需协助，请提供请求编号。",
                "request_id": request_id,
            }
        },
    )
