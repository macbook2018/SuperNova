from collections.abc import Generator
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4
from types import SimpleNamespace

import pytest
from app.billing import credentials
from app.billing.money import currency_minor_unit_exponent
from app.db.base import Base
from app.db.session import get_db
from app.models.platform_rbac import PlatformPermission, PlatformRole, PlatformRolePermission, PlatformUserRole
from app.main import app
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> Generator[TestClient, None, None]:
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
    testing_session = sessionmaker(
        bind=engine,
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
    )

    def override_get_db():
        with testing_session() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    with testing_session() as session:
        admin_role = PlatformRole(
            id=uuid4(), code="super_admin", name="超级管理员",
            description="测试平台超级管理员", is_system=True,
        )
        finance_role = PlatformRole(
            id=uuid4(), code="finance", name="财务管理员",
            description="测试财务管理员", is_system=True,
        )
        admin_permissions = [
            PlatformPermission(id=uuid4(), code=code, description=code)
            for code in (
                "platform.admin",
                "platform.rbac.manage",
                "platform.models.read",
                "platform.models.manage",
                "platform.billing.read",
                "platform.billing.manage",
            )
        ]
        session.add_all([admin_role, finance_role, *admin_permissions])
        session.flush()
        session.add_all([
            PlatformRolePermission(role_id=admin_role.id, permission_id=item.id)
            for item in admin_permissions
        ])
        finance_permission = next(item for item in admin_permissions if item.code == "platform.billing.read")
        session.add(
            PlatformRolePermission(role_id=finance_role.id, permission_id=finance_permission.id)
        )
        session.commit()
    monkeypatch.setattr(
        credentials,
        "get_settings",
        lambda: SimpleNamespace(
            platform_credentials_json='{"deepseek-primary":"sk-platform-test-secret"}'
        ),
    )
    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield test_client
    app.dependency_overrides.clear()
    Base.metadata.drop_all(engine)
    engine.dispose()


def register(client: TestClient, email: str = "admin@example.com") -> dict:
    response = client.post(
        "/auth/register",
        json={
            "email": email,
            "password": "correct-horse-battery",
            "display_name": "Workspace Owner",
        },
    )
    assert response.status_code == 201, response.text
    tokens = response.json()
    if email == "admin@example.com":
        generator = app.dependency_overrides[get_db]()
        session = next(generator)
        try:
            role = session.query(PlatformRole).filter_by(code="super_admin").one()
            session.add(
                PlatformUserRole(
                    id=uuid4(),
                    user_id=UUID(tokens["user"]["id"]),
                    role_id=role.id,
                )
            )
            session.commit()
        finally:
            try:
                next(generator)
            except StopIteration:
                pass
    return tokens


def headers(tokens: dict) -> dict[str, str]:
    return {"Authorization": f"Bearer {tokens['access_token']}"}


