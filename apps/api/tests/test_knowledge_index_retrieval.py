from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from threading import Barrier
from uuid import uuid4

import httpx
import pytest
import test_knowledge_postgres as postgres
from app.db import session as db_session
from app.knowledge import embeddings, retrieval
from app.knowledge.observations import observe_embedding_calls
from app.models.catalog import KnowledgeBase, ModelProvider, Persona, PersonaKnowledgeBase
from app.models.identity import Tenant
from app.models.knowledge import (
    KnowledgeDocument,
    KnowledgeDocumentIndexRevision,
    KnowledgeIndexChunk,
    KnowledgeIndexMember,
    KnowledgeIndexObservation,
    KnowledgeIndexRelease,
    KnowledgeIndexState,
    PlatformEmbeddingModel,
)
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session

pg_sessions = postgres.postgres_session_factory


def setup_knowledge(db, tenant_id, model_id, label):
    kb = postgres._knowledge_base(tenant_id, label)
    kb.platform_embedding_model_id = model_id
    doc, chunk = postgres._ready_document(
        tenant_id=tenant_id,
        knowledge_base_id=kb.id,
        filename=label + ".md",
        object_key=label + ".md",
        content=label + " legacy",
    )
    db.add(kb)
    db.flush()
    db.add(doc)
    db.flush()
    db.add(chunk)
    db.commit()
    return kb, doc, chunk


def create_shadow(db, kb, doc, profile, revision):
    release = KnowledgeIndexRelease(
        id=uuid4(),
        tenant_id=kb.tenant_id,
        knowledge_base_id=kb.id,
        revision=revision,
        request_key=str(uuid4()),
        expected_generation=0,
        status="ready",
        config_json={"embedding_profile": profile},
        embedding_fingerprint=embeddings.embedding_fingerprint(profile),
    )
    db.add(release)
    db.flush()
    db.add(
        KnowledgeIndexMember(
            tenant_id=kb.tenant_id,
            knowledge_base_id=kb.id,
            release_id=release.id,
            document_id=doc.id,
            source_version=1,
            source_sha256=doc.sha256,
            status="ready",
            chunk_count=1,
        )
    )
    db.flush()
    chunk = KnowledgeIndexChunk(
        id=uuid4(),
        tenant_id=kb.tenant_id,
        knowledge_base_id=kb.id,
        release_id=release.id,
        document_id=doc.id,
        chunk_index=0,
        content="price snapshot",
        content_hash="f" * 64,
        embedding=[0.1] * 1536,
        metadata_json={"page_number": 7},
    )
    db.add(chunk)
    db.commit()
    return release, chunk


def test_release_scoped_reads_and_provider_identity(pg_sessions, monkeypatch):
    tenant_id, other_tenant_id = uuid4(), uuid4()
    model = PlatformEmbeddingModel(
        id=uuid4(),
        code="test",
        display_name="test",
        provider_type="openai_compatible",
        upstream_model_name="original",
        base_url="https://embedding.example/v1",
        credential_ref="key-1",
        dimensions=1536,
        enabled=True,
        verified_at=datetime.now(UTC),
    )
    monkeypatch.setattr(embeddings, "validate_public_service_url", lambda _url: None)
    monkeypatch.setattr(embeddings, "resolve_platform_api_key", lambda ref: "secret-" + ref)
    requests = []

    def embed(config, texts):
        requests.append((config.model_name, config.api_key))
        return embeddings.EmbeddingBatch([[0.1] * 1536], 3, True, 1)

    monkeypatch.setattr(retrieval, "embed_texts", embed)
    with pg_sessions() as db:
        for item in (tenant_id, other_tenant_id):
            db.add(Tenant(id=item, name=str(item), status="active"))
        db.add(model)
        db.commit()
        kb, doc, legacy = setup_knowledge(db, tenant_id, model.id, "price")
        profile = embeddings.freeze_embedding_profile(db, kb)
        release, shadow = create_shadow(db, kb, doc, profile, 1)
        create_shadow(db, kb, doc, profile, 2)
        for other_tenant in (tenant_id, other_tenant_id):
            unrelated, unrelated_doc, _ = setup_knowledge(db, other_tenant, model.id, str(uuid4()))
            create_shadow(db, unrelated, unrelated_doc, profile, 1)

        def search(**kwargs):
            return retrieval.retrieve_knowledge_base(
                db, tenant_id=tenant_id, knowledge_base=kb, query="price", **kwargs
            )

        assert [hit["chunk_id"] for hit in search()] == [legacy.id]
        state = KnowledgeIndexState(
            tenant_id=tenant_id,
            knowledge_base_id=kb.id,
            active_release_id=release.id,
            last_revision=2,
            generation=1,
        )
        db.add(state)
        db.commit()
        model.credential_ref = "key-2"
        db.commit()
        hits = search()
        assert [hit["chunk_id"] for hit in hits] == [shadow.id]
        assert hits[0]["page_number"] == 7
        assert hits[0]["vector_rank"] == hits[0]["lexical_rank"] == 1
        assert requests[-1] == ("original", "secret-key-2")
        assert "secret" not in str(profile)
        assert search(document_ids=[unrelated_doc.id]) == []
        assert (
            retrieval.retrieve_knowledge_base(
                db, tenant_id=other_tenant_id, knowledge_base=kb, query="price"
            )
            == []
        )

        model.upstream_model_name = "different-1536-space"
        db.commit()
        calls_before = len(requests)
        with pytest.raises(ValueError, match="index_embedding_profile_changed"):
            search()
        assert len(requests) == calls_before
        model.upstream_model_name = "original"
        doc.sha256 = "d" * 64
        db.commit()
        # An already running query pinned to the old release must exclude a changed source.
        assert not retrieval._ranked_candidates(
            db, tenant_id, kb.id, release.id, None, [0.1] * 1536, "price", 20
        )
        state.active_release_id = None
        db.commit()
        assert [hit["chunk_id"] for hit in search()] == [legacy.id]


