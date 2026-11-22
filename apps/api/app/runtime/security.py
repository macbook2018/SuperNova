from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import jwt
from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.security import hash_secret
from app.models.bot_runtime import BotInstance, BotRuntimeCredential
from app.models.catalog import Persona
from app.models.identity import Tenant, TenantMembership, User

RUNTIME_ISSUER = "supernova-runtime"
RUNTIME_AUDIENCE = "supernova-runtime"
RUNTIME_SCOPES = {"chat.completions", "runtime.heartbeat", "runtime.events"}


@dataclass(frozen=True)
class RuntimePrincipal:
    credential: BotRuntimeCredential
    bot: BotInstance
    persona: Persona
    tenant: Tenant
    user: User


def _unauthorized() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail={"code": "runtime_credential_invalid", "message": "运行时凭据无效或已过期"},
        headers={"WWW-Authenticate": "Bearer"},
    )


def _runtime_secret() -> str:
    secret = get_settings().runtime_jwt_secret
    if not secret or len(secret) < 32:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "runtime_auth_not_configured",
                "message": "运行时认证尚未配置独立签名密钥",
            },
        )
    return secret


def issue_runtime_token(
    db: Session,
    bot: BotInstance,
    scope: str,
    ttl_seconds: int = 300,
) -> str:
    """Issue one short-lived, bot/persona/config-bound runtime token."""
    if scope not in RUNTIME_SCOPES:
        raise ValueError("Unsupported runtime scope")
    if not 1 <= ttl_seconds <= 300:
        raise ValueError("Runtime credentials must expire within five minutes")

    now = datetime.now(UTC)
    expires_at = now + timedelta(seconds=ttl_seconds)
    jti = uuid4()
    claims = {
        "iss": RUNTIME_ISSUER,
        "aud": RUNTIME_AUDIENCE,
        "sub": str(bot.id),
        "tenant_id": str(bot.tenant_id),
        "persona_id": str(bot.persona_id),
        "config_version": bot.config_version,
        "scope": scope,
        "iat": int(now.timestamp()),
        "exp": int(expires_at.timestamp()),
        "jti": str(jti),
    }
    token = jwt.encode(claims, _runtime_secret(), algorithm="HS256")
    db.add(
        BotRuntimeCredential(
            id=uuid4(),
            tenant_id=bot.tenant_id,
            bot_instance_id=bot.id,
            persona_id=bot.persona_id,
            jti=jti,
            config_version=bot.config_version,
            scope=scope,
            token_hash=hash_secret(token),
            expires_at=expires_at,
        )
    )
    db.flush()
    return token


def authenticate_runtime_token(
    db: Session,
    token: str,
    required_scope: str,
) -> RuntimePrincipal:
    try:
        claims = jwt.decode(
            token,
            _runtime_secret(),
            algorithms=["HS256"],
            issuer=RUNTIME_ISSUER,
            audience=RUNTIME_AUDIENCE,
        )
        bot_id = UUID(claims["sub"])
        tenant_id = UUID(claims["tenant_id"])
        persona_id = UUID(claims["persona_id"])
        jti = UUID(claims["jti"])
        config_version = int(claims["config_version"])
        scope = str(claims["scope"])
    except (jwt.InvalidTokenError, KeyError, ValueError, TypeError):
        raise _unauthorized() from None

    if scope != required_scope or scope not in RUNTIME_SCOPES:
        raise _unauthorized()

    now = datetime.now(UTC)
    credential = db.scalar(
        select(BotRuntimeCredential).where(
            BotRuntimeCredential.jti == jti,
            BotRuntimeCredential.tenant_id == tenant_id,
            BotRuntimeCredential.bot_instance_id == bot_id,
            BotRuntimeCredential.persona_id == persona_id,
            BotRuntimeCredential.scope == scope,
            BotRuntimeCredential.config_version == config_version,
        )
    )
    if credential is None or credential.revoked_at is not None:
        raise _unauthorized()
    expires_at = credential.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    if expires_at <= now or credential.token_hash != hash_secret(token):
        raise _unauthorized()

    bot = db.scalar(
        select(BotInstance).where(
            BotInstance.id == bot_id,
            BotInstance.tenant_id == tenant_id,
            BotInstance.persona_id == persona_id,
            BotInstance.config_version == config_version,
        )
    )
    tenant = db.get(Tenant, tenant_id)
    persona = db.scalar(
        select(Persona).where(
            Persona.id == persona_id,
            Persona.tenant_id == tenant_id,
        )
    )
    user = db.get(User, bot.created_by_user_id) if bot is not None else None
    membership = (
        db.scalar(
            select(TenantMembership).where(
                TenantMembership.tenant_id == tenant_id,
                TenantMembership.user_id == bot.created_by_user_id,
                TenantMembership.status == "active",
            )
        )
        if bot is not None
        else None
    )
    if (
        bot is None
        or tenant is None
        or tenant.status != "active"
        or persona is None
        or user is None
        or user.status != "active"
        or membership is None
    ):
        raise _unauthorized()

    if scope == "chat.completions" and (
        not persona.enabled or bot.desired_state != "running" or bot.actual_state != "running"
    ):
        raise HTTPException(
            status_code=403,
            detail={
                "code": "runtime_not_ready",
                "message": "机器人未处于可对话状态，或绑定的人格已停用",
            },
        )

    credential.last_used_at = now
    db.commit()
    return RuntimePrincipal(
        credential=credential,
        bot=bot,
        persona=persona,
        tenant=tenant,
        user=user,
    )
