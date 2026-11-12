from dataclasses import dataclass
from typing import Annotated
from uuid import UUID

import jwt
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.security import decode_access_token
from app.db.session import get_db
from app.models.identity import Tenant, TenantMembership, User

_bearer = HTTPBearer(auto_error=False)


@dataclass(frozen=True)
class Principal:
    user: User
    tenant: Tenant
    membership: TenantMembership


DatabaseSession = Annotated[Session, Depends(get_db)]


def get_current_principal(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
    db: DatabaseSession,
) -> Principal:
    unauthorized = HTTPException(
        status_code=401,
        detail={"code": "unauthorized", "message": "请登录后再执行此操作"},
        headers={"WWW-Authenticate": "Bearer"},
    )
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise unauthorized

    try:
        claims = decode_access_token(credentials.credentials)
        user_id = UUID(claims["sub"])
        tenant_id = UUID(claims["tid"])
    except (jwt.InvalidTokenError, KeyError, ValueError, TypeError):
        raise unauthorized from None

    user = db.scalar(select(User).where(User.id == user_id, User.status == "active"))
    tenant = db.scalar(select(Tenant).where(Tenant.id == tenant_id, Tenant.status == "active"))
    membership = db.scalar(
        select(TenantMembership).where(
            TenantMembership.user_id == user_id,
            TenantMembership.tenant_id == tenant_id,
            TenantMembership.status == "active",
        )
    )
    if user is None or tenant is None or membership is None:
        raise unauthorized

    return Principal(user=user, tenant=tenant, membership=membership)


CurrentPrincipal = Annotated[Principal, Depends(get_current_principal)]
