from __future__ import annotations

from collections.abc import Generator
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from app.db.base import Base
from app.db.session import get_db
from app.main import app, asgi_app
from app.models.chat_import import ChatImportJob, ChatSource
from app.models.platform_rbac import (
    PlatformPermission,
    PlatformRole,
    PlatformRolePermission,
    PlatformUserRole,
)
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool


@pytest.fixture
def operations_environment() -> Generator[tuple[TestClient, sessionmaker], None, None]:
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


def _grant_operations_read(db, user_id: UUID) -> None:
    role = PlatformRole(
        id=uuid4(),
        code="operations_reader",
        name="运行监控只读",
        description="Operations test role",
        is_system=True,
    )
    permission = PlatformPermission(
        id=uuid4(),
        code="platform.operations.read",
        description="Read platform operations",
    )
    db.add_all([role, permission])
    db.flush()
    db.add_all([
        PlatformRolePermission(role_id=role.id, permission_id=permission.id),
        PlatformUserRole(
            id=uuid4(),
            user_id=user_id,
            role_id=role.id,
            granted_by_user_id=None,
        ),
    ])
    db.commit()


def test_platform_operations_reports_dependencies_and_stale_worker_leases(
    operations_environment: tuple[TestClient, sessionmaker],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.api import platform_operations

    async def _ok(_value: str) -> str:
        return "ok"

    monkeypatch.setattr(platform_operations, "_check_database", _ok)
    monkeypatch.setattr(platform_operations, "_check_redis", _ok)

    client, factory = operations_environment
    admin = _register(client, "operations-admin@example.com")
    owner = _register(client, "operations-owner@example.com")
    now = datetime.now(UTC)

    with factory() as db:
        _grant_operations_read(db, UUID(admin["user"]["id"]))
        source = ChatSource(
            id=uuid4(),
            tenant_id=UUID(owner["tenant"]["id"]),
            platform="wechat",
            name="测试来源",
            status="active",
        )
        db.add(source)
        db.flush()
        db.add_all([
            ChatImportJob(
                id=uuid4(),
                tenant_id=source.tenant_id,
                source_id=source.id,
                status="queued",
                progress=0,
            ),
            ChatImportJob(
                id=uuid4(),
                tenant_id=source.tenant_id,
                source_id=source.id,
                status="processing",
                progress=25,
                lease_expires_at=now - timedelta(minutes=5),
            ),
        ])
        db.commit()

    response = client.get(
        "/admin/platform/operations/overview",
        headers={"Authorization": f"Bearer {admin['access_token']}"},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["overall"] == "attention"
    assert payload["dependencies"] == {"postgres": "ok", "redis": "ok"}
    assert payload["queues"]["chat_import"]["status_counts"] == {
        "processing": 1,
        "queued": 1,
    }
    assert payload["queues"]["chat_import"]["stale_leases"] == 1
    assert payload["queues"]["knowledge_ingestion"]["total"] == 0


def test_platform_operations_requires_operations_read_permission(
    operations_environment: tuple[TestClient, sessionmaker],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, _factory = operations_environment
    user = _register(client, "operations-forbidden@example.com")
    response = client.get(
        "/admin/platform/operations/overview",
        headers={"Authorization": f"Bearer {user['access_token']}"},
    )
    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "platform_permission_required"
