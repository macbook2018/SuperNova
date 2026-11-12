from __future__ import annotations

import ipaddress
import json
import socket
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated, Any
from urllib.parse import urlparse
from uuid import uuid4

import httpx
import structlog
from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from fastapi.responses import JSONResponse, StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import DatabaseSession
from app.core.config import get_settings
from app.core.secret_encryption import decrypt_provider_api_key
from app.core.security import hash_secret
from app.models.catalog import ModelProvider, Persona
from app.models.identity import ApiKey, Tenant, TenantMembership, User
from app.models.usage import UsageRecord
from app.schemas.gateway import ChatCompletionRequest

router = APIRouter(prefix="/v1", tags=["OpenAI 兼容网关"])
logger = structlog.get_logger()


@dataclass(frozen=True)
class GatewayPrincipal:
    api_key: ApiKey
    user: User
    tenant: Tenant


def _unauthorized() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail={"code": "invalid_api_key", "message": "API Key 无效、已撤销或已过期"},
        headers={"WWW-Authenticate": "Bearer"},
    )


def get_gateway_principal(
    db: DatabaseSession,
    authorization: Annotated[str | None, Header()] = None,
) -> GatewayPrincipal:
    if not authorization:
        raise _unauthorized()
    scheme, _, secret = authorization.partition(" ")
    if scheme.lower() != "bearer" or not secret.startswith("sn_live_"):
        raise _unauthorized()

    api_key = db.scalar(select(ApiKey).where(ApiKey.key_hash == hash_secret(secret)))
    if api_key is None or api_key.status != "active":
        raise _unauthorized()

    now = datetime.now(UTC)
    expires_at = api_key.expires_at
    if expires_at is not None:
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=UTC)
        if expires_at <= now:
            raise _unauthorized()

    user = db.scalar(select(User).where(User.id == api_key.user_id, User.status == "active"))
    tenant = db.scalar(
        select(Tenant).where(Tenant.id == api_key.tenant_id, Tenant.status == "active")
    )
    membership = db.scalar(
        select(TenantMembership).where(
            TenantMembership.user_id == api_key.user_id,
            TenantMembership.tenant_id == api_key.tenant_id,
            TenantMembership.status == "active",
        )
    )
    if user is None or tenant is None or membership is None:
        raise _unauthorized()

    api_key.last_used_at = now
    db.commit()
    return GatewayPrincipal(api_key=api_key, user=user, tenant=tenant)


GatewayPrincipalDependency = Annotated[GatewayPrincipal, Depends(get_gateway_principal)]


def _request_id(request: Request) -> str:
    return str(getattr(request.state, "request_id", None) or uuid4())


def _validate_provider_target(base_url: str) -> None:
    settings = get_settings()
    parsed = urlparse(base_url)
    host = parsed.hostname
    if not host or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise HTTPException(
            status_code=403,
            detail={"code": "provider_url_not_allowed", "message": "模型供应商地址不符合安全要求"},
        )

    environment = settings.environment.lower()
    development = environment not in {"production", "prod"}
    local_hosts = {"localhost", "127.0.0.1", "::1"}
    local_target = host.lower() in local_hosts

    if parsed.scheme != "https" and not (development and local_target and parsed.scheme == "http"):
        raise HTTPException(
            status_code=403,
            detail={"code": "provider_url_not_allowed", "message": "模型供应商必须使用 HTTPS"},
        )
    if parsed.port not in (None, 443) and not (development and local_target):
        raise HTTPException(
            status_code=403,
            detail={
                "code": "provider_url_not_allowed",
                "message": "模型供应商仅允许使用标准 HTTPS 端口",
            },
        )
    if local_target:
        if not development:
            raise HTTPException(
                status_code=403,
                detail={
                    "code": "provider_url_not_allowed",
                    "message": "生产环境不允许访问本机模型地址",
                },
            )
        return

    try:
        literal_ip = ipaddress.ip_address(host)
        addresses = {literal_ip}
    except ValueError:
        try:
            resolved = socket.getaddrinfo(host, parsed.port or 443, type=socket.SOCK_STREAM)
        except OSError:
            raise HTTPException(
                status_code=502,
                detail={"code": "provider_dns_failed", "message": "无法解析模型供应商地址"},
            ) from None
        addresses = {ipaddress.ip_address(item[4][0]) for item in resolved}

    if not addresses or any(not address.is_global for address in addresses):
        raise HTTPException(
            status_code=403,
            detail={
                "code": "provider_url_not_allowed",
                "message": "模型供应商地址解析到非公网 IP，已阻止请求",
            },
        )


