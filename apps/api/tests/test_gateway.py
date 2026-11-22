import hashlib
import json
from collections.abc import Generator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from app.api import gateway
from app.billing.metering import (
    reserve_gateway_request,
    run_billing_reconciliation,
    settle_unknown_reservation_with_usage,
)
from app.db.base import Base
from app.db.session import get_db
from app.main import app, asgi_app
from app.models.billing import (
    BillingAuditEvent,
    BillingLedgerPosting,
    BillingLedgerTransaction,
    BillingPlan,
    BillingPlanEntitlement,
    BillingRatedItem,
    BillingReservation,
    BillingReservationItem,
    BillingUsageEvent,
    PlatformModel,
    PlatformModelPrice,
    TenantSubscription,
)
from app.models.usage import UsageRecord
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, func, select
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
    plan_id = uuid4()
    with testing_session() as seed_db:
        seed_db.add(
            BillingPlan(
                id=plan_id,
                code="gateway-test",
                revision=1,
                name="Gateway Test",
                billing_interval="month",
                currency="CNY",
                subscription_price_minor=0,
                status="active",
            )
        )
        seed_db.flush()
        seed_db.add_all(
            [
                BillingPlanEntitlement(
                    id=uuid4(),
                    plan_id=plan_id,
                    resource_code=resource_code,
                    unit=unit,
                    included_units=1_000_000_000,
                    hard_limit_units=None,
                    overage_rate_minor_per_unit=Decimal("0"),
                    overage_behavior="block",
                )
                for resource_code, unit in [
                    ("api_requests", "requests"),
                    ("platform_model_requests", "requests"),
                    ("platform_input_tokens", "tokens"),
                    ("platform_output_tokens", "tokens"),
                ]
            ]
        )
        seed_db.commit()
    app.state.test_session_factory = testing_session
    app.state.test_billing_plan_id = plan_id

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
    tokens = response.json()
    now = datetime.now(UTC)
    with app.state.test_session_factory() as seed_db:
        seed_db.add(
            TenantSubscription(
                id=uuid4(),
                tenant_id=UUID(tokens["tenant"]["id"]),
                plan_id=app.state.test_billing_plan_id,
                status="active",
                current_period_start=now - timedelta(minutes=1),
                current_period_end=now + timedelta(days=30),
            )
        )
        seed_db.commit()
    return tokens


def jwt_headers(tokens: dict) -> dict[str, str]:
    return {"Authorization": f"Bearer {tokens['access_token']}"}


