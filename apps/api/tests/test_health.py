from uuid import UUID

from app.api import health
from app.main import app
from fastapi.testclient import TestClient

client = TestClient(app)


def test_liveness_returns_alive() -> None:
    response = client.get("/health/live")

    assert response.status_code == 200
    assert response.json() == {"status": "alive"}


def test_request_id_is_returned() -> None:
    request_id = "9f4a4c84-7d7c-4a3c-a8c1-5d4f8f0c4a12"
    response = client.get("/health/live", headers={"X-Request-ID": request_id})

    assert response.status_code == 200
    assert response.headers["X-Request-ID"] == request_id


def test_request_id_is_generated_when_missing() -> None:
    response = client.get("/health/live")

    assert response.status_code == 200
    assert str(UUID(response.headers["X-Request-ID"])) == response.headers["X-Request-ID"]


def test_invalid_request_id_is_replaced() -> None:
    response = client.get("/health/live", headers={"X-Request-ID": "not-a-uuid"})

    assert response.status_code == 200
    assert response.headers["X-Request-ID"] != "not-a-uuid"
    assert str(UUID(response.headers["X-Request-ID"])) == response.headers["X-Request-ID"]


def test_readiness_returns_ready_when_dependencies_are_healthy(monkeypatch) -> None:
    async def healthy(_: str) -> str:
        return "ok"

    monkeypatch.setattr(health, "_check_database", healthy)
    monkeypatch.setattr(health, "_check_redis", healthy)

    response = client.get("/health/ready")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ready",
        "dependencies": {"postgres": "ok", "redis": "ok"},
    }


def test_readiness_returns_503_when_a_dependency_is_unavailable(monkeypatch) -> None:
    async def healthy(_: str) -> str:
        return "ok"

    async def unavailable(_: str) -> str:
        return "error"

    monkeypatch.setattr(health, "_check_database", healthy)
    monkeypatch.setattr(health, "_check_redis", unavailable)

    response = client.get("/health/ready")

    assert response.status_code == 503
    assert response.json() == {
        "status": "not_ready",
        "dependencies": {"postgres": "ok", "redis": "error"},
    }
