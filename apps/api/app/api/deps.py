from dataclasses import dataclass
from datetime import UTC, datetime
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
from app.models.platform_rbac import (
    PlatformPermission,
    PlatformRole,
    PlatformRolePermission,
    PlatformUserRole,
)

_bearer = HTTPBearer(auto_error=False)


@dataclass(frozen=True)
class Principal:
    user: User
    tenant: Tenant
    membership: TenantMembership
    platform_roles: frozenset[str] = frozenset()
    platform_permissions: frozenset[str] = frozenset()


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

    now = datetime.now(UTC)
    platform_access = db.execute(
        select(PlatformRole.code, PlatformPermission.code)
        .select_from(PlatformUserRole)
        .join(PlatformRole, PlatformRole.id == PlatformUserRole.role_id)
        .join(PlatformRolePermission, PlatformRolePermission.role_id == PlatformRole.id)
        .join(PlatformPermission, PlatformPermission.id == PlatformRolePermission.permission_id)
        .where(
            PlatformUserRole.user_id == user_id,
            PlatformUserRole.revoked_at.is_(None),
            (PlatformUserRole.expires_at.is_(None) | (PlatformUserRole.expires_at > now)),
        )
    ).all()
    return Principal(
        user=user,
        tenant=tenant,
        membership=membership,
        platform_roles=frozenset(role_code for role_code, _ in platform_access),
        platform_permissions=frozenset(permission_code for _, permission_code in platform_access),
    )


CurrentPrincipal = Annotated[Principal, Depends(get_current_principal)]
