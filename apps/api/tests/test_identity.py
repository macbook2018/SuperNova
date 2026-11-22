from collections.abc import Generator

import pytest
from app.api.auth import _is_email_unique_violation
from app.db.base import Base
from app.db.session import get_db
from app.main import app, asgi_app
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool


@pytest.fixture
def client() -> Generator[TestClient, None, None]:
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
    with TestClient(asgi_app, raise_server_exceptions=False) as test_client:
        yield test_client
    app.dependency_overrides.clear()
    Base.metadata.drop_all(engine)
    engine.dispose()


def register(client: TestClient, email: str = "master@example.com") -> dict:
    response = client.post(
        "/auth/register",
        json={
            "email": email,
            "password": "correct-horse-battery",
            "display_name": "Master",
        },
    )
    assert response.status_code == 201
    return response.json()


def auth_headers(tokens: dict) -> dict[str, str]:
    return {"Authorization": f"Bearer {tokens['access_token']}"}


def test_register_creates_user_and_private_workspace(client: TestClient) -> None:
    response = client.post(
        "/auth/register",
        json={
            "email": "Master@Example.com",
            "password": "correct-horse-battery",
            "display_name": "Master",
            "workspace_name": "Master 工作空间",
        },
    )

    assert response.status_code == 201
    body = response.json()
    assert body["user"]["email"] == "master@example.com"
    assert body["tenant"]["name"] == "Master 工作空间"
    assert body["tenant"]["role"] == "owner"
    assert body["token_type"] == "bearer"
    assert body["expires_in"] == 900
    assert "password_hash" not in body["user"]
    assert body["access_token"]
    assert body["refresh_token"]

    me = client.get("/auth/me", headers=auth_headers(body))
    assert me.status_code == 200
    assert me.json()["user"]["id"] == body["user"]["id"]
    assert me.json()["tenant"]["id"] == body["tenant"]["id"]


def test_registration_rejects_duplicate_email_case_insensitively(client: TestClient) -> None:
    register(client)
    response = client.post(
        "/auth/register",
        json={
            "email": "MASTER@example.com",
            "password": "correct-horse-battery",
            "display_name": "Second",
        },
    )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "email_already_registered"


def test_registration_requires_strong_password(client: TestClient) -> None:
    response = client.post(
        "/auth/register",
        json={
            "email": "weak@example.com",
            "password": "short",
            "display_name": "Weak",
        },
    )

    assert response.status_code == 422


def test_login_and_invalid_password(client: TestClient) -> None:
    register(client)
    invalid = client.post(
        "/auth/login",
        json={"email": "master@example.com", "password": "incorrect-password"},
    )
    assert invalid.status_code == 401

    valid = client.post(
        "/auth/login",
        json={"email": "MASTER@example.com", "password": "correct-horse-battery"},
    )
    assert valid.status_code == 200
    assert valid.json()["user"]["email"] == "master@example.com"


def test_refresh_rotates_token_and_reuse_revokes_token_family(client: TestClient) -> None:
    tokens = register(client)
    first_refresh = tokens["refresh_token"]

    rotated = client.post("/auth/refresh", json={"refresh_token": first_refresh})
    assert rotated.status_code == 200
    second_refresh = rotated.json()["refresh_token"]
    assert second_refresh != first_refresh

    replay = client.post("/auth/refresh", json={"refresh_token": first_refresh})
    assert replay.status_code == 401

    revoked_family = client.post("/auth/refresh", json={"refresh_token": second_refresh})
    assert revoked_family.status_code == 401


