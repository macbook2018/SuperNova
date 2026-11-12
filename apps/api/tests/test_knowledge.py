from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select, func
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.db.session import get_db
from app.knowledge.chunking import chunk_blocks
from app.knowledge.parsers import parse_document, validate_document_bytes
from app.main import app
from app.models.knowledge import KnowledgeDocument, KnowledgeIngestionJob


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


def register(client: TestClient, email: str) -> dict:
    response = client.post(
        "/auth/register",
        json={
            "email": email,
            "password": "correct-horse-battery",
            "display_name": "Knowledge Tester",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def headers(tokens: dict) -> dict[str, str]:
    return {"Authorization": f"Bearer {tokens['access_token']}"}


def create_kb(client: TestClient, auth: dict) -> dict:
    response = client.post(
        "/knowledge-bases",
        headers=headers(auth),
        json={"name": "产品知识库", "description": "产品与售后资料"},
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_text_and_html_parsers_keep_clean_text_and_metadata() -> None:
    body = b"# Refund policy\n\nCustomers can return items within 30 days."
    mime = validate_document_bytes("policy.md", "text/markdown", body)
    blocks = parse_document("policy.md", mime, body)
    assert len(blocks) == 1
    assert "30 days" in blocks[0].text

    html = b"<html><head><title>Help</title><script>secret()</script></head><body><h1>FAQ</h1><p>Shipping takes 3 days.</p></body></html>"
    html_mime = validate_document_bytes("help.html", "text/html", html)
    html_blocks = parse_document("help.html", html_mime, html)
    assert "Shipping takes 3 days" in html_blocks[0].text
    assert "secret" not in html_blocks[0].text
    assert html_blocks[0].metadata["title"] == "Help"


def test_chunker_applies_overlap_and_stable_hashes() -> None:
    from app.knowledge.parsers import ExtractedBlock

    blocks = [ExtractedBlock(text=("产品退货规则。" * 120), metadata={"page_number": 2})]
    chunks = chunk_blocks(blocks, chunk_size=160, chunk_overlap=30)
    assert len(chunks) > 1
    assert all(len(item["content"]) <= 160 for item in chunks)
    assert all(item["metadata"]["page_number"] == 2 for item in chunks)
    assert all(len(item["content_hash"]) == 64 for item in chunks)
    assert chunks[0]["content"][-20:] in chunks[1]["content"]


def test_upload_creates_persistent_ingestion_job_and_is_idempotent(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("app.api.knowledge.put_object", lambda *_args, **_kwargs: None)
    auth = register(client, "knowledge-owner@example.com")
    kb = create_kb(client, auth)
    files = {"file": ("faq.txt", b"Shipping takes 3 days.", "text/plain")}

    first = client.post(
        f"/knowledge-bases/{kb['id']}/documents/upload",
        headers=headers(auth),
        files=files,
    )
    assert first.status_code == 202, first.text
    first_doc = first.json()
    assert first_doc["status"] == "queued"
    assert first_doc["file_size_bytes"] > 0

    second = client.post(
        f"/knowledge-bases/{kb['id']}/documents/upload",
        headers=headers(auth),
        files=files,
    )
    assert second.status_code == 202, second.text
    assert second.json()["id"] == first_doc["id"]

    listed = client.get(
        f"/knowledge-bases/{kb['id']}/documents",
        headers=headers(auth),
    )
    assert listed.status_code == 200
    assert [item["id"] for item in listed.json()] == [first_doc["id"]]


def test_upload_rejects_mismatched_file_signature(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("app.api.knowledge.put_object", lambda *_args, **_kwargs: None)
    auth = register(client, "knowledge-pdf@example.com")
    kb = create_kb(client, auth)
    response = client.post(
        f"/knowledge-bases/{kb['id']}/documents/upload",
        headers=headers(auth),
        files={"file": ("broken.pdf", b"this is not a PDF", "application/pdf")},
    )
    assert response.status_code == 415
    assert response.json()["detail"]["code"] == "file_signature_mismatch"


def test_url_import_is_tenant_scoped_and_deduplicated(client: TestClient) -> None:
    owner = register(client, "knowledge-url@example.com")
    other = register(client, "knowledge-other@example.com")
    kb = create_kb(client, owner)
    payload = {"url": "https://example.com/help"}

    first = client.post(
        f"/knowledge-bases/{kb['id']}/documents/import-url",
        headers=headers(owner),
        json=payload,
    )
    assert first.status_code == 202, first.text
    second = client.post(
        f"/knowledge-bases/{kb['id']}/documents/import-url",
        headers=headers(owner),
        json=payload,
    )
    assert second.status_code == 202, second.text
    assert second.json()["id"] == first.json()["id"]

    forbidden = client.get(
        f"/knowledge-bases/{kb['id']}/documents",
        headers=headers(other),
    )
    assert forbidden.status_code == 404