def create_provider(client: TestClient, tokens: dict) -> dict:
    response = client.post(
        "/model-providers",
        headers=jwt_headers(tokens),
        json={
            "name": "DeepSeek",
            "provider_type": "deepseek",
            "base_url": "https://api.deepseek.com",
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
            "model_name": "deepseek-flash",
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
            "model": "deepseek-flash",
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
            "user": "wxid_xxx",
            "sender_profile": {
                "wxid": "wxid_xxx",
                "nickname": "张三",
                "remark": "张总",
                "avatar_url": "https://example.com/avatar.png",
                "labels": ["意向客户", "已成交"],
            },
        },
    )

    assert response.status_code == 200, response.text
    assert response.json()["model"] == "customer-support"
    assert response.json()["usage"]["total_tokens"] == 34
    call = FakeClient.calls[0]
    assert call["url"] == "https://api.deepseek.com/chat/completions"
    assert call["headers"]["Authorization"] == "Bearer sk-provider-secret-1234"
    assert call["json"]["model"] == "deepseek-flash"
    assert call["json"]["max_tokens"] == 1024
    system_content = call["json"]["messages"][0]["content"]
    assert system_content.startswith("你是一名耐心、专业的客服。")
    assert '"nickname":"张三"' in system_content
    assert '"remark":"张总"' in system_content
    assert '"labels":["意向客户","已成交"]' in system_content
    assert "wxid_xxx" not in system_content
    assert "avatar.png" not in system_content
    assert "user" not in call["json"]

    summary = client.get("/usage/summary", headers=jwt_headers(owner))
    assert summary.status_code == 200, summary.text
    assert summary.json()["all_time"]["requests"] == 1
    assert summary.json()["all_time"]["total_tokens"] == 34
    assert summary.json()["all_time"]["prompt_tokens"] == 23
    assert summary.json()["all_time"]["completion_tokens"] == 11

    records = client.get("/usage/records", headers=jwt_headers(owner))
    assert records.status_code == 200
    assert records.json()[0]["persona_slug"] == "customer-support"
    assert records.json()[0]["model_source"] == "byok"
    assert records.json()[0]["platform_model_id"] is None
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
                "model": "deepseek-flash",
                "choices": [{"index": 0, "delta": {"content": "你好"}, "finish_reason": None}],
            },
            ensure_ascii=False,
        ),
        "data: " + json.dumps(
            {
                "id": "chatcmpl-stream",
                "object": "chat.completion.chunk",
                "created": 1800000000,
                "model": "deepseek-flash",
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


def _sample_gateway_citations() -> list[dict]:
    return [
        {
            "index": 1,
            "knowledge_base_id": str(uuid4()),
            "knowledge_base_name": "客服知识库",
            "document_id": str(uuid4()),
            "document_name": "退货政策.md",
            "source_type": "upload",
            "source_url": None,
            "page_number": None,
            "sheet_name": None,
            "slide_number": None,
            "chunk_id": str(uuid4()),
            "chunk_index": 0,
            "score": 0.93,
            "snippet": "商品支持签收后 30 天内退货。",
        }
    ]


def test_chat_completion_injects_retrieved_context_and_returns_citations(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner = register(client)
    provider = create_provider(client, owner)
    persona = create_persona(client, owner, provider["id"])
    key = create_api_key(client, owner)
    rag_context = "退货政策：商品支持签收后 30 天内退货。"
    citations = _sample_gateway_citations()
    retrieval_calls: list[dict] = []

    def fake_retrieval(_db, **kwargs):
        retrieval_calls.append(kwargs)
        return rag_context, citations

    monkeypatch.setattr(gateway, "retrieve_persona_context", fake_retrieval)
    FakeClient.calls = []
    FakeClient.response = FakeResponse(
        {
            "id": "chatcmpl-rag",
            "object": "chat.completion",
            "created": 1800000000,
            "model": "deepseek-flash",
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": "支持签收后 30 天内退货。"},
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 30, "completion_tokens": 12, "total_tokens": 42},
        }
    )
    monkeypatch.setattr(gateway.httpx, "Client", FakeClient)

    response = client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {key}"},
        json={
            "model": persona["slug"],
            "messages": [{"role": "user", "content": "退货期限是多久？"}],
        },
    )

    assert response.status_code == 200, response.text
    assert response.json()["supernova"]["citations"] == citations
    assert retrieval_calls[0]["tenant_id"] == UUID(owner["tenant"]["id"])
    assert retrieval_calls[0]["persona_id"] == UUID(persona["id"])
    assert retrieval_calls[0]["query"] == "退货期限是多久？"
    upstream_messages = FakeClient.calls[0]["json"]["messages"]
    assert rag_context in upstream_messages[0]["content"]
    assert "知识库检索参考资料" in upstream_messages[0]["content"]


def test_streaming_chat_completion_attaches_retrieved_citations(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner = register(client)
    provider = create_provider(client, owner)
    persona = create_persona(client, owner, provider["id"])
    key = create_api_key(client, owner)
    rag_context = "退货政策：商品支持签收后 30 天内退货。"
    citations = _sample_gateway_citations()
    monkeypatch.setattr(
        gateway,
        "retrieve_persona_context",
        lambda _db, **_kwargs: (rag_context, citations),
    )
    chunks = [
        "data: " + json.dumps(
            {
                "id": "chatcmpl-rag-stream",
                "object": "chat.completion.chunk",
                "created": 1800000000,
                "model": "deepseek-flash",
                "choices": [
                    {
                        "index": 0,
                        "delta": {"content": "支持签收后 30 天内退货。"},
                        "finish_reason": None,
                    }
                ],
            },
            ensure_ascii=False,
        ),
        "data: " + json.dumps(
            {
                "id": "chatcmpl-rag-stream",
                "object": "chat.completion.chunk",
                "created": 1800000000,
                "model": "deepseek-flash",
                "choices": [],
                "usage": {"prompt_tokens": 30, "completion_tokens": 12, "total_tokens": 42},
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
            "messages": [{"role": "user", "content": "退货期限是多久？"}],
            "stream": True,
        },
    )

    assert response.status_code == 200, response.text
    assert "data: [DONE]" in response.text
    events = [
        json.loads(line[6:])
        for line in response.text.splitlines()
        if line.startswith("data: ") and line != "data: [DONE]"
    ]
    assert any(
        event.get("supernova", {}).get("citations") == citations
        for event in events
    )
    request_payload = FakeClient.calls[0]["request"]["json"]
    assert rag_context in request_payload["messages"][0]["content"]


def test_chat_completions_url_uses_provider_specific_base_url_rules() -> None:
    assert (
        gateway._chat_completions_url("https://api.deepseek.com", "deepseek")
        == "https://api.deepseek.com/chat/completions"
    )
    assert (
        gateway._chat_completions_url("https://api.deepseek.com/v1", "deepseek")
        == "https://api.deepseek.com/v1/chat/completions"
    )
    assert (
        gateway._chat_completions_url("https://api.example.com", "openai_compatible")
        == "https://api.example.com/v1/chat/completions"
    )
    assert (
        gateway._chat_completions_url("https://api.example.com/v1", "openai_compatible")
        == "https://api.example.com/v1/chat/completions"
    )


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


def test_gateway_rejects_provider_redirect_instead_of_following_it(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner = register(client)
    provider = create_provider(client, owner)
    persona = create_persona(client, owner, provider["id"])
    key = create_api_key(client, owner)
    FakeClient.calls = []
    FakeClient.response = FakeResponse({}, status_code=302)
    monkeypatch.setattr(gateway.httpx, "Client", FakeClient)

    response = client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {key}"},
        json={
            "model": persona["slug"],
            "messages": [{"role": "user", "content": "你好"}],
        },
    )

    assert response.status_code == 502
    assert response.json()["error"]["code"] == "upstream_error"



def test_platform_model_routes_without_tenant_provider_and_records_source(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner = register(client)
    monkeypatch.setattr(gateway, "resolve_platform_api_key", lambda _ref: "sk-platform-secret-123")
    model_id = uuid4()
    now = datetime.now(UTC)
    with app.state.test_session_factory() as seed_db:
        seed_db.add(
            PlatformModel(
                id=model_id,
                code="platform-deepseek",
                display_name="SuperNova DeepSeek",
                provider_type="deepseek",
                upstream_model_name="deepseek-chat",
                base_url="https://api.deepseek.com",
                credential_ref="deepseek-primary",
                enabled=True,
            )
        )
        seed_db.add(
            PlatformModelPrice(
                id=uuid4(),
                platform_model_id=model_id,
                revision=1,
                currency="CNY",
                input_per_million_minor=Decimal("200"),
                output_per_million_minor=Decimal("2000"),
                effective_from=now - timedelta(days=1),
                effective_until=None,
            )
        )
        seed_db.commit()

    created = client.post(
        "/personas",
        headers=jwt_headers(owner),
        json={
            "name": "平台客服",
            "slug": "platform-support",
            "description": "平台模型测试",
            "system_prompt": "你是一名专业客服。",
            "model_source": "platform",
            "platform_model_id": str(model_id),
            "temperature": 0.4,
            "top_p": 0.9,
            "max_tokens": 512,
            "enabled": True,
            "knowledge_base_ids": [],
        },
    )
    assert created.status_code == 201, created.text
    persona = created.json()
    assert persona["model_source"] == "platform"
    assert persona["provider_id"] is None
    assert persona["model_name"] == "deepseek-chat"

    key = create_api_key(client, owner)
    FakeClient.calls = []
    FakeClient.response = FakeResponse(
        {
            "id": "chatcmpl-platform",
            "object": "chat.completion",
            "created": 1800000000,
            "model": "deepseek-chat",
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": "平台模型已接通"},
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 12, "completion_tokens": 8, "total_tokens": 20},
        }
    )
    monkeypatch.setattr(gateway.httpx, "Client", FakeClient)
    response = client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {key}"},
        json={
            "model": persona["slug"],
            "messages": [{"role": "user", "content": "你好"}],
        },
    )
    assert response.status_code == 200, response.text
    call = FakeClient.calls[0]
    assert call["url"] == "https://api.deepseek.com/chat/completions"
    assert call["headers"]["Authorization"] == "Bearer sk-platform-secret-123"
    assert call["json"]["model"] == "deepseek-chat"

    records = client.get("/usage/records", headers=jwt_headers(owner))
    assert records.status_code == 200
    assert records.json()[0]["model_source"] == "platform"
    assert records.json()[0]["platform_model_id"] == str(model_id)


def test_gateway_blocks_new_request_when_api_request_entitlement_exhausted(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner = register(client)
    with app.state.test_session_factory() as seed_db:
        entitlement = seed_db.scalar(
            select(BillingPlanEntitlement).where(
                BillingPlanEntitlement.plan_id == app.state.test_billing_plan_id,
                BillingPlanEntitlement.resource_code == "api_requests",
            )
        )
        entitlement.included_units = 1
        seed_db.commit()

    provider = create_provider(client, owner)
    persona = create_persona(client, owner, provider["id"])
    key = create_api_key(client, owner)
    FakeClient.calls = []
    FakeClient.response = FakeResponse(
        {
            "id": "chatcmpl-quota",
            "object": "chat.completion",
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": "ok"},
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 2, "completion_tokens": 1, "total_tokens": 3},
        }
    )
    monkeypatch.setattr(gateway.httpx, "Client", FakeClient)
    payload = {
        "model": persona["slug"],
        "messages": [{"role": "user", "content": "hello"}],
    }
    first = client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {key}"},
        json=payload,
    )
    second = client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {key}"},
        json=payload,
    )
    assert first.status_code == 200, first.text
    assert second.status_code == 429
    assert second.json()["error"]["code"] == "quota_exceeded"
    assert len(FakeClient.calls) == 1



