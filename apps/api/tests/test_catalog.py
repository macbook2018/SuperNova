from collections.abc import Generator

import pytest
from app.db.base import Base
from app.db.session import get_db
from app.main import app
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool


@pytest.fixture
def client() -> Generator[TestClient, None, None]:
    engine = create_engine(
        "sqlite+pysqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
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
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()
    Base.metadata.drop_all(engine)
    engine.dispose()


def register(client: TestClient, email: str = "owner@example.com") -> dict:
    response = client.post(
        "/auth/register",
        json={
            "email": email,
            "password": "correct-horse-battery",
            "display_name": "Workspace Owner",
        },
    )
    assert response.status_code == 201
    return response.json()


def headers(tokens: dict) -> dict[str, str]:
    return {"Authorization": f"Bearer {tokens['access_token']}"}


def create_provider(client: TestClient, auth: dict, name: str = "DeepSeek") -> dict:
    response = client.post(
        "/model-providers",
        headers=headers(auth),
        json={
            "name": name,
            "provider_type": "deepseek",
            "base_url": "https://api.deepseek.com",
            "api_key": "sk-provider-secret-1234",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def create_knowledge_base(client: TestClient, auth: dict, name: str = "产品资料库") -> dict:
    response = client.post(
        "/knowledge-bases",
        headers=headers(auth),
        json={"name": name, "description": "产品信息与常见问题"},
    )
    assert response.status_code == 201, response.text
    return response.json()


def create_persona(
    client: TestClient,
    auth: dict,
    provider_id: str | None,
    knowledge_base_ids: list[str] | None = None,
    slug: str = "customer-support",
) -> dict:
    response = client.post(
        "/personas",
        headers=headers(auth),
        json={
            "name": "智能客服",
            "slug": slug,
            "description": "产品咨询与售后支持",
            "system_prompt": "你是一名专业、耐心的在线客服。",
            "provider_id": provider_id,
            "model_name": "deepseek-chat",
            "temperature": 0.4,
            "top_p": 0.9,
            "max_tokens": 2048,
            "enabled": True,
            "knowledge_base_ids": knowledge_base_ids or [],
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_model_provider_secret_is_encrypted_and_never_returned(client: TestClient) -> None:
    auth = register(client)
    created = create_provider(client, auth)
    assert created["name"] == "DeepSeek"
    assert created["has_api_key"] is True
    assert created["api_key_hint"] == "••••1234"
    assert "api_key" not in created
    assert "ciphertext" not in created
    assert "sk-provider-secret-1234" not in str(created)

    listed = client.get("/model-providers", headers=headers(auth))
    assert listed.status_code == 200
    assert len(listed.json()) == 1
    assert "sk-provider-secret-1234" not in listed.text


def test_model_provider_update_secret_and_prevent_delete_when_in_use(client: TestClient) -> None:
    auth = register(client)
    provider = create_provider(client, auth)
    updated = client.patch(
        f"/model-providers/{provider['id']}",
        headers=headers(auth),
        json={"name": "DeepSeek Production", "api_key": "sk-new-secret-5678"},
    )
    assert updated.status_code == 200
    assert updated.json()["name"] == "DeepSeek Production"
    assert updated.json()["api_key_hint"] == "••••5678"
    assert "sk-new-secret-5678" not in updated.text

    create_persona(client, auth, provider["id"])
    deletion = client.delete(
        f"/model-providers/{provider['id']}",
        headers=headers(auth),
    )
    assert deletion.status_code == 409


def test_model_provider_clear_key_and_duplicate_name(client: TestClient) -> None:
    auth = register(client)
    provider = create_provider(client, auth)
    duplicate = client.post(
        "/model-providers",
        headers=headers(auth),
        json={
            "name": "DeepSeek",
            "provider_type": "deepseek",
            "base_url": "https://api.deepseek.com",
            "api_key": "sk-another-secret",
        },
    )
    assert duplicate.status_code == 409

    cleared = client.patch(
        f"/model-providers/{provider['id']}",
        headers=headers(auth),
        json={"clear_api_key": True},
    )
    assert cleared.status_code == 200
    assert cleared.json()["has_api_key"] is False
    assert cleared.json()["api_key_hint"] is None


def test_persona_create_update_and_knowledge_base_association(client: TestClient) -> None:
    auth = register(client)
    provider = create_provider(client, auth)
    kb_a = create_knowledge_base(client, auth, "产品资料库")
    kb_b = create_knowledge_base(client, auth, "售后知识库")

    persona = create_persona(
        client,
        auth,
        provider["id"],
        [kb_a["id"], kb_b["id"]],
    )
    assert persona["slug"] == "customer-support"
    assert persona["provider_id"] == provider["id"]
    assert persona["provider_name"] == "DeepSeek"
    assert set(persona["knowledge_base_ids"]) == {kb_a["id"], kb_b["id"]}
    assert persona["temperature"] == 0.4

    updated = client.patch(
        f"/personas/{persona['id']}",
        headers=headers(auth),
        json={
            "name": "高级客服",
            "temperature": 0.2,
            "knowledge_base_ids": [kb_b["id"]],
        },
    )
    assert updated.status_code == 200
    assert updated.json()["name"] == "高级客服"
    assert updated.json()["temperature"] == 0.2
    assert updated.json()["knowledge_base_ids"] == [kb_b["id"]]


def test_persona_slug_is_unique_within_workspace(client: TestClient) -> None:
    auth = register(client)
    create_persona(client, auth, None)
    duplicate = client.post(
        "/personas",
        headers=headers(auth),
        json={
            "name": "另一个客服",
            "slug": "customer-support",
            "system_prompt": "你是另一个客服。",
            "model_name": "qwen-plus",
        },
    )
    assert duplicate.status_code == 409


def test_catalog_resources_are_isolated_between_tenants(client: TestClient) -> None:
    owner_a = register(client, "a@example.com")
    owner_b = register(client, "b@example.com")
    provider_a = create_provider(client, owner_a)
    kb_a = create_knowledge_base(client, owner_a)

    assert client.get("/model-providers", headers=headers(owner_b)).json() == []
    assert client.get("/knowledge-bases", headers=headers(owner_b)).json() == []
    assert client.get("/personas", headers=headers(owner_b)).json() == []

    cross_tenant_persona = client.post(
        "/personas",
        headers=headers(owner_b),
        json={
            "name": "非法引用",
            "slug": "cross-tenant",
            "system_prompt": "不能引用其他租户的供应商。",
            "provider_id": provider_a["id"],
            "model_name": "deepseek-chat",
        },
    )
    assert cross_tenant_persona.status_code == 404

    cross_tenant_kb = client.post(
        "/personas",
        headers=headers(owner_b),
        json={
            "name": "非法知识库关联",
            "slug": "cross-tenant-kb",
            "system_prompt": "不能引用其他租户的知识库。",
            "model_name": "deepseek-chat",
            "knowledge_base_ids": [kb_a["id"]],
        },
    )
    assert cross_tenant_kb.status_code == 404

    cross_tenant_delete = client.delete(
        f"/model-providers/{provider_a['id']}",
        headers=headers(owner_b),
    )
    assert cross_tenant_delete.status_code == 404


def test_persona_validation_and_enable_toggle(client: TestClient) -> None:
    auth = register(client)
    invalid_slug = client.post(
        "/personas",
        headers=headers(auth),
        json={
            "name": "Bad Slug",
            "slug": "Bad_Slug",
            "system_prompt": "prompt",
            "model_name": "deepseek-chat",
        },
    )
    assert invalid_slug.status_code == 422

    persona = create_persona(client, auth, None)
    disabled = client.patch(
        f"/personas/{persona['id']}",
        headers=headers(auth),
        json={"enabled": False},
    )
    assert disabled.status_code == 200
    assert disabled.json()["enabled"] is False

    removed = client.delete(f"/personas/{persona['id']}", headers=headers(auth))
    assert removed.status_code == 204
    assert client.get("/personas", headers=headers(auth)).json() == []


def test_knowledge_base_delete_removes_associations(client: TestClient) -> None:
    auth = register(client)
    kb = create_knowledge_base(client, auth)
    persona = create_persona(client, auth, None, [kb["id"]])

    removed = client.delete(f"/knowledge-bases/{kb['id']}", headers=headers(auth))
    assert removed.status_code == 204
    refreshed = client.get(f"/personas/{persona['id']}", headers=headers(auth))
    assert refreshed.status_code == 200
    assert refreshed.json()["knowledge_base_ids"] == []