def test_revocation_while_query_embedding_is_in_flight(pg_sessions, monkeypatch):
    tenant_id = uuid4()
    with pg_sessions() as db:
        db.add(Tenant(id=tenant_id, name="binding revocation", status="active"))
        db.commit()
        first, first_doc, _ = setup_knowledge(db, tenant_id, None, "first")
        second, _, _ = setup_knowledge(db, tenant_id, None, "second")
        persona = Persona(
            id=uuid4(),
            tenant_id=tenant_id,
            name="support",
            slug="support",
            description="",
            system_prompt="answer",
            model_source="byok",
            model_name="test",
            temperature=0.3,
            top_p=0.9,
            max_tokens=128,
            enabled=True,
        )
        db.add(persona)
        db.flush()
        for kb in [first, second]:
            db.add(
                PersonaKnowledgeBase(
                    tenant_id=tenant_id, persona_id=persona.id, knowledge_base_id=kb.id
                )
            )
        db.commit()
        config = embeddings.EmbeddingConfig("https://unit.test", "private", "openai", "model", 1536)
        monkeypatch.setattr(retrieval, "resolve_embedding_config", lambda *_: config)
        calls = []

        def revoke_after_first_kb(_config, _texts):
            calls.append(True)
            if len(calls) == 2:
                with pg_sessions.begin() as other:
                    # Remove an already retrieved KB, disable the later one before its SQL.
                    binding = other.scalar(
                        select(PersonaKnowledgeBase).where(
                            PersonaKnowledgeBase.tenant_id == tenant_id,
                            PersonaKnowledgeBase.knowledge_base_id == first.id,
                        )
                    )
                    other.delete(binding)
                    other.get(KnowledgeBase, second.id).enabled = False
            return embeddings.EmbeddingBatch([[0.1] * 1536], 3, True, 1)

        monkeypatch.setattr(retrieval, "embed_texts", revoke_after_first_kb)
        context, citations = retrieval.retrieve_persona_context(
            db, tenant_id=tenant_id, persona_id=persona.id, query="price"
        )
        assert len(calls) == 2
        assert (context, citations) == ("", [])
        assert (
            retrieval.retrieve_knowledge_base(
                db, tenant_id=tenant_id, knowledge_base=first, query="price", persona_id=persona.id
            )
            == []
        )
        # Revoking the binding does not remove the tenant's direct KB search permission.
        hits = retrieval.retrieve_knowledge_base(
            db, tenant_id=tenant_id, knowledge_base=first, query="price"
        )
        assert [hit["document_id"] for hit in hits] == [first_doc.id]


