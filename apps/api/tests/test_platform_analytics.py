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
from app.models.usage import UsageRecord
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool


@pytest.fixture
def analytics_environment() -> Generator[tuple[TestClient, sessionmaker], None, None]:
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


def _grant_analytics(db, user_id: UUID) -> None:
    role = PlatformRole(
        id=uuid4(),
        code="analytics_reader",
        name="运营统计只读",
        description="Analytics test role",
        is_system=True,
    )
    permission = PlatformPermission(
        id=uuid4(),
        code="platform.analytics.read",
        description="Read platform analytics",
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


def test_platform_analytics_aggregates_usage_without_exposing_customer_identity(
    analytics_environment: tuple[TestClient, sessionmaker],
) -> None:
    client, factory = analytics_environment
    admin = _register(client, "analytics-admin@example.com")
    customer = _register(client, "analytics-customer@example.com")
    now = datetime.now(UTC)

    with factory() as db:
        _grant_analytics(db, UUID(admin["user"]["id"]))
        db.add_all([
            UsageRecord(
                id=uuid4(),
                request_id=str(uuid4()),
                tenant_id=UUID(customer["tenant"]["id"]),
                user_id=UUID(customer["user"]["id"]),
                model_source="platform",
                persona_slug="support",
                provider_type="deepseek",
                model_name="deepseek-chat",
                status="succeeded",
                prompt_tokens=100,
                completion_tokens=50,
                total_tokens=150,
                latency_ms=400,
                created_at=now - timedelta(days=1),
            ),
            UsageRecord(
                id=uuid4(),
                request_id=str(uuid4()),
                tenant_id=UUID(customer["tenant"]["id"]),
                user_id=UUID(customer["user"]["id"]),
                model_source="byok",
                persona_slug="support",
                provider_type="openai",
                model_name="gpt-4.1-mini",
                status="failed",
                prompt_tokens=30,
                completion_tokens=0,
                total_tokens=30,
                latency_ms=900,
                error_code="upstream_error",
                created_at=now - timedelta(days=1),
            ),
            UsageRecord(
                id=uuid4(),
                request_id=str(uuid4()),
                tenant_id=UUID(customer["tenant"]["id"]),
                user_id=UUID(customer["user"]["id"]),
                model_source="byok",
                persona_slug="support",
                provider_type="openai",
                model_name="old-model",
                status="succeeded",
                prompt_tokens=10,
                completion_tokens=5,
                total_tokens=15,
                latency_ms=100,
                created_at=now - timedelta(days=45),
            ),
        ])
        db.commit()

    response = client.get(
        "/admin/platform/analytics/overview",
        headers={"Authorization": f"Bearer {admin['access_token']}"},
        params={"days": 30},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["period"]["days"] == 30
    assert payload["accounts"]["users"] == 2
    assert payload["accounts"]["tenants"] == 2
    assert payload["usage"]["requests"] == 2
    assert payload["usage"]["successful_requests"] == 1
    assert payload["usage"]["failed_requests"] == 1
    assert payload["usage"]["total_tokens"] == 180
    assert {item["model_source"] for item in payload["by_model_source"]} == {
        "platform",
        "byok",
    }
    assert all("email" not in item for item in payload["top_models"])


def test_platform_analytics_requires_analytics_permission(
    analytics_environment: tuple[TestClient, sessionmaker],
) -> None:
    client, _factory = analytics_environment
    user = _register(client, "analytics-forbidden@example.com")
    response = client.get(
        "/admin/platform/analytics/overview",
        headers={"Authorization": f"Bearer {user['access_token']}"},
    )
    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "platform_permission_required"
