import asyncio
from uuid import UUID

from app.api import health
from app.main import app, unhandled_exception_handler
from fastapi.testclient import TestClient
from starlette.requests import Request

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


def test_not_found_response_includes_valid_request_id() -> None:
    response = client.get("/route-that-does-not-exist")

    assert response.status_code == 404
    assert str(UUID(response.headers["X-Request-ID"])) == response.headers["X-Request-ID"]


def test_unhandled_exception_returns_safe_error_envelope() -> None:
    request_id = "9f4a4c84-7d7c-4a3c-a8c1-5d4f8f0c4a12"
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": "/_test/error",
        "raw_path": b"/_test/error",
        "query_string": b"",
        "headers": [],
        "server": ("testserver", 80),
        "client": ("testclient", 12345),
    }
    request = Request(scope)
    request.state.request_id = request_id

    async def invoke_handler():
        try:
            raise RuntimeError("sensitive internal detail")
        except RuntimeError as exc:
            return await unhandled_exception_handler(request, exc)

    response = asyncio.run(invoke_handler())

    assert response.status_code == 500
    assert response.headers["X-Request-ID"] == request_id
    assert b"internal_error" in response.body
    assert b"sensitive internal detail" not in response.body


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