def _chat_completions_url(base_url: str) -> str:
    base = base_url.rstrip("/")
    parsed = urlparse(base)
    if parsed.path.rstrip("/").endswith("/v1"):
        return base + "/chat/completions"
    return base + "/v1/chat/completions"


def _resolve_persona(
    db: Session,
    principal: GatewayPrincipal,
    model_slug: str,
) -> tuple[Persona, ModelProvider, str, str]:
    persona = db.scalar(
        select(Persona).where(
            Persona.tenant_id == principal.tenant.id,
            Persona.slug == model_slug,
        )
    )
    if persona is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "model_not_found", "message": "指定的人格不存在或不属于当前用户"},
        )
    if not persona.enabled:
        raise HTTPException(
            status_code=403,
            detail={"code": "model_disabled", "message": "该人格已停用，无法调用"},
        )
    if persona.provider_id is None:
        raise HTTPException(
            status_code=409,
            detail={"code": "model_provider_not_configured", "message": "该人格尚未配置模型供应商"},
        )

    provider = db.scalar(
        select(ModelProvider).where(
            ModelProvider.id == persona.provider_id,
            ModelProvider.tenant_id == principal.tenant.id,
        )
    )
    if provider is None or not provider.enabled:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "model_provider_disabled",
                "message": "该人格绑定的模型供应商不存在或已停用",
            },
        )
    if not provider.api_key_ciphertext:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "provider_credentials_missing",
                "message": "请先为模型供应商配置 API Key",
            },
        )
    if provider.provider_type == "anthropic":
        raise HTTPException(
            status_code=501,
            detail={
                "code": "provider_not_supported",
                "message": "当前网关阶段暂不支持 Anthropic 原生协议，请使用 OpenAI 兼容供应商",
            },
        )

    try:
        provider_secret = decrypt_provider_api_key(provider.api_key_ciphertext)
    except RuntimeError:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "provider_credentials_unavailable",
                "message": "模型供应商凭据无法解密，请检查服务端加密配置",
            },
        ) from None

    _validate_provider_target(provider.base_url)
    return persona, provider, provider_secret, _chat_completions_url(provider.base_url)


def _extract_usage(value: Any) -> dict[str, int] | None:
    if not isinstance(value, dict):
        return None

    def valid_count(key: str) -> int | None:
        item = value.get(key)
        return item if isinstance(item, int) and not isinstance(item, bool) and item >= 0 else None

    prompt = valid_count("prompt_tokens")
    completion = valid_count("completion_tokens")
    total = valid_count("total_tokens")
    if total is None and prompt is not None and completion is not None:
        total = prompt + completion
    if prompt is None and completion is None and total is None:
        return None
    return {
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "total_tokens": total,
    }


def _record_usage(
    db: Session,
    *,
    request_id: str,
    principal: GatewayPrincipal,
    persona: Persona,
    provider: ModelProvider,
    usage: dict[str, int] | None,
    status_value: str,
    latency_ms: int,
    error_code: str | None = None,
) -> None:
    usage = usage or {}
    db.add(
        UsageRecord(
            id=uuid4(),
            request_id=request_id,
            tenant_id=principal.tenant.id,
            user_id=principal.user.id,
            api_key_id=principal.api_key.id,
            persona_id=persona.id,
            provider_id=provider.id,
            persona_slug=persona.slug,
            provider_type=provider.provider_type,
            model_name=persona.model_name,
            status=status_value,
            prompt_tokens=usage.get("prompt_tokens"),
            completion_tokens=usage.get("completion_tokens"),
            total_tokens=usage.get("total_tokens"),
            latency_ms=max(0, latency_ms),
            error_code=error_code,
        )
    )
    db.commit()


def _upstream_payload(payload: ChatCompletionRequest, persona: Persona) -> dict[str, Any]:
    messages = [{"role": "system", "content": persona.system_prompt}]
    messages.extend(message.model_dump(exclude_none=True) for message in payload.messages)
    max_tokens = min(payload.max_tokens or persona.max_tokens, persona.max_tokens)
    result: dict[str, Any] = {
        "model": persona.model_name,
        "messages": messages,
        "temperature": (
            payload.temperature if payload.temperature is not None else persona.temperature
        ),
        "top_p": payload.top_p if payload.top_p is not None else persona.top_p,
        "max_tokens": max_tokens,
        "stream": payload.stream,
    }
    if payload.stop is not None:
        result["stop"] = payload.stop
    if payload.presence_penalty is not None:
        result["presence_penalty"] = payload.presence_penalty
    if payload.frequency_penalty is not None:
        result["frequency_penalty"] = payload.frequency_penalty
    if payload.stream:
        result["stream_options"] = {"include_usage": True}
    return result


def _client_error(status_code: int, code: str, message: str) -> HTTPException:
    return HTTPException(
        status_code=status_code,
        detail={"code": code, "message": message},
    )


