from datetime import UTC, datetime
from uuid import UUID, uuid4

from fastapi import APIRouter, Header, HTTPException, Query, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, select

from app.api.deps import CurrentPrincipal, DatabaseSession, Principal
from app.models.identity import User
from app.models.platform_rbac import (
    PlatformAdminAuditLog,
    PlatformRole,
    PlatformUserRole,
)

router = APIRouter(tags=["平台权限"])


class PlatformRoleChangeRequest(BaseModel):
    reason: str = Field(min_length=3, max_length=500)
    expires_at: datetime | None = None

    @field_validator("expires_at")
    @classmethod
    def require_timezone_for_expiry(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.tzinfo is None:
            raise ValueError("角色到期时间必须包含时区")
        return value


def require_platform_permission(principal: Principal, permission: str) -> None:
    if permission not in principal.platform_permissions:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "platform_permission_required",
                "message": "当前平台角色没有执行此操作的权限",
                "permission": permission,
            },
        )


def require_platform_admin(principal: Principal) -> None:
    require_platform_permission(principal, "platform.admin")


def record_platform_audit(
    db,
    *,
    actor_user_id: UUID,
    target_user_id: UUID | None,
    action: str,
    resource_type: str,
    resource_id: str,
    reason: str | None,
    request_id: str | None,
    details: dict | None = None,
) -> None:
    db.add(
        PlatformAdminAuditLog(
            id=uuid4(),
            actor_user_id=actor_user_id,
            target_user_id=target_user_id,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            result="success",
            reason=reason,
            request_id=request_id,
            details=details or {},
        )
    )


@router.get("/admin/platform/access")
def get_platform_access(principal: CurrentPrincipal) -> dict:
    return {
        "is_platform_admin": bool(principal.platform_permissions),
        "roles": sorted(principal.platform_roles),
        "permissions": sorted(principal.platform_permissions),
    }


@router.get("/admin/platform/roles")
def list_platform_roles(
    principal: CurrentPrincipal,
    db: DatabaseSession,
) -> list[dict]:
    require_platform_permission(principal, "platform.rbac.manage")
    roles = db.scalars(select(PlatformRole).order_by(PlatformRole.code)).all()
    return [
        {
            "code": role.code,
            "name": role.name,
            "description": role.description,
            "is_system": role.is_system,
        }
        for role in roles
    ]


@router.post(
    "/admin/platform/users/{user_id}/roles/{role_code}",
    status_code=status.HTTP_201_CREATED,
)
def grant_platform_role(
    user_id: UUID,
    role_code: str,
    payload: PlatformRoleChangeRequest,
    principal: CurrentPrincipal,
    db: DatabaseSession,
    request_id: str | None = Header(default=None, alias="X-Request-ID"),
) -> dict:
    require_platform_permission(principal, "platform.rbac.manage")
    target = db.get(User, user_id)
    role = db.scalar(
        select(PlatformRole).where(PlatformRole.code == role_code)
    )
    if target is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "user_not_found", "message": "用户不存在"},
        )
    if target.status != "active":
        raise HTTPException(
            status_code=409,
            detail={"code": "user_inactive", "message": "不能向已禁用账户授予平台角色"},
        )
    if role is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "platform_role_not_found", "message": "平台角色不存在"},
        )
    now = datetime.now(UTC)
    if payload.expires_at is not None and payload.expires_at <= now:
        raise HTTPException(
            status_code=422,
            detail={"code": "invalid_role_expiry", "message": "角色到期时间必须晚于当前时间"},
        )
    existing = db.scalar(
        select(PlatformUserRole).where(
            PlatformUserRole.user_id == user_id,
            PlatformUserRole.role_id == role.id,
            PlatformUserRole.revoked_at.is_(None),
            (PlatformUserRole.expires_at.is_(None) | (PlatformUserRole.expires_at > now)),
        )
    )
    if existing is not None:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "platform_role_already_granted",
                "message": "用户已拥有该平台角色",
            },
        )
    assignment = PlatformUserRole(
        id=uuid4(),
        user_id=user_id,
        role_id=role.id,
        granted_by_user_id=principal.user.id,
        expires_at=payload.expires_at,
    )
    db.add(assignment)
    record_platform_audit(
        db,
        actor_user_id=principal.user.id,
        target_user_id=user_id,
        action="platform.role.grant",
        resource_type="platform_role",
        resource_id=role.code,
        reason=payload.reason,
        request_id=request_id,
        details={
            "role_code": role.code,
            "expires_at": payload.expires_at.isoformat() if payload.expires_at else None,
        },
    )
    db.commit()
    return {
        "user_id": str(user_id),
        "role": role.code,
        "expires_at": payload.expires_at,
        "granted": True,
    }


