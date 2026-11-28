from contextlib import contextmanager
from dataclasses import replace
from uuid import UUID, uuid4

import pytest
import test_knowledge
from app.db.session import get_db
from app.knowledge.embeddings import EmbeddingConfig
from app.knowledge.legacy_revisions import record_revisions
from app.main import app
from app.models.catalog import KnowledgeBase
from app.models.identity import TenantMembership
from app.models.knowledge import (
    KnowledgeChunk,
    KnowledgeDocument,
    KnowledgeDocumentIndexRevision,
    KnowledgeIndexBuildJob,
    KnowledgeIndexChunk,
    KnowledgeIndexMember,
    KnowledgeIndexObservation,
    KnowledgeIndexRelease,
    KnowledgeIngestionJob,
    KnowledgeUsageRecord,
)
from sqlalchemy import func, select
from test_knowledge import create_kb, headers, register

knowledge_client = test_knowledge.client


@pytest.fixture
def workspace(knowledge_client, monkeypatch):
    db_session = contextmanager(app.dependency_overrides[get_db])
    monkeypatch.setattr("app.api.knowledge.put_object", lambda *_args: None)
    monkeypatch.setattr(
        "app.knowledge.indexing.freeze_embedding_profile",
        lambda *_: {
            "source": "platform",
            "base_url": "https://internal.example.invalid",
            "credential_ref": "internal-profile-reference",
        },
    )
    auth = register(knowledge_client, "rag-index@example.com")
    kb = create_kb(knowledge_client, auth)
    return knowledge_client, headers(auth), f"/knowledge-bases/{kb['id']}", db_session


def _ready_document(client, auth, base, db_session):
    file = {"file": ("manual.txt", b"The shipping policy", "text/plain")}
    response = client.post(base + "/documents/upload", headers=auth, files=file)
    assert response.status_code == 202, response.text
    document_id = UUID(response.json()["id"])
    with db_session() as db:
        document = db.get(KnowledgeDocument, document_id)
        document.status = "ready"
        document.active_index_version = 1
        document.chunk_count = 1
        db.add(
            KnowledgeChunk(
                tenant_id=document.tenant_id,
                knowledge_base_id=document.knowledge_base_id,
                document_id=document.id,
                index_version=1,
                chunk_index=0,
                content="The shipping policy",
                content_hash=document.sha256,
                embedding=[0.1] * 1536,
            )
        )
        job = db.scalar(
            select(KnowledgeIngestionJob).where(KnowledgeIngestionJob.document_id == document_id)
        )
        job.status = "succeeded"
        db.commit()
    return document_id


def test_management_scope_and_config_validation(workspace):
    client, auth, base, db_session = workspace
    endpoint = base + "/index-versions"
    other = headers(register(client, "rag-other@example.com"))
    assert client.get(endpoint).status_code == 401
    assert client.get(endpoint, headers=other).status_code == 404
    assert client.get(endpoint, headers=auth).json() == {
        "generation": 0,
        "active_release_id": None,
        "versions": [],
    }
    body = {"request_key": "first", "expected_generation": 0}
    assert client.post(endpoint, headers=other, json=body).status_code == 404
    assert client.post(endpoint, headers=auth, json=body).status_code == 409  # empty KB
    for invalid in (
        {"request_key": "  "},
        {"config": {"embedding_dimensions": 768}},
        {"config": {"chunk_size": 100, "chunk_overlap": 101}},
    ):
        assert client.post(endpoint, headers=auth, json={**body, **invalid}).status_code == 422
    assert client.get(endpoint + "/" + str(uuid4()), headers=auth).status_code == 404
    with db_session() as db:
        owner = db.scalar(
            select(TenantMembership).where(
                TenantMembership.tenant_id
                == UUID(client.get("/auth/me", headers=auth).json()["tenant"]["id"])
            )
        )
        owner.role = "member"
        db.commit()
    assert client.get(endpoint, headers=auth).status_code == 403
    assert client.post(endpoint, headers=auth, json=body).status_code == 403