def _record_failed(
    db: Session,
    *,
    request_id: str,
    principal: GatewayPrincipal,
    persona: Persona,
    provider: ModelProvider,
    started: float,
    error_code: str,
) -> None:
    _record_usage(
        db,
        request_id=request_id,
        principal=principal,
        persona=persona,
        provider=provider,
        usage=None,
        status_value="failed",
        latency_ms=int((time.monotonic() - started) * 1000),
        error_code=error_code,
    )


@router.get("/models")
def list_models(
    principal: GatewayPrincipalDependency,
    db: DatabaseSession,
) -> dict[str, Any]:
    records = db.execute(
        select(Persona, ModelProvider)
        .join(
            ModelProvider,
            (ModelProvider.id == Persona.provider_id)
            & (ModelProvider.tenant_id == Persona.tenant_id),
        )
        .where(
            Persona.tenant_id == principal.tenant.id,
            Persona.enabled.is_(True),
            ModelProvider.enabled.is_(True),
            ModelProvider.api_key_ciphertext.is_not(None),
            ModelProvider.provider_type != "anthropic",
        )
        .order_by(Persona.created_at.desc())
    ).all()

    data = []
    for persona, _provider in records:
        created_at = persona.created_at
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=UTC)
        data.append(
            {
                "id": persona.slug,
                "object": "model",
                "created": int(created_at.timestamp()),
                "owned_by": "supernova",
            }
        )
    return {"object": "list", "data": data}


