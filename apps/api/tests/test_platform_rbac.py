from __future__ import annotations

from collections.abc import Generator
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from app.db.base import Base
from app.db.session import get_db
from app.main import app, asgi_app
from app.models.platform_rbac import (
    PlatformPermission,
    PlatformRole,
    PlatformRolePermission,
    PlatformUserRole,
)
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool


@pytest.fixture
def rbac_environment() -> Generator[tuple[TestClient, sessionmaker], None, None]:
    engine = create_engine(
        "sqlite+pysqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def _enable_sqlite_foreign_keys(connection, _record) -> None:
        cursor = connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(engine)
    factory = sessionmaker(
        bind=engine,
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
    )

    def override_get_db():
        with factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(asgi_app, raise_server_exceptions=False) as client:
        yield client, factory
    app.dependency_overrides.clear()
    Base.metadata.drop_all(engine)
    engine.dispose()


def _register(client: TestClient, email: str) -> dict:
    response = client.post(
        "/auth/register",
        json={
            "email": email,
            "password": "correct-horse-battery",
            "display_name": email.split("@")[0],
        },
    )
    assert response.status_code == 201
    return response.json()


def _headers(tokens: dict) -> dict[str, str]:
    return {"Authorization": f"Bearer {tokens['access_token']}"}


def _seed_role(
    db: Session,
    *,
    code: str,
    name: str,
    permissions: tuple[str, ...],
) -> PlatformRole:
    role = PlatformRole(
        id=uuid4(),
        code=code,
        name=name,
        description="RBAC integration test role",
        is_system=True,
    )
    db.add(role)
    for permission_code in permissions:
        permission = db.scalar(
            select(PlatformPermission).where(PlatformPermission.code == permission_code)
        )
        if permission is None:
            permission = PlatformPermission(
                id=uuid4(),
                code=permission_code,
                description=f"Test permission {permission_code}",
            )
            db.add(permission)
            db.flush()
        db.add(
            PlatformRolePermission(
                role_id=role.id,
                permission_id=permission.id,
            )
        )
    db.flush()
    return role


def _assign_role(
    db: Session,
    *,
    user_id: UUID,
    role: PlatformRole,
    expires_at: datetime | None = None,
) -> PlatformUserRole:
    assignment = PlatformUserRole(
        id=uuid4(),
        user_id=user_id,
        role_id=role.id,
        granted_by_user_id=None,
        expires_at=expires_at,
    )
    db.add(assignment)
    db.commit()
    return assignment


def test_platform_role_grant_revoke_and_audit_are_effective_on_next_request(
    rbac_environment: tuple[TestClient, sessionmaker],
) -> None:
    client, factory = rbac_environment
    actor = _register(client, "rbac-admin@example.com")
    target = _register(client, "rbac-target@example.com")

    with factory() as db:
        super_admin = _seed_role(
            db,
            code="super_admin",
            name="超级管理员",
            permissions=("platform.rbac.manage", "platform.audit.read"),
        )
        _seed_role(
            db,
            code="read_only",
            name="平台只读",
            permissions=("platform.models.read",),
        )
        _assign_role(db, user_id=UUID(actor["user"]["id"]), role=super_admin)

    actor_headers = _headers(actor)
    target_headers = _headers(target)

    before = client.get("/admin/platform/access", headers=target_headers)
    assert before.status_code == 200
    assert before.json()["permissions"] == []

    granted = client.post(
        f"/admin/platform/users/{target['user']['id']}/roles/read_only",
        headers=actor_headers,
        json={"reason": "授予模型目录只读权限"},
    )
    assert granted.status_code == 201
    assert granted.json()["granted"] is True

    after_grant = client.get("/admin/platform/access", headers=target_headers)
    assert after_grant.status_code == 200
    assert after_grant.json()["roles"] == ["read_only"]
    assert after_grant.json()["permissions"] == ["platform.models.read"]

    duplicate = client.post(
        f"/admin/platform/users/{target['user']['id']}/roles/read_only",
        headers=actor_headers,
        json={"reason": "重复授权测试"},
    )
    assert duplicate.status_code == 409
    assert duplicate.json()["detail"]["code"] == "platform_role_already_granted"

    revoked = client.request(
        "DELETE",
        f"/admin/platform/users/{target['user']['id']}/roles/read_only",
        headers=actor_headers,
        json={"reason": "只读任务已结束"},
    )
    assert revoked.status_code == 200
    assert revoked.json()["revoked"] is True

    after_revoke = client.get("/admin/platform/access", headers=target_headers)
    assert after_revoke.status_code == 200
    assert after_revoke.json()["permissions"] == []

    audit = client.get("/admin/platform/audit-logs", headers=actor_headers)
    assert audit.status_code == 200
    actions = [item["action"] for item in audit.json()]
    assert "platform.role.grant" in actions
    assert "platform.role.revoke" in actions


def test_platform_role_grant_rejects_expired_and_inactive_assignments(
    rbac_environment: tuple[TestClient, sessionmaker],
) -> None:
    client, factory = rbac_environment
    actor = _register(client, "rbac-expiry-admin@example.com")
    target = _register(client, "rbac-expiry-target@example.com")

    with factory() as db:
        super_admin = _seed_role(
            db,
            code="super_admin",
            name="超级管理员",
            permissions=("platform.rbac.manage",),
        )
        expiring = _seed_role(
            db,
            code="temporary_support",
            name="临时支持",
            permissions=("platform.users.read", "platform.tenants.read"),
        )
        _assign_role(db, user_id=UUID(actor["user"]["id"]), role=super_admin)
        _assign_role(
            db,
            user_id=UUID(target["user"]["id"]),
            role=expiring,
            expires_at=datetime.now(UTC) - timedelta(minutes=1),
        )

    access = client.get("/admin/platform/access", headers=_headers(target))
    assert access.status_code == 200
    assert access.json()["roles"] == []
    assert access.json()["permissions"] == []

    invalid_expiry = client.post(
        f"/admin/platform/users/{target['user']['id']}/roles/temporary_support",
        headers=_headers(actor),
        json={
            "reason": "无效的过期时间",
            "expires_at": (datetime.now(UTC) - timedelta(minutes=1)).isoformat(),
        },
    )
    assert invalid_expiry.status_code == 422
    assert invalid_expiry.json()["detail"]["code"] == "invalid_role_expiry"


def test_last_active_super_admin_cannot_revoke_own_final_assignment(
    rbac_environment: tuple[TestClient, sessionmaker],
) -> None:
    client, factory = rbac_environment
    actor = _register(client, "last-super-admin@example.com")

    with factory() as db:
        super_admin = _seed_role(
            db,
            code="super_admin",
            name="超级管理员",
            permissions=("platform.rbac.manage",),
        )
        _assign_role(db, user_id=UUID(actor["user"]["id"]), role=super_admin)

    response = client.request(
        "DELETE",
        f"/admin/platform/users/{actor['user']['id']}/roles/super_admin",
        headers=_headers(actor),
        json={"reason": "测试最后一个超级管理员保护"},
    )
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "last_super_admin"

    access = client.get("/admin/platform/access", headers=_headers(actor))
    assert access.status_code == 200
    assert "platform.rbac.manage" in access.json()["permissions"]


def test_platform_user_search_and_profile_require_users_read_permission(
    rbac_environment: tuple[TestClient, sessionmaker],
) -> None:
    client, factory = rbac_environment
    actor = _register(client, "platform-user-admin@example.com")
    target = _register(client, "customer-search@example.com")
    _register(client, "another-customer@example.com")

    with factory() as db:
        reader = _seed_role(
            db,
            code="support_reader",
            name="客户支持只读",
            permissions=("platform.users.read", "platform.tenants.read"),
        )
        _assign_role(db, user_id=UUID(actor["user"]["id"]), role=reader)

    headers = _headers(actor)
    searched = client.get(
        "/admin/platform/users",
        headers=headers,
        params={"q": "customer-search", "limit": 10, "offset": 0},
    )
    assert searched.status_code == 200
    payload = searched.json()
    assert payload["total"] == 1
    assert payload["items"][0]["id"] == target["user"]["id"]
    assert payload["items"][0]["email"] == "customer-search@example.com"
    assert payload["items"][0]["tenant_count"] == 1

    profile = client.get(
        f"/admin/platform/users/{target['user']['id']}",
        headers=headers,
    )
    assert profile.status_code == 200
    detail = profile.json()
    assert detail["email"] == "customer-search@example.com"
    assert len(detail["tenants"]) == 1
    assert detail["tenants"][0]["tenant_id"] == target["tenant"]["id"]
    assert detail["tenants"][0]["tenant_name"]

    missing = client.get(
        f"/admin/platform/users/{uuid4()}",
        headers=headers,
    )
    assert missing.status_code == 404
    assert missing.json()["detail"]["code"] == "user_not_found"


def test_platform_user_search_rejects_users_without_read_permission(
    rbac_environment: tuple[TestClient, sessionmaker],
) -> None:
    client, _factory = rbac_environment
    user = _register(client, "no-platform-user-read@example.com")

    response = client.get("/admin/platform/users", headers=_headers(user))
    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "platform_permission_required"


def test_platform_user_read_does_not_disclose_tenant_metadata_without_tenant_permission(
    rbac_environment: tuple[TestClient, sessionmaker],
) -> None:
    client, factory = rbac_environment
    actor = _register(client, "user-read-only@example.com")
    target = _register(client, "private-tenant-customer@example.com")

    with factory() as db:
        reader = _seed_role(
            db,
            code="user_directory_reader",
            name="用户目录只读",
            permissions=("platform.users.read",),
        )
        _assign_role(db, user_id=UUID(actor["user"]["id"]), role=reader)

    headers = _headers(actor)
    listing = client.get("/admin/platform/users", headers=headers)
    assert listing.status_code == 200
    target_row = next(
        item for item in listing.json()["items"]
        if item["id"] == target["user"]["id"]
    )
    assert target_row["tenant_count"] is None

    detail = client.get(
        f"/admin/platform/users/{target['user']['id']}",
        headers=headers,
    )
    assert detail.status_code == 200
    assert detail.json()["tenants"] == []


def test_platform_tenant_directory_and_lifecycle_are_permission_gated(
    rbac_environment: tuple[TestClient, sessionmaker],
) -> None:
    client, factory = rbac_environment
    actor = _register(client, "tenant-operator@example.com")
    customer = _register(client, "tenant-customer@example.com")

    with factory() as db:
        operator = _seed_role(
            db,
            code="tenant_operator",
            name="租户运营",
            permissions=("platform.tenants.read", "platform.tenants.manage", "platform.audit.read"),
        )
        _assign_role(db, user_id=UUID(actor["user"]["id"]), role=operator)

    headers = _headers(actor)
    listing = client.get("/admin/platform/tenants", headers=headers)
    assert listing.status_code == 200
    tenant_row = next(
        item for item in listing.json()["items"]
        if item["id"] == customer["tenant"]["id"]
    )
    assert tenant_row["name"] == customer["tenant"]["name"]
    assert tenant_row["member_count"] is None

    detail = client.get(
        f"/admin/platform/tenants/{customer['tenant']['id']}",
        headers=headers,
    )
    assert detail.status_code == 200
    assert detail.json()["members"] == []
    assert detail.json()["member_count"] is None

    disabled = client.patch(
        f"/admin/platform/tenants/{customer['tenant']['id']}/status",
        headers=headers,
        json={"status": "disabled", "reason": "客户申请暂停服务"},
    )
    assert disabled.status_code == 200
    assert disabled.json()["status"] == "disabled"

    enabled = client.patch(
        f"/admin/platform/tenants/{customer['tenant']['id']}/status",
        headers=headers,
        json={"status": "active", "reason": "客户恢复服务"},
    )
    assert enabled.status_code == 200
    assert enabled.json()["status"] == "active"

    audit = client.get("/admin/platform/audit-logs", headers=headers)
    assert audit.status_code == 200
    tenant_actions = [
        item for item in audit.json()
        if item["resource_type"] == "tenant"
        and item["resource_id"] == customer["tenant"]["id"]
    ]
    assert [item["action"] for item in tenant_actions] == [
        "platform.tenant.enable",
        "platform.tenant.disable",
    ]


def test_platform_tenant_cannot_disable_workspace_containing_active_super_admin(
    rbac_environment: tuple[TestClient, sessionmaker],
) -> None:
    client, factory = rbac_environment
    actor = _register(client, "tenant-lockout-operator@example.com")
    admin = _register(client, "tenant-lockout-superadmin@example.com")

    with factory() as db:
        operator = _seed_role(
            db,
            code="tenant_lockout_operator",
            name="租户管理员",
            permissions=("platform.tenants.read", "platform.tenants.manage"),
        )
        super_admin = _seed_role(
            db,
            code="super_admin",
            name="超级管理员",
            permissions=("platform.rbac.manage",),
        )
        _assign_role(db, user_id=UUID(actor["user"]["id"]), role=operator)
        _assign_role(db, user_id=UUID(admin["user"]["id"]), role=super_admin)

    response = client.patch(
        f"/admin/platform/tenants/{admin['tenant']['id']}/status",
        headers=_headers(actor),
        json={"status": "disabled", "reason": "测试管理员保护"},
    )
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "tenant_contains_super_admin"

    current = client.get(
        f"/admin/platform/tenants/{admin['tenant']['id']}",
        headers=_headers(actor),
    )
    assert current.status_code == 200
    assert current.json()["status"] == "active"