def test_build_idempotency_scope_and_invalidation(workspace):
    client, auth, base, db_session = workspace
    doc_id = _ready_document(client, auth, base, db_session)
    endpoint = base + "/index-versions"
    body = {"request_key": "first", "expected_generation": 0, "config": {"chunk_size": 500}}
    response = client.post(endpoint, headers=auth, json=body)
    assert response.status_code == 202, response.text
    candidate = response.json()
    assert candidate["status"] == "building"
    assert "internal-profile-reference" not in response.text
    assert "internal.example" not in response.text
    item = endpoint + "/" + candidate["id"]
    assert client.get(item, headers=auth).json() == candidate
    assert client.post(endpoint, headers=auth, json=body).json() == candidate
    assert (
        client.post(
            endpoint, headers=auth, json={**body, "config": {"chunk_size": 550}}
        ).status_code
        == 409
    )
    assert (
        client.post(item + "/publish", headers=auth, json={"expected_generation": 0}).status_code
        == 409
    )
    other_tokens = register(client, "rag-other@example.com")
    other = headers(other_tokens)
    other_kb = create_kb(client, other_tokens)
    other_path = f"/knowledge-bases/{other_kb['id']}/index-versions/{candidate['id']}"
    assert client.get(other_path, headers=other).status_code == 404
    assert (
        client.post(
            other_path + "/publish", headers=other, json={"expected_generation": 0}
        ).status_code
        == 404
    )
    assert client.get(item, headers=other).status_code == 404
    assert (
        client.post(item + "/publish", headers=other, json={"expected_generation": 0}).status_code
        == 404
    )
    with db_session() as db:
        for model in (KnowledgeIndexRelease, KnowledgeIndexMember, KnowledgeIndexBuildJob):
            assert db.scalar(select(func.count()).select_from(model)) == 1

    # Retrying the upload is a no-op; only a real change invalidates the candidate.
    repeat = client.post(
        base + "/documents/upload",
        headers=auth,
        files={"file": ("other-name.txt", b"The shipping policy", "text/plain")},
    )
    assert repeat.json()["id"] == str(doc_id)
    assert client.get(endpoint, headers=auth).json()["generation"] == 0
    reindex = f"{base}/documents/{doc_id}/reindex"
    assert client.post(reindex, headers=auth).status_code == 202
    assert client.post(reindex, headers=auth).status_code == 202
    assert client.post(base + "/reindex", headers=auth).json()["queued_documents"] == 0
    result = client.get(endpoint, headers=auth).json()
    assert result["generation"] == 1
    assert result["active_release_id"] is None
    assert result["versions"][0]["status"] == "failed"
    assert (
        client.post(
            endpoint + "/rollback", headers=auth, json={"expected_generation": 1}
        ).status_code
        == 422
    )
    with db_session() as db:
        assert (
            db.scalar(
                select(func.count())
                .select_from(KnowledgeIngestionJob)
                .where(KnowledgeIngestionJob.job_type == "reindex")
            )
            == 1
        )


def test_legacy_retry_and_delete_are_idempotent(workspace):
    client, auth, base, db_session = workspace
    file = {"file": ("manual.txt", b"The shipping policy", "text/plain")}
    response = client.post(base + "/documents/upload", headers=auth, files=file)
    document_id = UUID(response.json()["id"])
    with db_session() as db:
        document = db.get(KnowledgeDocument, document_id)
        document.status = "failed"
        db.scalar(
            select(KnowledgeIngestionJob).where(KnowledgeIngestionJob.document_id == document_id)
        ).status = "dead_letter"
        db.commit()
    target = f"{base}/documents/{document_id}"
    for _ in range(2):
        assert client.post(target + "/retry", headers=auth).status_code == 202
    assert client.post(target + "/reindex", headers=auth).status_code == 202
    for _ in range(2):
        assert client.delete(target, headers=auth).status_code == 202
    with db_session() as db:
        jobs = db.scalars(
            select(KnowledgeIngestionJob).where(KnowledgeIngestionJob.document_id == document_id)
        ).all()
        assert sorted((j.job_type, j.status) for j in jobs) == [
            ("delete", "queued"),
            ("ingest", "cancelled"),
            ("ingest", "dead_letter"),
        ]