def test_persona_can_switch_between_platform_and_byok_without_cross_source_references(
    client: TestClient,
) -> None:
    owner = register(client)
    provider = create_provider(client, owner)
    persona = create_persona(client, owner, provider["id"])
    model_id = uuid4()
    now = datetime.now(UTC)
    with app.state.test_session_factory() as seed_db:
        seed_db.add(
            PlatformModel(
                id=model_id,
                code="switch-model",
                display_name="Switch Model",
                provider_type="deepseek",
                upstream_model_name="deepseek-chat",
                base_url="https://api.deepseek.com",
                credential_ref="deepseek-primary",
                enabled=True,
            )
        )
        seed_db.add(
            PlatformModelPrice(
                id=uuid4(),
                platform_model_id=model_id,
                revision=1,
                currency="CNY",
                input_per_million_minor=Decimal("200"),
                output_per_million_minor=Decimal("2000"),
                effective_from=now - timedelta(days=1),
                effective_until=None,
            )
        )
        seed_db.commit()

    switched_to_platform = client.patch(
        f"/personas/{persona['id']}",
        headers=jwt_headers(owner),
        json={
            "model_source": "platform",
            "platform_model_id": str(model_id),
            "provider_id": None,
        },
    )
    assert switched_to_platform.status_code == 200, switched_to_platform.text
    platform_persona = switched_to_platform.json()
    assert platform_persona["model_source"] == "platform"
    assert platform_persona["provider_id"] is None
    assert platform_persona["platform_model_id"] == str(model_id)
    assert platform_persona["model_name"] == "deepseek-chat"

    switched_to_byok = client.patch(
        f"/personas/{persona['id']}",
        headers=jwt_headers(owner),
        json={
            "model_source": "byok",
            "provider_id": provider["id"],
            "platform_model_id": None,
            "model_name": "deepseek-flash",
        },
    )
    assert switched_to_byok.status_code == 200, switched_to_byok.text
    byok_persona = switched_to_byok.json()
    assert byok_persona["model_source"] == "byok"
    assert byok_persona["provider_id"] == provider["id"]
    assert byok_persona["platform_model_id"] is None
    assert byok_persona["model_name"] == "deepseek-flash"