@router.post("/chat/completions")
def create_chat_completion(
    payload: ChatCompletionRequest,
    request: Request,
    principal: GatewayPrincipalDependency,
    db: DatabaseSession,
):
    request_id = _request_id(request)
    persona, provider, provider_secret, endpoint = _resolve_persona(
        db, principal, payload.model
    )
    upstream_payload = _upstream_payload(payload, persona)
    headers = {
        "Authorization": "Bearer " + provider_secret,
        "Content-Type": "application/json",
        "Accept": "text/event-stream" if payload.stream else "application/json",
        "X-Request-ID": request_id,
    }
    started = time.monotonic()
    timeout = httpx.Timeout(90.0, connect=10.0)

    if not payload.stream:
        try:
            with httpx.Client(
                timeout=timeout,
                follow_redirects=False,
                trust_env=False,
            ) as client:
                upstream = client.post(endpoint, headers=headers, json=upstream_payload)
        except httpx.TimeoutException:
            _record_failed(
                db,
                request_id=request_id,
                principal=principal,
                persona=persona,
                provider=provider,
                started=started,
                error_code="provider_timeout",
            )
            raise _client_error(504, "provider_timeout", "模型供应商响应超时") from None
        except httpx.RequestError:
            _record_failed(
                db,
                request_id=request_id,
                principal=principal,
                persona=persona,
                provider=provider,
                started=started,
                error_code="provider_connection_error",
            )
            raise _client_error(502, "provider_connection_error", "无法连接模型供应商") from None

        latency_ms = int((time.monotonic() - started) * 1000)
        if upstream.status_code >= 400:
            _record_usage(
                db,
                request_id=request_id,
                principal=principal,
                persona=persona,
                provider=provider,
                usage=None,
                status_value="failed",
                latency_ms=latency_ms,
                error_code="provider_http_error",
            )
            if upstream.status_code in {401, 403}:
                message = "模型供应商认证失败，请检查供应商 API Key"
            elif upstream.status_code == 429:
                message = "模型供应商暂时限流，请稍后重试"
            elif upstream.status_code == 400:
                message = "模型供应商拒绝请求，请检查模型名称或请求参数"
            else:
                message = "模型供应商暂时无法完成请求"
            raise _client_error(502, "upstream_error", message)

        try:
            result = upstream.json()
        except ValueError:
            _record_usage(
                db,
                request_id=request_id,
                principal=principal,
                persona=persona,
                provider=provider,
                usage=None,
                status_value="failed",
                latency_ms=latency_ms,
                error_code="invalid_provider_response",
            )
            raise _client_error(
                502, "invalid_provider_response", "模型供应商返回了无法识别的响应"
            ) from None

        if not isinstance(result, dict) or not isinstance(result.get("choices"), list):
            _record_usage(
                db,
                request_id=request_id,
                principal=principal,
                persona=persona,
                provider=provider,
                usage=None,
                status_value="failed",
                latency_ms=latency_ms,
                error_code="invalid_provider_response",
            )
            raise _client_error(
                502, "invalid_provider_response", "模型供应商返回了不兼容的对话响应"
            )

        usage = _extract_usage(result.get("usage"))
        result["id"] = result.get("id") or "chatcmpl-" + uuid4().hex
        result["object"] = "chat.completion"
        result["created"] = result.get("created") or int(time.time())
        result["model"] = persona.slug
        if usage is not None:
            result["usage"] = usage
        _record_usage(
            db,
            request_id=request_id,
            principal=principal,
            persona=persona,
            provider=provider,
            usage=usage,
            status_value="succeeded",
            latency_ms=latency_ms,
        )
        return JSONResponse(content=result)

    client = httpx.Client(
        timeout=timeout,
        follow_redirects=False,
        trust_env=False,
    )
    try:
        upstream_request = client.build_request(
            "POST",
            endpoint,
            headers=headers,
            json=upstream_payload,
        )
        upstream = client.send(upstream_request, stream=True)
    except httpx.TimeoutException:
        client.close()
        _record_failed(
            db,
            request_id=request_id,
            principal=principal,
            persona=persona,
            provider=provider,
            started=started,
            error_code="provider_timeout",
        )
        raise _client_error(504, "provider_timeout", "模型供应商响应超时") from None
    except httpx.RequestError:
        client.close()
        _record_failed(
            db,
            request_id=request_id,
            principal=principal,
            persona=persona,
            provider=provider,
            started=started,
            error_code="provider_connection_error",
        )
        raise _client_error(502, "provider_connection_error", "无法连接模型供应商") from None

    if upstream.status_code >= 400:
        upstream.close()
        client.close()
        _record_usage(
            db,
            request_id=request_id,
            principal=principal,
            persona=persona,
            provider=provider,
            usage=None,
            status_value="failed",
            latency_ms=int((time.monotonic() - started) * 1000),
            error_code="provider_http_error",
        )
        if upstream.status_code in {401, 403}:
            message = "模型供应商认证失败，请检查供应商 API Key"
        elif upstream.status_code == 429:
            message = "模型供应商暂时限流，请稍后重试"
        else:
            message = "模型供应商暂时无法完成请求"
        raise _client_error(502, "upstream_error", message)

    database_bind = db.get_bind()

    def stream_events():
        usage: dict[str, int] | None = None
        completed = False
        cancelled = False
        failure_code: str | None = None
        meter_failed = False
        try:
            for line in upstream.iter_lines():
                if not line:
                    continue
                if not line.startswith("data:"):
                    yield line + "\n"
                    continue
                raw = line[5:].strip()
                if raw == "[DONE]":
                    completed = True
                    continue
                try:
                    chunk = json.loads(raw)
                except ValueError:
                    yield line + "\n\n"
                    continue
                if not isinstance(chunk, dict):
                    yield line + "\n\n"
                    continue
                chunk_usage = _extract_usage(chunk.get("usage"))
                if chunk_usage is not None:
                    usage = chunk_usage
                chunk["model"] = persona.slug
                encoded_chunk = json.dumps(
                    chunk, ensure_ascii=False, separators=(",", ":")
                )
                yield "data: " + encoded_chunk + "\n\n"
            if not completed:
                failure_code = "provider_stream_incomplete"
        except GeneratorExit:
            cancelled = True
            raise
        except Exception as exc:
            failure_code = "provider_stream_error"
            logger.warning(
                "gateway_stream_failed",
                request_id=request_id,
                provider_type=provider.provider_type,
                error_type=type(exc).__name__,
            )
        finally:
            upstream.close()
            client.close()
            record_session = Session(bind=database_bind)
            try:
                _record_usage(
                    record_session,
                    request_id=request_id,
                    principal=principal,
                    persona=persona,
                    provider=provider,
                    usage=usage,
                    status_value=(
                        "cancelled"
                        if cancelled
                        else "failed"
                        if failure_code
                        else "succeeded"
                    ),
                    latency_ms=int((time.monotonic() - started) * 1000),
                    error_code=failure_code,
                )
            except Exception:
                meter_failed = True
                logger.exception("gateway_usage_record_failed", request_id=request_id)
            finally:
                record_session.close()

        if meter_failed:
            yield "event: error\ndata: " + json.dumps(
                {
                    "error": {
                        "message": "用量记录失败，请联系平台管理员",
                        "type": "server_error",
                        "code": "usage_record_failed",
                    }
                },
                ensure_ascii=False,
            ) + "\n\n"
        elif failure_code:
            yield "event: error\ndata: " + json.dumps(
                {
                    "error": {
                        "message": "模型供应商流式响应中断",
                        "type": "server_error",
                        "code": failure_code,
                    }
                },
                ensure_ascii=False,
            ) + "\n\n"
        elif completed:
            yield "data: [DONE]\n\n"

    return StreamingResponse(
        stream_events(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
            "X-Request-ID": request_id,
        },
    )