@router.delete("/admin/platform/users/{user_id}/roles/{role_code}")
def revoke_platform_role(
    user_id: UUID,
    role_code: str,
    payload: PlatformRoleChangeRequest,
    principal: CurrentPrincipal,
    db: DatabaseSession,
    request_id: str | None = Header(default=None, alias="X-Request-ID"),
) -> dict:
    require_platform_permission(principal, "platform.rbac.manage")
    role = db.scalar(
        select(PlatformRole).where(PlatformRole.code == role_code)
    )
    if role is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "platform_role_not_found", "message": "平台角色不存在"},
        )
    now = datetime.now(UTC)
    assignment = db.scalar(
        select(PlatformUserRole).where(
            PlatformUserRole.user_id == user_id,
            PlatformUserRole.role_id == role.id,
            PlatformUserRole.revoked_at.is_(None),
            (PlatformUserRole.expires_at.is_(None) | (PlatformUserRole.expires_at > now)),
        )
    )
    if assignment is None:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "platform_role_assignment_not_found",
                "message": "有效的平台角色授权不存在",
            },
        )
    if role_code == "super_admin":
        active_admins = db.scalar(
            select(func.count(PlatformUserRole.id))
            .join(PlatformRole, PlatformRole.id == PlatformUserRole.role_id)
            .join(User, User.id == PlatformUserRole.user_id)
            .where(
                PlatformRole.code == "super_admin",
                PlatformUserRole.revoked_at.is_(None),
                (PlatformUserRole.expires_at.is_(None) | (PlatformUserRole.expires_at > now)),
                User.status == "active",
            )
        ) or 0
        if active_admins <= 1:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "last_super_admin",
                    "message": "不能撤销最后一个有效超级管理员",
                },
            )
    assignment.revoked_at = now
    record_platform_audit(
        db,
        actor_user_id=principal.user.id,
        target_user_id=user_id,
        action="platform.role.revoke",
        resource_type="platform_role",
        resource_id=role.code,
        reason=payload.reason,
        request_id=request_id,
        details={"role_code": role.code},
    )
    db.commit()
    return {"user_id": str(user_id), "role": role.code, "revoked": True}


@router.get("/admin/platform/audit-logs")
def list_platform_audit_logs(
    principal: CurrentPrincipal,
    db: DatabaseSession,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> list[dict]:
    require_platform_permission(principal, "platform.audit.read")
    rows = db.scalars(
        select(PlatformAdminAuditLog)
        .order_by(PlatformAdminAuditLog.created_at.desc())
        .offset(offset)
        .limit(limit)
    ).all()
    return [
        {
            "id": str(item.id),
            "actor_user_id": str(item.actor_user_id) if item.actor_user_id else None,
            "target_user_id": str(item.target_user_id) if item.target_user_id else None,
            "action": item.action,
            "resource_type": item.resource_type,
            "resource_id": item.resource_id,
            "result": item.result,
            "reason": item.reason,
            "request_id": item.request_id,
            "details": item.details,
            "created_at": item.created_at.isoformat(),
        }
        for item in rows
    ]