def test_usage_follows_published_manifest_and_separates_physical_attempts(workspace, monkeypatch):
    client, auth, base, db_session = workspace
    doc_id = _ready_document(client, auth, base, db_session)
    versions = base + "/index-versions"
    response = client.post(
        versions,
        headers=auth,
        json={
            "request_key": "metrics",
            "expected_generation": 0,
        },
    )
    assert response.status_code == 202, response.text
    release_id = UUID(response.json()["id"])
    with db_session() as db:
        release = db.get(KnowledgeIndexRelease, release_id)
        release.status = "ready"
        scope = dict(tenant_id=release.tenant_id, knowledge_base_id=release.knowledge_base_id)
        member = db.get(
            KnowledgeIndexMember, (release.tenant_id, release.knowledge_base_id, release_id, doc_id)
        )
        member.chunk_count = 3
        member.status = "ready"
        for operation, requests, tokens in (
            ("ingest", 1, 5),
            ("retrieval", 1, 4),
            ("future", 9, 99),
        ):
            db.add(
                KnowledgeUsageRecord(
                    **scope,
                    operation=operation,
                    input_tokens=tokens,
                    request_count=requests,
                    status="succeeded",
                )
            )
        for operation, status, tokens in (
            ("query_embedding", "succeeded", 4),
            ("shadow_embedding", "succeeded", 15),
            ("shadow_embedding", "unknown", None),
        ):
            db.add(
                KnowledgeIndexObservation(
                    **scope, operation=operation, status=status, input_tokens=tokens
                )
            )
        db.commit()

    usage = client.get(base + "/usage", headers=auth).json()
    assert usage["active_release_id"] is None
    assert usage["active_chunks"] == usage["legacy_active_chunks"] == 1
    assert usage["embedding_requests"] == 2
    assert usage["embedding_tokens"] == 9
    assert usage["retrieval_requests"] == 1
    assert [
        (r["operation"], r["status"], r["calls"], r["known_input_tokens"], r["unknown_calls"])
        for r in usage["operation_usage"]
    ] == [
        ("query_embedding", "succeeded", 1, 4, 0),
        ("shadow_embedding", "succeeded", 1, 15, 0),
        ("shadow_embedding", "unknown", 1, 0, 1),
    ]
    monkeypatch.setattr("app.knowledge.indexing.resolve_snapshot_embedding_config", lambda *_: None)
    published = client.post(
        f"{versions}/{release_id}/publish", headers=auth, json={"expected_generation": 0}
    )
    assert published.status_code == 200, published.text
    active = client.get(base + "/usage", headers=auth).json()
    assert active["active_release_id"] == str(release_id)
    assert active["active_chunks"] == 3
    assert active["legacy_active_chunks"] == 1
    restored = client.post(
        versions + "/rollback",
        headers=auth,
        json={"expected_generation": 1, "target_release_id": None},
    )
    assert restored.status_code == 200, restored.text
    assert client.get(base + "/usage", headers=auth).json()["active_chunks"] == 1