def test_snapshot_byok_resolves_only_owners_current_credential(pg_sessions, monkeypatch):
    tenant_id, foreign_id = uuid4(), uuid4()
    monkeypatch.setattr(embeddings, "validate_public_service_url", lambda _url: None)
    monkeypatch.setattr(embeddings, "decrypt_provider_api_key", lambda ciphertext: ciphertext)
    with pg_sessions() as db:
        providers = []
        for owner in (tenant_id, foreign_id):
            db.add(Tenant(id=owner, name=str(owner), status="active"))
            db.flush()
            provider = ModelProvider(
                id=uuid4(),
                tenant_id=owner,
                name="BYOK",
                provider_type="openai_compatible",
                base_url="https://provider.example/v1",
                api_key_ciphertext="original-key",
            )
            db.add(provider)
            providers.append(provider)
        db.commit()
        kb = postgres._knowledge_base(tenant_id, "BYOK")
        kb.embedding_source = "byok"
        kb.embedding_provider_id = providers[0].id
        kb.embedding_model_name = "customer-model"
        profile = embeddings.freeze_embedding_profile(db, kb)
        fingerprint = embeddings.embedding_fingerprint(profile)
        config_used = embeddings.resolve_embedding_config(db, kb)
        assert embeddings.embedding_config_fingerprint(kb, config_used) == fingerprint
        providers[0].api_key_ciphertext = "rotated-key"
        db.commit()
        config = embeddings.resolve_snapshot_embedding_config(db, kb, profile, fingerprint)
        assert (config.model_name, config.api_key) == ("customer-model", "rotated-key")
        assert "key" not in str(profile)

        # A valid checksum alone cannot authorize another tenant's provider.
        foreign_profile = {**profile, "provider_id": str(providers[1].id)}
        with pytest.raises(ValueError, match="byok_embedding_provider_unavailable"):
            embeddings.resolve_snapshot_embedding_config(
                db, kb, foreign_profile, embeddings.embedding_fingerprint(foreign_profile)
            )
        providers[0].base_url = "https://different.example/v1"
        db.commit()
        with pytest.raises(ValueError, match="index_embedding_profile_changed"):
            embeddings.resolve_snapshot_embedding_config(db, kb, profile, fingerprint)


@pytest.mark.parametrize("origin", ["generated", "legacy_unknown"])
def test_v1_reader_rejects_only_in_scope_mismatches_even_during_embedding(
    pg_sessions, monkeypatch, origin
):
    tenant_id = uuid4()
    current = embeddings.EmbeddingConfig("https://unit.test", "private", "openai", "new", 1536)
    previous = embeddings.EmbeddingConfig("https://unit.test", "private", "openai", "old", 1536)
    monkeypatch.setattr(retrieval, "resolve_embedding_config", lambda *_: current)
    calls = []
    with pg_sessions() as db:
        db.add(Tenant(id=tenant_id, name="v1 fingerprints", status="active"))
        db.commit()
        kb, doc, _ = setup_knowledge(db, tenant_id, None, "price")
        untouched, untouched_chunk = postgres._ready_document(
            tenant_id=tenant_id,
            knowledge_base_id=kb.id,
            filename="unknown.md",
            object_key="unknown.md",
            content="price unknown",
        )
        db.add(untouched)
        db.flush()
        db.add(untouched_chunk)
        db.add(
            KnowledgeDocumentIndexRevision(
                tenant_id=tenant_id,
                knowledge_base_id=kb.id,
                document_id=doc.id,
                index_version=2,
                source_sha256=doc.sha256,
                chunk_count=1,
                reader_fingerprint=embeddings.embedding_config_fingerprint(kb, previous),
                profile_origin=origin,
            )
        )
        db.commit()

        def activate_during_call(_config, _texts):
            calls.append(True)
            if len(calls) == 1:
                with pg_sessions.begin() as other:
                    other.get(KnowledgeDocument, doc.id).active_index_version = 2
            return embeddings.EmbeddingBatch([[0.1] * 1536], 1, True, 1)

        monkeypatch.setattr(retrieval, "embed_texts", activate_during_call)

        def query(document_ids=None):
            return retrieval.retrieve_knowledge_base(
                db, tenant_id=tenant_id, knowledge_base=kb, query="price", document_ids=document_ids
            )

        with pytest.raises(ValueError, match="index_embedding_profile_changed"):
            query()
        with pytest.raises(ValueError, match="index_embedding_profile_changed"):
            query([doc.id])
        assert len(calls) == 1  # Known mismatch is rejected prior to another provider call.
        assert [hit["chunk_id"] for hit in query([untouched.id])] == [untouched_chunk.id]
        with pg_sessions.begin() as other:
            other.get(KnowledgeDocument, doc.id).status = "deleting"
        assert [hit["chunk_id"] for hit in query()] == [untouched_chunk.id]