def test_new_tenant_requires_subscription_for_byok_calls(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner = register(client)
    provider = create_provider(client, owner)
    persona = create_persona(client, owner, provider["id"])
    key = create_api_key(client, owner)
    FakeClient.calls = []
    FakeClient.response = FakeResponse(
        {
            "id": "chatcmpl-no-subscription",
            "object": "chat.completion",
            "choices": [],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }
    )
    monkeypatch.setattr(gateway.httpx, "Client", FakeClient)

    with app.state.test_session_factory() as seed_db:
        seed_db.query(TenantSubscription).filter(
            TenantSubscription.tenant_id == UUID(owner["tenant"]["id"])
        ).delete()
        seed_db.commit()

    response = client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {key}"},
        json={
            "model": persona["slug"],
            "messages": [{"role": "user", "content": "hello"}],
        },
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "subscription_required"
    assert FakeClient.calls == []


def test_historical_tenant_without_billing_policy_keeps_byok_compatibility(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner = register(client)
    provider = create_provider(client, owner)
    persona = create_persona(client, owner, provider["id"])
    key = create_api_key(client, owner)
    FakeClient.calls = []
    FakeClient.response = FakeResponse(
        {
            "id": "chatcmpl-legacy",
            "object": "chat.completion",
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": "legacy ok"},
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 2, "completion_tokens": 2, "total_tokens": 4},
        }
    )
    monkeypatch.setattr(gateway.httpx, "Client", FakeClient)

    from app.models.billing import TenantBillingPolicy

    with app.state.test_session_factory() as seed_db:
        seed_db.query(TenantSubscription).filter(
            TenantSubscription.tenant_id == UUID(owner["tenant"]["id"])
        ).delete()
        seed_db.query(TenantBillingPolicy).filter(
            TenantBillingPolicy.tenant_id == UUID(owner["tenant"]["id"])
        ).delete()
        seed_db.commit()

    response = client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {key}"},
        json={
            "model": persona["slug"],
            "messages": [{"role": "user", "content": "hello"}],
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["choices"][0]["message"]["content"] == "legacy ok"


def test_idempotency_key_replays_success_without_second_provider_call(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner = register(client)
    provider = create_provider(client, owner)
    persona = create_persona(client, owner, provider["id"])
    key = create_api_key(client, owner)
    FakeClient.calls = []
    FakeClient.response = FakeResponse(
        {
            "id": "chatcmpl-idempotent",
            "object": "chat.completion",
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": "只调用一次"},
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 7, "completion_tokens": 3, "total_tokens": 10},
        }
    )
    monkeypatch.setattr(gateway.httpx, "Client", FakeClient)
    headers = {
        "Authorization": f"Bearer {key}",
        "Idempotency-Key": "support-ticket-2026-0001",
    }
    payload = {
        "model": persona["slug"],
        "messages": [{"role": "user", "content": "请回复"}],
    }

    first = client.post("/v1/chat/completions", headers=headers, json=payload)
    second = client.post("/v1/chat/completions", headers=headers, json=payload)

    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    assert second.json() == first.json()
    assert len(FakeClient.calls) == 1

    with app.state.test_session_factory() as db:
        reservation = db.scalar(
            select(BillingReservation).where(
                BillingReservation.tenant_id == UUID(owner["tenant"]["id"]),
                BillingReservation.event_key == "support-ticket-2026-0001",
            )
        )
        assert reservation is not None
        assert reservation.state == "settled"
        assert reservation.response_json == first.json()
        assert db.scalar(
            select(func.count(UsageRecord.id)).where(
                UsageRecord.tenant_id == UUID(owner["tenant"]["id"])
            )
        ) == 1
        assert db.scalar(
            select(func.count(BillingUsageEvent.id)).where(
                BillingUsageEvent.reservation_id == reservation.id
            )
        ) == 3


def test_idempotency_key_cannot_be_reused_for_different_payload(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner = register(client)
    provider = create_provider(client, owner)
    persona = create_persona(client, owner, provider["id"])
    key = create_api_key(client, owner)
    FakeClient.calls = []
    FakeClient.response = FakeResponse(
        {
            "id": "chatcmpl-idempotency-conflict",
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": "ok"},
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 2, "completion_tokens": 1, "total_tokens": 3},
        }
    )
    monkeypatch.setattr(gateway.httpx, "Client", FakeClient)
    headers = {
        "Authorization": f"Bearer {key}",
        "Idempotency-Key": "same-key-different-body",
    }
    first = client.post(
        "/v1/chat/completions",
        headers=headers,
        json={"model": persona["slug"], "messages": [{"role": "user", "content": "first"}]},
    )
    other_key = create_api_key(client, owner)
    cross_key = client.post(
        "/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {other_key}",
            "Idempotency-Key": "same-key-different-body",
        },
        json={"model": persona["slug"], "messages": [{"role": "user", "content": "first"}]},
    )
    second = client.post(
        "/v1/chat/completions",
        headers=headers,
        json={"model": persona["slug"], "messages": [{"role": "user", "content": "second"}]},
    )

    assert first.status_code == 200
    assert cross_key.status_code == 409
    assert cross_key.json()["error"]["code"] == "idempotency_key_conflict"
    assert second.status_code == 409
    assert second.json()["error"]["code"] == "idempotency_key_conflict"
    assert len(FakeClient.calls) == 1


def test_held_request_reservation_blocks_quota_overbooking(client: TestClient) -> None:
    owner = register(client)
    tenant_id = UUID(owner["tenant"]["id"])
    with app.state.test_session_factory() as db:
        entitlement = db.scalar(
            select(BillingPlanEntitlement).where(
                BillingPlanEntitlement.plan_id == app.state.test_billing_plan_id,
                BillingPlanEntitlement.resource_code == "api_requests",
            )
        )
        entitlement.included_units = 1
        entitlement.hard_limit_units = 1
        db.commit()

    request_hash = hashlib.sha256(b"pending request").hexdigest()
    with app.state.test_session_factory() as db:
        first = reserve_gateway_request(
            db,
            tenant_id=tenant_id,
            idempotency_key="pending-request-1",
            request_hash=request_hash,
            model_source="byok",
            input_token_reservation=0,
            output_token_reservation=0,
            price_version_id=None,
        )
    assert first.reservation_id is not None

    with app.state.test_session_factory() as db:
        with pytest.raises(HTTPException) as exc:
            reserve_gateway_request(
                db,
                tenant_id=tenant_id,
                idempotency_key="pending-request-2",
                request_hash=hashlib.sha256(b"another request").hexdigest(),
                model_source="byok",
                input_token_reservation=0,
                output_token_reservation=0,
                price_version_id=None,
            )
    assert exc.value.status_code == 429
    assert exc.value.detail["code"] == "quota_exceeded"

    snapshot = client.get("/billing/entitlements", headers=jwt_headers(owner))
    api_quota = next(
        item for item in snapshot.json()["resources"]
        if item["resource_code"] == "api_requests"
    )
    assert api_quota["reserved_units"] == 1
    assert api_quota["remaining_units"] == 0


def test_platform_usage_creates_shadow_ratings_and_balanced_ledger(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner = register(client)
    monkeypatch.setattr(
        gateway,
        "resolve_platform_api_key",
        lambda _ref: "sk-platform-shadow-test",
    )
    model_id = uuid4()
    now = datetime.now(UTC)
    with app.state.test_session_factory() as db:
        db.add(
            PlatformModel(
                id=model_id,
                code="shadow-ledger-model",
                display_name="Shadow Ledger Model",
                provider_type="deepseek",
                upstream_model_name="deepseek-chat",
                base_url="https://api.deepseek.com",
                credential_ref="shadow-test",
                enabled=True,
            )
        )
        db.add(
            PlatformModelPrice(
                id=uuid4(),
                platform_model_id=model_id,
                revision=1,
                currency="CNY",
                input_per_million_minor=Decimal("200000000"),
                output_per_million_minor=Decimal("200000000"),
                effective_from=now - timedelta(minutes=1),
                effective_until=None,
            )
        )
        db.commit()

    created = client.post(
        "/personas",
        headers=jwt_headers(owner),
        json={
            "name": "影子计价测试",
            "slug": "shadow-ledger",
            "description": "测试影子账本",
            "system_prompt": "你是测试助手。",
            "model_source": "platform",
            "platform_model_id": str(model_id),
            "temperature": 0.4,
            "top_p": 0.9,
            "max_tokens": 1024,
            "enabled": True,
            "knowledge_base_ids": [],
        },
    )
    assert created.status_code == 201, created.text
    key = create_api_key(client, owner)
    FakeClient.calls = []
    FakeClient.response = FakeResponse(
        {
            "id": "chatcmpl-shadow-ledger",
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": "shadow rated"},
                    "finish_reason": "stop",
                }
            ],
            "usage": {
                "prompt_tokens": 12,
                "completion_tokens": 8,
                "total_tokens": 20,
            },
        }
    )
    monkeypatch.setattr(gateway.httpx, "Client", FakeClient)
    response = client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {key}"},
        json={
            "model": "shadow-ledger",
            "messages": [{"role": "user", "content": "计算影子账单"}],
        },
    )
    assert response.status_code == 200, response.text

    summary = client.get("/billing/usage", headers=jwt_headers(owner))
    assert summary.status_code == 200, summary.text
    assert summary.json()["shadow_rated_items"] == 2
    assert summary.json()["minor_unit_exponent"] == 2
    assert Decimal(str(summary.json()["estimated_amount_minor"])) > Decimal("3000")
    ledger_response = client.get("/billing/ledger", headers=jwt_headers(owner))
    assert ledger_response.status_code == 200, ledger_response.text
    assert len(ledger_response.json()) == 2
    assert all(
        sum(posting["amount_minor"] for posting in entry["postings"]) == 0
        for entry in ledger_response.json()
    )

    with app.state.test_session_factory() as db:
        rated = db.scalars(
            select(BillingRatedItem)
            .join(BillingUsageEvent, BillingUsageEvent.id == BillingRatedItem.usage_event_id)
            .where(
                BillingUsageEvent.tenant_id == UUID(owner["tenant"]["id"]),
                BillingRatedItem.state == "shadow",
            )
        ).all()
        assert len(rated) == 2
        assert sum(item.amount_minor_decimal for item in rated) > Decimal("3000")
        transactions = db.scalars(
            select(BillingLedgerTransaction).where(
                BillingLedgerTransaction.mode == "shadow"
            )
        ).all()
        assert len(transactions) == 2
        for transaction in transactions:
            postings = db.scalars(
                select(BillingLedgerPosting).where(
                    BillingLedgerPosting.transaction_id == transaction.id
                )
            ).all()
            assert len(postings) == 2
            assert sum(item.amount_minor for item in postings) == 0
        assert db.scalar(
            select(func.count(BillingAuditEvent.id)).where(
                BillingAuditEvent.tenant_id == UUID(owner["tenant"]["id"]),
                BillingAuditEvent.action == "reservation_overrun",
            )
        ) == 0
        reconciliation = run_billing_reconciliation(
            db,
            actor_user_id=UUID(owner["user"]["id"]),
            period_start=now - timedelta(days=1),
            period_end=now + timedelta(days=1),
        )
        assert reconciliation.status == "completed"
        assert reconciliation.report_json["unbalanced_transactions"] == []
        assert reconciliation.report_json["rated_ledger_mismatch_minor_by_currency"] == {}


