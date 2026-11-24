from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from sqlalchemy import select

from app.api.deps import DatabaseSession
from app.api.gateway import GatewayPrincipal, create_chat_completion
from app.models.bot_runtime import BotRuntimeEvent
from app.runtime.deps import require_runtime_scope
from app.runtime.security import RuntimePrincipal
from app.schemas.bot_runtime import (
    RuntimeChatCompletionRequest,
    RuntimeEventEnvelope,
    RuntimeEventResponse,
    RuntimeHeartbeatRequest,
    RuntimeHeartbeatResponse,
)
from app.schemas.gateway import ChatCompletionRequest

router = APIRouter(prefix="/internal/runtime/v1", tags=["内部机器人运行时"])

RuntimeChatPrincipal = Annotated[
    RuntimePrincipal, Depends(require_runtime_scope("chat.completions"))
]
RuntimeHeartbeatPrincipal = Annotated[
    RuntimePrincipal, Depends(require_runtime_scope("runtime.heartbeat"))
]
RuntimeEventsPrincipal = Annotated[
    RuntimePrincipal, Depends(require_runtime_scope("runtime.events"))
]


@router.post("/heartbeat", response_model=RuntimeHeartbeatResponse)
def runtime_heartbeat(
    payload: RuntimeHeartbeatRequest,
    principal: RuntimeHeartbeatPrincipal,
    db: DatabaseSession,
) -> RuntimeHeartbeatResponse:
    bot = principal.bot
    if payload.config_version != bot.config_version:
        raise HTTPException(
            status_code=409,
            detail={"code": "runtime_config_stale", "message": "运行时配置版本已过期"},
        )
    if payload.runtime_version != bot.runtime_version:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "runtime_version_mismatch",
                "message": "运行时版本与实例固定版本不一致",
            },
        )

    now = datetime.now(UTC)
    bot.last_heartbeat_at = now
    if bot.desired_state == "running":
        bot.actual_state = payload.state
        bot.last_error_code = payload.health_code if payload.state == "unhealthy" else None
    db.commit()
    return RuntimeHeartbeatResponse(
        accepted=True,
        server_time=now,
        config_version=bot.config_version,
    )


@router.post("/events", response_model=RuntimeEventResponse)
def runtime_event(
    payload: RuntimeEventEnvelope,
    principal: RuntimeEventsPrincipal,
    db: DatabaseSession,
) -> RuntimeEventResponse:
    if (
        payload.tenant_id != principal.tenant.id
        or payload.bot_instance_id != principal.bot.id
    ):
        raise HTTPException(
            status_code=403,
            detail={"code": "runtime_event_scope_mismatch", "message": "事件不属于当前运行实例"},
        )

    existing = db.scalar(
        select(BotRuntimeEvent).where(
            BotRuntimeEvent.tenant_id == principal.tenant.id,
            BotRuntimeEvent.id == payload.event_id,
        )
    )
    if existing is not None:
        return RuntimeEventResponse(accepted=True, duplicate=True)

    db.add(
        BotRuntimeEvent(
            id=payload.event_id,
            tenant_id=principal.tenant.id,
            bot_instance_id=principal.bot.id,
            event_type=payload.event_type,
            schema_version=payload.schema_version,
            channel_account_id=payload.channel_account_id,
            source_account_id=payload.source_account_id,
            platform=payload.platform,
            correlation_id=payload.correlation_id,
            occurred_at=payload.occurred_at,
            payload_json=payload.payload,
        )
    )
    db.commit()
    return RuntimeEventResponse(accepted=True, duplicate=False)


@router.post("/chat/completions")
def runtime_chat_completion(
    payload: RuntimeChatCompletionRequest,
    request: Request,
    principal: RuntimeChatPrincipal,
    db: DatabaseSession,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    # The runtime cannot select another Persona or model. The server binds every
    # request to the Persona recorded on the authenticated BotInstance.
    # A compromised runtime must not override the Persona system prompt or
    # sampling configuration, even when it bypasses the local bridge.
    safe_messages = [
        message.model_dump(exclude_none=True)
        for message in payload.messages
        if message.role in {"user", "assistant"}
    ]
    if not safe_messages or not any(msg["role"] == "user" for msg in safe_messages):
        raise HTTPException(
            status_code=422,
            detail={"code": "runtime_user_message_required", "message": "需要用户消息"},
        )
    gateway_payload = ChatCompletionRequest.model_validate(
        {
            "model": principal.persona.slug,
            "messages": safe_messages,
            "stream": False,
        }
    )
    gateway_principal = GatewayPrincipal(
        api_key=None,
        user=principal.user,
        tenant=principal.tenant,
        identity_id=principal.bot.id,
        bot_instance_id=principal.bot.id,
    )
    return create_chat_completion(
        gateway_payload,
        request,
        gateway_principal,
        db,
        idempotency_key,
    )