def test_document_history_restore_scopes_metadata_and_rejects_stale_profiles(
    workspace, monkeypatch
):
    client, auth, base, db_session = workspace
    doc_id = _ready_document(client, auth, base, db_session)
    history = f"{base}/documents/{doc_id}/index-history"
    config = EmbeddingConfig(
        base_url="https://example.invalid/v1",
        provider_type="openai_compatible",
        api_key="private-test-key",
        model_name="embed-A",
        dimensions=1536,
    )
    with db_session() as db:
        doc = db.get(KnowledgeDocument, doc_id)
        kb = db.get(KnowledgeBase, doc.knowledge_base_id)
        record_revisions(db, doc, kb, config, new_version=2, new_count=1)
        db.add(
            KnowledgeChunk(
                tenant_id=doc.tenant_id,
                knowledge_base_id=kb.id,
                document_id=doc.id,
                index_version=2,
                chunk_index=0,
                content="new projection",
                content_hash=doc.sha256,
                embedding=[0.1] * 1536,
            )
        )
        doc.active_index_version = doc.pending_index_version = 2
        db.commit()

    other = headers(register(client, "index-outsider@example.com"))
    assert client.get(history, headers=other).status_code == 404
    assert client.get(history + "?limit=101", headers=auth).status_code == 422
    first_page = client.get(history + "?limit=1", headers=auth).json()
    second_page = client.get(history + "?limit=1&offset=1", headers=auth).json()
    assert first_page["active_index_version"] == 2
    assert first_page["generation"] == 0
    assert [r["version"] for r in first_page["revisions"]] == [2]
    assert [r["version"] for r in second_page["revisions"]] == [1]
    assert set(second_page["revisions"][0]) == {
        "version",
        "created_at",
        "chunk_count",
        "profile_origin",
    }
    assert second_page["revisions"][0]["profile_origin"] == "legacy_unknown"

    target = history + "/1/restore"
    preconditions = {"expected_generation": 0, "expected_active_index_version": 2}
    assert client.post(target, headers=other, json=preconditions).status_code == 404
    assert (
        client.post(history + "/999/restore", headers=auth, json=preconditions).status_code == 404
    )
    assert client.post(target, headers=auth, json={"expected_generation": 0}).status_code == 422
    resolver = "app.knowledge.legacy_revisions.resolve_embedding_config"
    monkeypatch.setattr(resolver, lambda *_: replace(config, model_name="different-space"))
    rejected = client.post(target, headers=auth, json=preconditions)
    assert rejected.status_code == 409
    assert rejected.json()["detail"]["code"] == "index_embedding_profile_changed"
    monkeypatch.setattr(resolver, lambda *_: config)
    restored = client.post(target, headers=auth, json=preconditions)
    assert restored.status_code == 200, restored.text
    assert restored.json() == {"active_index_version": 1, "generation": 1}
    returned = client.post(
        history + "/2/restore",
        headers=auth,
        json={"expected_active_index_version": 1, "expected_generation": 1},
    )
    assert returned.json() == {"active_index_version": 2, "generation": 2}
    # Same document version again, but an old request cannot overwrite the new selection.
    assert client.post(target, headers=auth, json=preconditions).status_code == 409
    assert (
        client.post(
            target,
            headers=auth,
            json={"expected_active_index_version": 2, "expected_generation": 2},
        ).status_code
        == 200
    )
    assert client.post(f"{base}/documents/{doc_id}/reindex", headers=auth).status_code == 202
    with db_session() as db:
        doc = db.get(KnowledgeDocument, doc_id)
        assert doc.pending_index_version == 3


@pytest.mark.parametrize("allocation", ["chunk", "revision", "job"])
def test_legacy_rebuild_recovers_high_water_from_retained_history(workspace, allocation):
    client, auth, base, db_session = workspace
    doc_id = _ready_document(client, auth, base, db_session)
    with db_session() as db:
        doc = db.get(KnowledgeDocument, doc_id)
        scope = dict(
            tenant_id=doc.tenant_id,
            knowledge_base_id=doc.knowledge_base_id,
            document_id=doc.id,
            index_version=7,
        )
        if allocation == "chunk":
            retained = KnowledgeChunk(
                **scope,
                chunk_index=0,
                content="retained text",
                content_hash=doc.sha256,
                embedding=[0.1] * 1536,
            )
        elif allocation == "revision":
            retained = KnowledgeDocumentIndexRevision(
                **scope,
                source_sha256=doc.sha256,
                chunk_count=1,
                reader_fingerprint="a" * 64,
                profile_origin="legacy_unknown",
            )
        else:
            retained = KnowledgeIngestionJob(
                **scope,
                job_type="reindex",
                status="dead_letter",
                dedupe_key=f"historical:{doc.id}:7",
            )
        db.add(retained)
        # Older applications may reset both document fields while historical rows survive.
        doc.pending_index_version = doc.active_index_version = 1
        db.commit()
    result = client.post(f"{base}/documents/{doc_id}/reindex", headers=auth)
    assert result.status_code == 202, result.text
    with db_session() as db:
        assert db.get(KnowledgeDocument, doc_id).pending_index_version == 8