def test_logout_revokes_refresh_token(client: TestClient) -> None:
    tokens = register(client)
    logout = client.post("/auth/logout", json={"refresh_token": tokens["refresh_token"]})

    assert logout.status_code == 204
    refresh = client.post("/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert refresh.status_code == 401


def test_protected_routes_require_valid_access_token(client: TestClient) -> None:
    missing = client.get("/auth/me")
    assert missing.status_code == 401

    tokens = register(client)
    tampered = "x" + tokens["access_token"][1:]
    invalid = client.get("/auth/me", headers={"Authorization": f"Bearer {tampered}"})
    assert invalid.status_code == 401


def test_api_keys_are_user_scoped_and_secret_is_only_returned_once(client: TestClient) -> None:
    user_a = register(client, "a@example.com")
    user_b = register(client, "b@example.com")

    created = client.post(
        "/api-keys",
        headers=auth_headers(user_a),
        json={"name": "我的业务系统"},
    )
    assert created.status_code == 201
    key_body = created.json()
    secret = key_body["secret"]
    key_id = key_body["api_key"]["id"]
    assert secret.startswith("sn_live_")
    assert key_body["api_key"]["key_prefix"].startswith("sn_live_")
    assert key_body["api_key"]["status"] == "active"

    listed_a = client.get("/api-keys", headers=auth_headers(user_a))
    assert listed_a.status_code == 200
    assert len(listed_a.json()) == 1
    assert "secret" not in listed_a.text
    assert secret not in listed_a.text
    assert "key_hash" not in listed_a.text

    listed_b = client.get("/api-keys", headers=auth_headers(user_b))
    assert listed_b.status_code == 200
    assert listed_b.json() == []

    cross_tenant_revoke = client.delete(
        f"/api-keys/{key_id}",
        headers=auth_headers(user_b),
    )
    assert cross_tenant_revoke.status_code == 404

    revoked = client.delete(f"/api-keys/{key_id}", headers=auth_headers(user_a))
    assert revoked.status_code == 204

    after_revoke = client.get("/api-keys", headers=auth_headers(user_a))
    assert after_revoke.json()[0]["status"] == "revoked"


def test_api_key_rotation_revokes_old_key_and_returns_new_secret(client: TestClient) -> None:
    tokens = register(client)
    headers = auth_headers(tokens)
    created = client.post("/api-keys", headers=headers, json={"name": "服务端"})
    key_id = created.json()["api_key"]["id"]
    old_secret = created.json()["secret"]

    rotated = client.post(f"/api-keys/{key_id}/rotate", headers=headers)
    assert rotated.status_code == 200
    assert rotated.json()["secret"] != old_secret
    assert rotated.json()["api_key"]["name"] == "服务端"

    listed = client.get("/api-keys", headers=headers).json()
    statuses = {item["id"]: item["status"] for item in listed}
    assert statuses[key_id] == "revoked"
    assert statuses[rotated.json()["api_key"]["id"]] == "active"


def test_api_key_expiration_is_validated(client: TestClient) -> None:
    tokens = register(client)
    response = client.post(
        "/api-keys",
        headers=auth_headers(tokens),
        json={"name": "临时密钥", "expires_in_days": 366},
    )
    assert response.status_code == 422


def test_email_integrity_error_classifier_only_matches_email_constraint() -> None:
    email_error = IntegrityError(
        "INSERT INTO users",
        {},
        Exception("UNIQUE constraint failed: users.email"),
    )
    other_error = IntegrityError(
        "INSERT INTO tenants",
        {},
        Exception("UNIQUE constraint failed: tenants.name"),
    )

    assert _is_email_unique_violation(email_error) is True
    assert _is_email_unique_violation(other_error) is False


def test_register_does_not_misreport_other_integrity_errors(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_commit(self: Session) -> None:
        raise IntegrityError(
            "INSERT INTO tenants",
            {},
            Exception("UNIQUE constraint failed: tenants.name"),
        )

    monkeypatch.setattr(Session, "commit", fail_commit)
    response = client.post(
        "/auth/register",
        headers={"Origin": "http://localhost:5173"},
        json={
            "email": "new-user@example.com",
            "password": "correct-horse-battery",
            "display_name": "New User",
        },
    )

    assert response.status_code == 500
    assert response.json()["error"]["code"] == "internal_error"
    assert response.json()["error"]["request_id"]
    assert response.headers["access-control-allow-origin"] == "http://localhost:5173"



def test_public_registration_cannot_claim_reserved_platform_admin_email(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from types import SimpleNamespace
    from app.api import auth

    monkeypatch.setattr(
        auth,
        "get_settings",
        lambda: SimpleNamespace(platform_admin_email_set=frozenset({"reserved@example.com"})),
    )
    response = client.post(
        "/auth/register",
        json={
            "email": "RESERVED@example.com",
            "password": "correct-horse-battery",
            "display_name": "Reserved",
        },
    )
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "reserved_platform_admin_email"