def test_batch_attempts_survive_later_failure_and_rollback(pg_sessions, monkeypatch):
    tenant_id = uuid4()
    kb = postgres._knowledge_base(tenant_id, "accounting")
    posted = []

    def send(request):
        posted.append(request)
        if len(posted) == 1:
            return httpx.Response(
                200,
                json={
                    "data": [{"index": i, "embedding": [0.1] * 1536} for i in range(64)],
                    "usage": {"prompt_tokens": 73},
                },
            )
        if len(posted) == 2:
            return httpx.Response(
                200,
                json={
                    "data": [{"index": 7, "embedding": [0.1] * 1536}],
                    "usage": {"prompt_tokens": 4},
                },
            )
        if len(posted) == 3:
            raise httpx.ReadTimeout("timeout")
        return httpx.Response(200, json={"data": [{"index": 0, "embedding": [0.1] * 1536}]})

    real_client = httpx.Client
    transport = httpx.MockTransport(send)
    monkeypatch.setattr(embeddings.httpx, "Client", lambda **kw: real_client(transport=transport))
    config = embeddings.EmbeddingConfig("https://unit.test", "private", "openai", "model", 1536)
    with pg_sessions() as db:
        db.add(Tenant(id=tenant_id, name="observation", status="active"))
        db.flush()
        db.add(kb)
        db.commit()
        with observe_embedding_calls(
            db,
            tenant_id=tenant_id,
            knowledge_base_id=kb.id,
            request_id="request-1",
            operation="query_embedding",
        ):
            with pytest.raises(RuntimeError, match="invalid_index"):
                embeddings.embed_texts(config, ["text"] * 65)
            with pytest.raises(httpx.ReadTimeout):
                embeddings.embed_texts(config, ["retry"])
            assert not embeddings.embed_texts(config, ["no usage"]).usage_known
        db.rollback()
        rows = db.scalars(select(KnowledgeIndexObservation)).all()
        assert len(posted) == len(rows) == 4
        assert {(row.request_id, row.operation, row.model_name) for row in rows} == {
            ("request-1", "query_embedding", "model")
        }
        assert {(row.status, row.input_tokens) for row in rows} == {
            ("succeeded", 73),
            ("failed", 4),
            ("unknown", None),
        }


def test_observations_do_not_starve_saturated_reader_pool(pg_sessions, monkeypatch):
    tenant_id = uuid4()
    kb = postgres._knowledge_base(tenant_id, "writer-pool")
    with pg_sessions() as db:
        db.add(Tenant(id=tenant_id, name="writer-pool", status="active"))
        db.flush()
        db.add(kb)
        db.commit()
        url = db.get_bind().url
        schema = db.scalar(text("SELECT current_schema()"))

    options = {
        "connect_args": {"options": f"-c search_path={schema},public"},
        "max_overflow": 0,
        "pool_timeout": 3,
    }
    readers = create_engine(url, pool_size=2, **options)
    writer = create_engine(url, pool_size=1, **options)
    monkeypatch.setattr(db_session, "engine", readers)
    monkeypatch.setattr(db_session, "observation_engine", writer)
    readers_busy, upstream_busy = Barrier(2, timeout=8), Barrier(2, timeout=8)

    def send(request):
        # Both external calls must be in flight despite the single writer connection.
        upstream_busy.wait()
        return httpx.Response(
            200,
            json={"data": [{"index": 0, "embedding": [0.1] * 1536}], "usage": {"prompt_tokens": 1}},
        )

    real_client = httpx.Client
    transport = httpx.MockTransport(send)
    monkeypatch.setattr(embeddings.httpx, "Client", lambda **kw: real_client(transport=transport))
    config = embeddings.EmbeddingConfig("https://unit.test", "private", "openai", "model", 1536)

    def call_provider(request_id):
        with Session(readers) as db:
            db.execute(text("SELECT 1"))
            readers_busy.wait()
            with observe_embedding_calls(
                db,
                tenant_id=tenant_id,
                knowledge_base_id=kb.id,
                request_id=request_id,
                operation="query_embedding",
            ):
                return embeddings.embed_texts(config, ["test"]).input_tokens

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            assert list(pool.map(call_provider, ["request-a", "request-b"])) == [1, 1]
        with pg_sessions() as db:
            rows = db.scalars(select(KnowledgeIndexObservation)).all()
            assert {(row.request_id, row.status) for row in rows} == {
                ("request-a", "succeeded"),
                ("request-b", "succeeded"),
            }
        assert writer.pool.checkedout() == readers.pool.checkedout() == 0
    finally:
        writer.dispose()
        readers.dispose()


@pytest.mark.parametrize("indices", [[0, 0], [0, 2], [True, 0], [None, 0]])
def test_embedding_batch_rejects_ambiguous_provider_indices(indices):
    payload = {"data": [{"index": index, "embedding": [0.1] * 1536} for index in indices]}
    with pytest.raises(RuntimeError, match="invalid_index"):
        embeddings._response_vectors(payload, 2, 1536)