def test_missing_platform_usage_keeps_token_reservation_unknown_and_never_estimates_tokens(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner = register(client)
    monkeypatch.setattr(
        gateway,
        "resolve_platform_api_key",
        lambda _ref: "sk-platform-unknown-test",
    )
    model_id = uuid4()
    now = datetime.now(UTC)
    with app.state.test_session_factory() as db:
        db.add(
            PlatformModel(
                id=model_id,
                code="unknown-usage-model",
                display_name="Unknown Usage Model",
                provider_type="deepseek",
                upstream_model_name="deepseek-chat",
                base_url="https://api.deepseek.com",
                credential_ref="unknown-test",
                enabled=True,
            )
        )
        db.add(
            PlatformModelPrice(
                id=uuid4(),
                platform_model_id=model_id,
                revision=1,
                currency="CNY",
                input_per_million_minor=Decimal("200"),
                output_per_million_minor=Decimal("2000"),
                effective_from=now - timedelta(minutes=1),
                effective_until=None,
            )
        )
        db.commit()
    created = client.post(
        "/personas",
        headers=jwt_headers(owner),
        json={
            "name": "未知用量测试",
            "slug": "unknown-usage",
            "description": "测试未知 usage",
            "system_prompt": "你是测试助手。",
            "model_source": "platform",
            "platform_model_id": str(model_id),
            "temperature": 0.4,
            "top_p": 0.9,
            "max_tokens": 256,
            "enabled": True,
            "knowledge_base_ids": [],
        },
    )
    assert created.status_code == 201, created.text
    key = create_api_key(client, owner)
    FakeClient.calls = []
    FakeClient.response = FakeResponse(
        {
            "id": "chatcmpl-no-usage",
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": "usage unavailable"},
                    "finish_reason": "stop",
                }
            ],
        }
    )
    monkeypatch.setattr(gateway.httpx, "Client", FakeClient)
    headers = {
        "Authorization": f"Bearer {key}",
        "Idempotency-Key": "unknown-usage-request",
    }
    payload = {
        "model": "unknown-usage",
        "messages": [{"role": "user", "content": "不允许猜 Token"}],
    }
    first = client.post("/v1/chat/completions", headers=headers, json=payload)
    second = client.post("/v1/chat/completions", headers=headers, json=payload)
    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    assert second.json() == first.json()
    assert len(FakeClient.calls) == 1

    with app.state.test_session_factory() as db:
        reservation = db.scalar(
            select(BillingReservation).where(
                BillingReservation.tenant_id == UUID(owner["tenant"]["id"]),
                BillingReservation.event_key == "unknown-usage-request",
            )
        )
        assert reservation is not None
        assert reservation.state == "unknown"
        items = {
            item.resource_code: item
            for item in db.scalars(
                select(BillingReservationItem).where(
                    BillingReservationItem.reservation_id == reservation.id
                )
            ).all()
        }
        assert items["platform_input_tokens"].state == "unknown"
        assert items["platform_output_tokens"].state == "unknown"
        events = db.scalars(
            select(BillingUsageEvent).where(
                BillingUsageEvent.reservation_id == reservation.id
            )
        ).all()
        assert {item.resource_code for item in events} == {
            "api_requests",
            "platform_model_requests",
        }

        settle_unknown_reservation_with_usage(
            db,
            reservation_id=reservation.id,
            actor_user_id=UUID(owner["user"]["id"]),
            prompt_tokens=5,
            completion_tokens=3,
            reason="从上游供应商账单核实了本次 Token 用量",
        )
        db.refresh(reservation)
        assert reservation.state == "settled"
        assert items["platform_input_tokens"].state == "settled"
        assert items["platform_input_tokens"].consumed_units == 5
        assert items["platform_output_tokens"].state == "settled"
        assert items["platform_output_tokens"].consumed_units == 3
        assert db.scalar(
            select(func.count(BillingRatedItem.id))
            .join(
                BillingUsageEvent,
                BillingUsageEvent.id == BillingRatedItem.usage_event_id,
            )
            .where(
                BillingUsageEvent.reservation_id == reservation.id,
                BillingRatedItem.state == "shadow",
            )
        ) == 2
        assert db.scalar(
            select(func.count(BillingAuditEvent.id)).where(
                BillingAuditEvent.tenant_id == UUID(owner["tenant"]["id"]),
                BillingAuditEvent.action == "manual_usage_settlement",
                BillingAuditEvent.subject_id == str(reservation.id),
            )
        ) == 1

def test_sender_profile_wxid_must_match_top_level_user(client: TestClient) -> None:
    response = client.post(
        "/v1/chat/completions",
        json={
            "model": "customer-support",
            "messages": [{"role": "user", "content": "你好"}],
            "user": "wxid_account_a",
            "sender_profile": {
                "wxid": "wxid_account_b",
                "nickname": "张三",
                "labels": ["意向客户"],
            },
        },
    )

    assert response.status_code == 422