def test_chunk_preview_pages_only_the_active_document_and_binds_version(workspace):
    client, auth, base, db_session = workspace
    doc_id = _ready_document(client, auth, base, db_session)
    url = f"{base}/documents/{doc_id}/chunks"
    with db_session() as db:
        doc = db.get(KnowledgeDocument, doc_id)
        for version, index, text in ((1, 1, "Second page"), (2, 0, "Rebuilt document")):
            db.add(KnowledgeChunk(
                tenant_id=doc.tenant_id, knowledge_base_id=doc.knowledge_base_id,
                document_id=doc_id, index_version=version, chunk_index=index,
                content=text, content_hash=doc.sha256, embedding=[0.1] * 1536,
                metadata_json={"page_number": 4, "internal_key": "never-in-response"},
            ))
        db.commit()
    first = client.get(url, headers=auth, params={"limit": 1}).json()
    assert first["has_more"] and first["release_revision"] is None
    assert first["items"][0]["content"] == "The shipping policy"
    assert first["items"][0]["locations"] == []
    params = {"limit": 1, "offset": 1, "snapshot": first["snapshot"]}
    second = client.get(url, headers=auth, params=params).json()
    assert not second["has_more"]
    item = second["items"][0]
    assert item["locations"] == [{"page_number": 4}] and item["content"] == "Second page"
    assert set(item) == {"id", "content", "chunk_index", "heading_path", "locations"}
    assert client.get(url, headers=auth, params={**params, "offset": 2}).json()["items"] == []
    assert client.get(url, headers=auth, params={"offset": 1}).status_code == 422
    assert client.get(url, headers=auth, params={"limit": 51}).status_code == 422
    with db_session() as db:
        db.get(KnowledgeDocument, doc_id).active_index_version = 2
        db.commit()
    change = client.get(url, headers=auth, params=params)
    assert change.status_code == 409
    assert change.json()["detail"]["code"] == "preview_version_changed"
    assert client.get(url, headers=auth).json()["items"][0]["content"] == "Rebuilt document"


def test_chunk_preview_honours_scope_lifecycle_and_published_manifest(workspace, monkeypatch):
    client, auth, base, db_session = workspace
    doc_id = _ready_document(client, auth, base, db_session)
    url = f"{base}/documents/{doc_id}/chunks"
    other = register(client, "outsider-preview@example.com")
    other_base = create_kb(client, other)["id"]
    assert client.get(url).status_code == 401
    assert client.get(url, headers=headers(other)).status_code == 404
    assert client.get(
        f"/knowledge-bases/{other_base}/documents/{doc_id}/chunks", headers=headers(other)
    ).status_code == 404
    sibling = client.post("/knowledge-bases", headers=auth, json={"name": "Sibling KB"})
    assert sibling.status_code == 201
    wrong_kb = f"/knowledge-bases/{sibling.json()['id']}/documents/{doc_id}/chunks"
    assert client.get(wrong_kb, headers=auth).status_code == 404
    versions = base + "/index-versions"
    release_id = UUID(client.post(versions, headers=auth, json={
        "request_key": "preview", "expected_generation": 0,
    }).json()["id"])
    with db_session() as db:
        release = db.get(KnowledgeIndexRelease, release_id)
        release.status = "ready"
        member_key = (release.tenant_id, release.knowledge_base_id, release_id, doc_id)
        member = db.get(KnowledgeIndexMember, member_key)
        member.status, member.chunk_count = "ready", 1
        db.add(KnowledgeIndexChunk(
            tenant_id=release.tenant_id, knowledge_base_id=release.knowledge_base_id,
            release_id=release_id, document_id=doc_id, chunk_index=0, content="原文",
            content_hash="a" * 64, embedding=[0.1] * 1536, metadata_json={
                "heading_path": ["政策"], "source_spans": [{"line_start": 4, "line_end": 5}],
                "index_prefix": "仅供检索",
            },
        ))
        db.commit()
    legacy = client.get(url, headers=auth).json()
    assert legacy["items"][0]["content"] == "The shipping policy"
    monkeypatch.setattr("app.knowledge.indexing.resolve_snapshot_embedding_config", lambda *_: None)
    assert client.post(f"{versions}/{release_id}/publish", headers=auth,
                       json={"expected_generation": 0}).status_code == 200
    assert client.get(url, headers=auth, params={"snapshot": legacy["snapshot"]}).status_code == 409
    page = client.get(url, headers=auth).json()
    assert page["active_release_id"] == str(release_id) and page["release_revision"] == 1
    assert page["items"][0]["content"] == "原文"
    assert page["items"][0]["heading_path"] == ["政策"]
    assert page["items"][0]["locations"] == [{"line_start": 4, "line_end": 5}]
    with db_session() as db:
        db.get(KnowledgeIndexMember, member_key).source_sha256 = "0" * 64
        db.commit()
    assert client.get(url, headers=auth).status_code == 409
    with db_session() as db:
        db.get(KnowledgeDocument, doc_id).status = "deleting"
        db.commit()
    assert client.get(url, headers=auth).status_code == 404