def create_plan(client: TestClient, admin: dict) -> dict:
    response = client.post(
        "/admin/billing/plans",
        headers=headers(admin),
        json={
            "code": "starter",
            "revision": 1,
            "name": "入门版",
            "billing_interval": "month",
            "currency": "CNY",
            "subscription_price_minor": 9900,
            "status": "active",
            "entitlements": [
                {
                    "resource_code": "api_requests",
                    "unit": "requests",
                    "included_units": 1000,
                    "hard_limit_units": 1000,
                    "overage_rate_minor_per_unit": "0",
                },
                {
                    "resource_code": "platform_model_requests",
                    "unit": "requests",
                    "included_units": 500,
                    "hard_limit_units": 500,
                    "overage_rate_minor_per_unit": "0",
                },
                {
                    "resource_code": "platform_input_tokens",
                    "unit": "tokens",
                    "included_units": 1000000,
                    "hard_limit_units": 1000000,
                    "overage_rate_minor_per_unit": "0",
                },
                {
                    "resource_code": "platform_output_tokens",
                    "unit": "tokens",
                    "included_units": 200000,
                    "hard_limit_units": 200000,
                    "overage_rate_minor_per_unit": "0",
                },
            ],
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_admin_can_create_versioned_plan_and_assign_subscription(client: TestClient) -> None:
    admin = register(client)
    customer = register(client, "customer@example.com")
    plan = create_plan(client, admin)

    assigned = client.post(
        f"/admin/billing/tenants/{customer['tenant']['id']}/subscription",
        headers=headers(admin),
        json={
            "plan_id": plan["id"],
            "current_period_start": (datetime.now(UTC) - timedelta(minutes=1)).isoformat(),
            "current_period_end": (datetime.now(UTC) + timedelta(days=30)).isoformat(),
            "status": "active",
        },
    )
    assert assigned.status_code == 201, assigned.text
    assert assigned.json()["plan_code"] == "starter"

    listed_plans = client.get("/billing/plans", headers=headers(customer))
    assert listed_plans.status_code == 200
    assert listed_plans.json()[0]["code"] == "starter"

    subscription = client.get("/billing/subscription", headers=headers(customer))
    assert subscription.status_code == 200
    assert subscription.json()["plan_name"] == "入门版"

    entitlements = client.get("/billing/entitlements", headers=headers(customer))
    assert entitlements.status_code == 200
    body = entitlements.json()
    assert body["legacy_unmetered"] is False
    assert body["subscription"]["plan_code"] == "starter"
    by_code = {item["resource_code"]: item for item in body["resources"]}
    assert by_code["api_requests"]["included_units"] == 1000
    assert by_code["api_requests"]["remaining_units"] == 1000


def test_non_admin_cannot_manage_plans_or_platform_models(client: TestClient) -> None:
    customer = register(client, "customer@example.com")
    denied_plan = client.post(
        "/admin/billing/plans",
        headers=headers(customer),
        json={
            "code": "forbidden",
            "revision": 1,
            "name": "不应创建",
            "billing_interval": "month",
            "currency": "CNY",
            "subscription_price_minor": 0,
            "status": "active",
            "entitlements": [
                {
                    "resource_code": "api_requests",
                    "unit": "requests",
                    "included_units": 1,
                    "overage_rate_minor_per_unit": "0",
                }
            ],
        },
    )
    assert denied_plan.status_code == 403
    assert denied_plan.json()["detail"]["code"] == "platform_admin_required"

    denied_model = client.get("/admin/platform/models", headers=headers(customer))
    assert denied_model.status_code == 403


def test_platform_admin_can_publish_model_and_immutable_price_version(
    client: TestClient,
) -> None:
    admin = register(client)
    model_response = client.post(
        "/admin/platform/models",
        headers=headers(admin),
        json={
            "code": "deepseek-main",
            "display_name": "DeepSeek 官方模型",
            "provider_type": "deepseek",
            "upstream_model_name": "deepseek-chat",
            "base_url": "https://api.deepseek.com",
            "credential_ref": "deepseek-primary",
            "enabled": True,
        },
    )
    assert model_response.status_code == 201, model_response.text
    model = model_response.json()
    assert model["credential_ref"] == "deepseek-primary"

    start = (datetime.now(UTC) - timedelta(minutes=1)).isoformat()
    price_response = client.post(
        f"/admin/platform/models/{model['id']}/prices",
        headers=headers(admin),
        json={
            "currency": "CNY",
            "input_per_million_minor": "200",
            "output_per_million_minor": "2000",
            "effective_from": start,
        },
    )
    assert price_response.status_code == 201, price_response.text
    assert price_response.json()["revision"] == 1

    rollover_start = datetime.now(UTC)
    rollover = client.post(
        f"/admin/platform/models/{model['id']}/prices",
        headers=headers(admin),
        json={
            "currency": "CNY",
            "input_per_million_minor": "250",
            "output_per_million_minor": "2500",
            "effective_from": rollover_start.isoformat(),
        },
    )
    assert rollover.status_code == 201, rollover.text
    assert rollover.json()["revision"] == 2

    prices = client.get(
        f"/admin/platform/models/{model['id']}/prices",
        headers=headers(admin),
    )
    assert prices.status_code == 200
    assert len(prices.json()) == 2
    by_revision = {item["revision"]: item for item in prices.json()}
    assert by_revision[1]["effective_until"] is not None
    assert by_revision[2]["effective_until"] is None

    customer = register(client, "customer@example.com")
    public = client.get("/platform/models", headers=headers(customer))
    assert public.status_code == 200
    assert len(public.json()) == 1
    assert public.json()[0]["id"] == model["id"]
    assert "credential_ref" not in public.text
    assert "deepseek-primary" not in public.text

    overlapping = client.post(
        f"/admin/platform/models/{model['id']}/prices",
        headers=headers(admin),
        json={
            "currency": "CNY",
            "input_per_million_minor": "250",
            "output_per_million_minor": "2500",
            "effective_from": (rollover_start - timedelta(days=2)).isoformat(),
        },
    )
    assert overlapping.status_code == 409
    assert overlapping.json()["detail"]["code"] == "platform_model_price_period_overlap"


def test_plan_revisions_are_immutable_and_only_one_current_subscription(
    client: TestClient,
) -> None:
    admin = register(client)
    customer = register(client, "customer@example.com")
    plan = create_plan(client, admin)

    duplicate = client.post(
        "/admin/billing/plans",
        headers=headers(admin),
        json={
            "code": "starter",
            "revision": 1,
            "name": "重复版本",
            "billing_interval": "month",
            "currency": "CNY",
            "subscription_price_minor": 100,
            "status": "active",
            "entitlements": [
                {
                    "resource_code": "api_requests",
                    "unit": "requests",
                    "included_units": 1,
                    "overage_rate_minor_per_unit": "0",
                }
            ],
        },
    )
    assert duplicate.status_code == 409

    payload = {
        "plan_id": plan["id"],
        "current_period_start": (datetime.now(UTC) - timedelta(minutes=1)).isoformat(),
        "current_period_end": (datetime.now(UTC) + timedelta(days=30)).isoformat(),
        "status": "active",
    }
    first = client.post(
        f"/admin/billing/tenants/{customer['tenant']['id']}/subscription",
        headers=headers(admin),
        json=payload,
    )
    assert first.status_code == 201
    second = client.post(
        f"/admin/billing/tenants/{customer['tenant']['id']}/subscription",
        headers=headers(admin),
        json=payload,
    )
    assert second.status_code == 409


def test_platform_admin_can_run_shadow_reconciliation_and_non_admin_is_denied(
    client: TestClient,
) -> None:
    admin = register(client)
    customer = register(client, "customer@example.com")
    start = datetime.now(UTC) - timedelta(days=1)
    end = datetime.now(UTC) + timedelta(minutes=1)

    denied = client.post(
        "/admin/billing/reconciliation/run",
        headers=headers(customer),
        json={
            "period_start": start.isoformat(),
            "period_end": end.isoformat(),
        },
    )
    assert denied.status_code == 403
    assert denied.json()["detail"]["code"] == "platform_admin_required"

    response = client.post(
        "/admin/billing/reconciliation/run",
        headers=headers(admin),
        json={
            "period_start": start.isoformat(),
            "period_end": end.isoformat(),
        },
    )
    assert response.status_code == 201, response.text
    run = response.json()
    assert run["status"] == "completed"
    assert run["report_json"]["unbalanced_transactions"] == []
    assert run["report_json"]["unknown_reservations"] == 0

    listed = client.get(
        "/admin/billing/reconciliation/runs",
        headers=headers(admin),
    )
    assert listed.status_code == 200
    assert listed.json()[0]["id"] == run["id"]



def test_currency_minor_unit_precision_matches_iso_currency_exponents() -> None:
    assert currency_minor_unit_exponent("CNY") == 2
    assert currency_minor_unit_exponent("USD") == 2
    assert currency_minor_unit_exponent("JPY") == 0
    assert currency_minor_unit_exponent("KRW") == 0
    assert currency_minor_unit_exponent("KWD") == 3
    assert currency_minor_unit_exponent("CLF") == 4


def test_platform_rbac_grant_revoke_and_audit(client: TestClient) -> None:
    admin = register(client)
    customer = register(client, "customer@example.com")

    access = client.get("/admin/platform/access", headers=headers(admin))
    assert access.status_code == 200
    assert "super_admin" in access.json()["roles"]
    assert "platform.rbac.manage" in access.json()["permissions"]

    granted = client.post(
        f"/admin/platform/users/{customer['user']['id']}/roles/finance",
        headers=headers(admin),
        json={"reason": "财务岗位职责需要"},
    )
    assert granted.status_code == 201, granted.text
    assert granted.json()["role"] == "finance"

    customer_access = client.get(
        "/admin/platform/access", headers=headers(customer)
    )
    assert customer_access.status_code == 200
    assert "finance" in customer_access.json()["roles"]
    assert "platform.billing.read" in customer_access.json()["permissions"]

    revoked = client.request(
        "DELETE",
        f"/admin/platform/users/{customer['user']['id']}/roles/finance",
        headers=headers(admin),
        json={"reason": "岗位调整"},
    )
    assert revoked.status_code == 200, revoked.text

    after_revoke = client.get("/admin/platform/access", headers=headers(customer))
    assert after_revoke.json()["roles"] == []
    assert after_revoke.json()["permissions"] == []

    generator = app.dependency_overrides[get_db]()
    session = next(generator)
    try:
        from app.models.platform_rbac import PlatformAdminAuditLog

        events = session.query(PlatformAdminAuditLog).all()
        assert {event.action for event in events} >= {
            "platform.role.grant",
            "platform.role.revoke",
        }
    finally:
        try:
            next(generator)
        except StopIteration:
            pass


def test_non_admin_cannot_grant_platform_roles(client: TestClient) -> None:
    customer = register(client, "customer@example.com")
    other = register(client, "other@example.com")
    denied = client.post(
        f"/admin/platform/users/{other['user']['id']}/roles/finance",
        headers=headers(customer),
        json={"reason": "不应获得权限"},
    )
    assert denied.status_code == 403
    assert denied.json()["detail"]["code"] == "platform_permission_required"
