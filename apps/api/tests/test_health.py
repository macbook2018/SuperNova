from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_liveness_returns_alive() -> None:
    response = client.get("/health/live")

    assert response.status_code == 200
    assert response.json() == {"status": "alive"}


def test_request_id_is_returned() -> None:
    response = client.get(
        "/health/live",
        headers={"X-Request-ID": "9f4a4c84-7d7c-4a3c-a8c1-5d4f8f0c4a12"},
    )

    assert response.status_code == 200
    assert response.headers["X-Request-ID"] == "9f4a4c84-7d7c-4a3c-a8c1-5d4f8f0c4a12"


def test_invalid_request_id_is_replaced() -> None:
    response = client.get("/health/live", headers={"X-Request-ID": "not-a-uuid"})

    assert response.status_code == 200
    assert response.headers["X-Request-ID"] != "not-a-uuid"
