import json
from collections.abc import Generator

import pytest
from app.api import gateway
from app.db.base import Base
from app.db.session import get_db
from app.main import app, asgi_app
from fastapi import HTTPException
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
    monkeypatch.setattr(gateway, "_validate_provider_target", lambda _url: None)
    with TestClient(asgi_app, raise_server_exceptions=False) as test_client:
        yield test_client
    app.dependency_overrides.clear()
    Base.metadata.drop_all(engine)
    engine.dispose()


def register(client: TestClient, email: str = "gateway@example.com") -> dict:
    response = client.post(
        "/auth/register",
        json={
            "email": email,
            "password": "correct-horse-battery",
            "display_name": "Gateway Owner",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def jwt_headers(tokens: dict) -> dict[str, str]:
    return {"Authorization": f"Bearer {tokens['access_token']}"}


def create_provider(client: TestClient, tokens: dict) -> dict:
    response = client.post(
        "/model-providers",
        headers=jwt_headers(tokens),
        json={
            "name": "DeepSeek",
            "provider_type": "deepseek",
            "base_url": "https://api.deepseek.com/v1",
            "api_key": "sk-provider-secret-1234",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def create_persona(
    client: TestClient,
    tokens: dict,
    provider_id: str | None,
    slug: str = "customer-support",
    max_tokens: int = 1024,
) -> dict:
    response = client.post(
        "/personas",
        headers=jwt_headers(tokens),
        json={
            "name": "智能客服",
            "slug": slug,
            "description": "测试人格",
            "system_prompt": "你是一名耐心、专业的客服。",
            "provider_id": provider_id,
            "model_name": "deepseek-chat",
            "temperature": 0.4,
            "top_p": 0.9,
            "max_tokens": max_tokens,
            "enabled": True,
            "knowledge_base_ids": [],
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def create_api_key(client: TestClient, tokens: dict) -> str:
    response = client.post(
        "/api-keys",
        headers=jwt_headers(tokens),
        json={"name": "Gateway integration"},
    )
    assert response.status_code == 201, response.text
    return response.json()["secret"]


class FakeResponse:
    def __init__(self, payload: dict, status_code: int = 200, lines: list[str] | None = None):
        self._payload = payload
        self.status_code = status_code
        self._lines = lines or []

    def json(self) -> dict:
        return self._payload

    def iter_lines(self):
        return iter(self._lines)

    def close(self) -> None:
        return None


class FakeClient:
    calls: list[dict] = []
    response: FakeResponse | None = None

    def __init__(self, **_kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def post(self, url: str, headers: dict, json: dict):
        self.calls.append({"url": url, "headers": headers, "json": json})
        assert self.response is not None
        return self.response

    def build_request(self, method: str, url: str, **kwargs):
        return {"method": method, "url": url, **kwargs}

    def send(self, request: dict, stream: bool = False):
        self.calls.append({"request": request, "stream": stream})
        assert self.response is not None
        return self.response

    def close(self) -> None:
        return None


def test_models_uses_one_user_key_for_all_enabled_personas(client: TestClient) -> None:
    owner = register(client)
    provider = create_provider(client, owner)
    create_persona(client, owner, provider["id"], "customer-support")
    create_persona(client, owner, provider["id"], "sales-assistant")
    key = create_api_key(client, owner)

    response = client.get("/v1/models", headers={"Authorization": f"Bearer {key}"})

    assert response.status_code == 200, response.text
    assert response.json()["object"] == "list"
    assert {item["id"] for item in response.json()["data"]} == {
        "customer-support",
        "sales-assistant",
    }


def test_chat_completion_routes_persona_and_records_actual_usage(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner = register(client)
    provider = create_provider(client, owner)
    persona = create_persona(client, owner, provider["id"], max_tokens=1024)
    key = create_api_key(client, owner)
    FakeClient.calls = []
    FakeClient.response = FakeResponse(
        {
            "id": "chatcmpl-test",
            "object": "chat.completion",
            "created": 1800000000,
            "model": "deepseek-chat",
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": "您好，有什么可以帮您？"},
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 23, "completion_tokens": 11, "total_tokens": 34},
        }
    )
    monkeypatch.setattr(gateway.httpx, "Client", FakeClient)

    response = client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {key}"},
        json={
            "model": persona["slug"],
            "messages": [{"role": "user", "content": "你好"}],
            "max_tokens": 4096,
        },
    )

    assert response.status_code == 200, response.text
    assert response.json()["model"] == "customer-support"
    assert response.json()["usage"]["total_tokens"] == 34
    call = FakeClient.calls[0]
    assert call["url"] == "https://api.deepseek.com/v1/chat/completions"
    assert call["headers"]["Authorization"] == "Bearer sk-provider-secret-1234"
    assert call["json"]["model"] == "deepseek-chat"
    assert call["json"]["max_tokens"] == 1024
    assert call["json"]["messages"][0] == {
        "role": "system",
        "content": "你是一名耐心、专业的客服。",
    }

    summary = client.get("/usage/summary", headers=jwt_headers(owner))
    assert summary.status_code == 200, summary.text
    assert summary.json()["all_time"]["requests"] == 1
    assert summary.json()["all_time"]["total_tokens"] == 34
    assert summary.json()["all_time"]["prompt_tokens"] == 23
    assert summary.json()["all_time"]["completion_tokens"] == 11

    records = client.get("/usage/records", headers=jwt_headers(owner))
    assert records.status_code == 200
    assert records.json()[0]["persona_slug"] == "customer-support"
    assert records.json()[0]["status"] == "succeeded"


def test_streaming_chat_completion_records_usage_and_openai_done_marker(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner = register(client)
    provider = create_provider(client, owner)
    persona = create_persona(client, owner, provider["id"])
    key = create_api_key(client, owner)
    chunks = [
        "data: " + json.dumps(
            {
                "id": "chatcmpl-stream",
                "object": "chat.completion.chunk",
                "created": 1800000000,
                "model": "deepseek-chat",
                "choices": [{"index": 0, "delta": {"content": "你好"}, "finish_reason": None}],
            },
            ensure_ascii=False,
        ),
        "data: " + json.dumps(
            {
                "id": "chatcmpl-stream",
                "object": "chat.completion.chunk",
                "created": 1800000000,
                "model": "deepseek-chat",
                "choices": [],
                "usage": {"prompt_tokens": 5, "completion_tokens": 2, "total_tokens": 7},
            },
            ensure_ascii=False,
        ),
        "data: [DONE]",
    ]
    FakeClient.calls = []
    FakeClient.response = FakeResponse({}, lines=chunks)
    monkeypatch.setattr(gateway.httpx, "Client", FakeClient)

    response = client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {key}"},
        json={
            "model": persona["slug"],
            "messages": [{"role": "user", "content": "你好"}],
            "stream": True,
        },
    )

    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("text/event-stream")
    assert '"model":"customer-support"' in response.text
    assert "data: [DONE]" in response.text
    assert FakeClient.calls[0]["stream"] is True

    summary = client.get("/usage/summary", headers=jwt_headers(owner))
    assert summary.json()["all_time"]["requests"] == 1
    assert summary.json()["all_time"]["total_tokens"] == 7


def test_gateway_rejects_missing_or_revoked_api_key(client: TestClient) -> None:
    missing = client.get("/v1/models")
    assert missing.status_code == 401
    assert missing.json()["error"]["code"] == "invalid_api_key"

    owner = register(client)
    key_response = client.post(
        "/api-keys",
        headers=jwt_headers(owner),
        json={"name": "To revoke"},
    )
    key_id = key_response.json()["api_key"]["id"]
    secret = key_response.json()["secret"]
    revoked = client.delete(f"/api-keys/{key_id}", headers=jwt_headers(owner))
    assert revoked.status_code == 204

    rejected = client.get("/v1/models", headers={"Authorization": f"Bearer {secret}"})
    assert rejected.status_code == 401
    assert rejected.json()["error"]["code"] == "invalid_api_key"


def test_gateway_cannot_resolve_another_tenants_persona(client: TestClient) -> None:
    owner_a = register(client, "a@example.com")
    owner_b = register(client, "b@example.com")
    provider_a = create_provider(client, owner_a)
    create_persona(client, owner_a, provider_a["id"], "private-persona")
    key_b = create_api_key(client, owner_b)

    response = client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {key_b}"},
        json={
            "model": "private-persona",
            "messages": [{"role": "user", "content": "尝试越权"}],
        },
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "model_not_found"


def test_gateway_rejects_persona_without_provider(client: TestClient) -> None:
    owner = register(client)
    persona = create_persona(client, owner, None)
    key = create_api_key(client, owner)

    response = client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {key}"},
        json={
            "model": persona["slug"],
            "messages": [{"role": "user", "content": "你好"}],
        },
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "model_provider_not_configured"


def test_gateway_rejects_non_public_provider_address() -> None:
    with pytest.raises(HTTPException) as exc:
        gateway._validate_provider_target("https://192.168.1.20/v1")
    assert exc.value.status_code == 403
    assert exc.value.detail["code"] == "provider_url_not_allowed"


def test_gateway_request_validation_uses_openai_error_shape(client: TestClient) -> None:
    owner = register(client)
    key = create_api_key(client, owner)
    response = client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {key}"},
        json={"model": "x", "messages": [], "unsupported_field": True},
    )
    assert response.status_code == 422
    assert "error" in response.json()
    assert response.json()["error"]["type"] == "invalid_request_error"
