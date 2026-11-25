import os
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from queue import Queue
from types import SimpleNamespace
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from app.api.catalog import delete_knowledge_base
from app.core.config import get_settings
from app.knowledge import indexing
from fastapi import HTTPException
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session

BASE = "0014_audio_transcription"
V2 = "0015_rag_index_releases"


@pytest.fixture
def migrated_schema(monkeypatch):
    url = os.getenv("SUPERNOVA_DATABASE_URL", "")
    if not url.startswith("postgresql"):
        pytest.skip("PostgreSQL migration test requires SUPERNOVA_DATABASE_URL")
    schema = f"sn_ki_migration_{uuid4().hex}"
    admin = create_engine(url)
    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    with admin.begin() as conn:
        for extension in ("vector", "pg_trgm"):
            conn.exec_driver_sql(f"CREATE EXTENSION IF NOT EXISTS {extension} WITH SCHEMA public")
        conn.exec_driver_sql(f'CREATE SCHEMA "{schema}"')
        # Shadow an existing public.alembic_version even when testing on a migrated database.
        conn.exec_driver_sql(
            f'CREATE TABLE "{schema}".alembic_version (version_num varchar(32) PRIMARY KEY)'
        )
    scoped_url = make_url(url).update_query_dict({"options": f"-c search_path={schema},public"})
    monkeypatch.setattr(
        get_settings(), "database_url", scoped_url.render_as_string(hide_password=False)
    )
    scoped = create_engine(scoped_url)
    try:
        command.upgrade(config, BASE)
        yield scoped, config
    finally:
        scoped.dispose()
        with admin.begin() as conn:
            conn.exec_driver_sql(f'DROP SCHEMA "{schema}" CASCADE')
        admin.dispose()


def test_release_migration_preserves_legacy_vectors_and_guards_downgrade(migrated_schema):
    engine, config = migrated_schema
    tenant, other_tenant, kb, other_kb, foreign_kb, doc, chunk, release = [
        uuid4() for _ in range(8)
    ]
    params = dict(
        tenant=tenant,
        other_tenant=other_tenant,
        kb=kb,
        other_kb=other_kb,
        foreign_kb=foreign_kb,
        doc=doc,
        chunk=chunk,
        release=release,
        embedding="[" + ",".join(["0.25"] * 1536) + "]",
    )

    with engine.begin() as conn:
        conn.execute(
            text("INSERT INTO tenants (id,name) VALUES (:tenant,'A'),(:other_tenant,'B')"), params
        )
        conn.execute(
            text("""INSERT INTO knowledge_bases (id,tenant_id,name) VALUES
                     (:kb,:tenant,'kb'),(:other_kb,:tenant,'other'),(:foreign_kb,:other_tenant,'foreign')"""),
            params,
        )
        conn.execute(
            text("""INSERT INTO knowledge_documents
                     (id,tenant_id,knowledge_base_id,original_filename,source_type,object_key,
                      mime_type,file_size_bytes,status,active_index_version,chunk_count)
                     VALUES (:doc,:tenant,:kb,'doc.txt','upload','abc','text/plain',
                             3,'ready',7,1)"""),
            params,
        )
        conn.execute(
            text("""INSERT INTO knowledge_chunks
                     (id,tenant_id,knowledge_base_id,document_id,index_version,chunk_index,
                      content,content_hash,embedding,metadata_json) VALUES
                     (:chunk,:tenant,:kb,:doc,7,0,'abc','abc',CAST(:embedding AS vector),'{}')"""),
            params,
        )

    def legacy(conn):
        return conn.execute(
            text("""SELECT c.id,d.active_index_version,c.content_hash,
            c.embedding::text, vector_dims(c.embedding) FROM knowledge_chunks c
            JOIN knowledge_documents d ON c.document_id=d.id WHERE c.id=:chunk"""),
            params,
        ).one()

    with engine.connect() as conn:
        snapshot = legacy(conn)
        assert snapshot[-1] == 1536
    command.upgrade(config, V2)
    with engine.connect() as conn:
        assert legacy(conn) == snapshot
        assert conn.scalar(text("SELECT count(*) FROM knowledge_index_states")) == 0
        assert conn.scalar(text("SELECT version_num FROM alembic_version")) == V2
    command.downgrade(config, BASE)
    with engine.connect() as conn:
        assert legacy(conn) == snapshot
    command.upgrade(config, V2)

    with engine.begin() as conn:
        conn.execute(
            text("""INSERT INTO knowledge_index_releases
            (id,tenant_id,knowledge_base_id,revision,expected_generation,request_key,
             config_json,embedding_fingerprint) VALUES
            (:release,:tenant,:kb,1,0,'request','{}','fingerprint')"""),
            params,
        )
        # Both a foreign tenant and a second KB of the same tenant violate the scoped pointer.
        for scope_tenant, scope_kb in ((tenant, other_kb), (other_tenant, foreign_kb)):
            with pytest.raises(IntegrityError), conn.begin_nested():
                conn.execute(
                    text("""INSERT INTO knowledge_index_states
                    (tenant_id,knowledge_base_id,active_release_id)
                    VALUES (:t,:k,:release)"""),
                    dict(t=scope_tenant, k=scope_kb, release=release),
                )
        alien_doc = uuid4()
        conn.execute(
            text("""INSERT INTO knowledge_documents
                (id,tenant_id,knowledge_base_id,original_filename,source_type,object_key,
                 mime_type,file_size_bytes) VALUES
                (:id,:tenant,:other_kb,'alien.txt','upload','alien','text/plain',1)"""),
            {**params, "id": alien_doc},
        )
        with pytest.raises(IntegrityError), conn.begin_nested():
            conn.execute(
                text("""INSERT INTO knowledge_index_members
                    (tenant_id,knowledge_base_id,release_id,document_id,source_version)
                    VALUES (:tenant,:kb,:release,:id,1)"""),
                {**params, "id": alien_doc},
            )
        with pytest.raises(DBAPIError, match="immutable"), conn.begin_nested():
            conn.execute(
                text("UPDATE knowledge_index_releases SET config_json = '{\"mutated\": 1}'")
            )
    with pytest.raises(RuntimeError, match="preserve it"):
        command.downgrade(config, BASE)
    with engine.connect() as conn:
        assert legacy(conn) == snapshot
        assert conn.scalar(text("SELECT count(*) FROM knowledge_index_releases")) == 1
        assert conn.scalar(text("SELECT version_num FROM alembic_version")) == V2


@pytest.fixture
def ready_release(migrated_schema):
    engine, config = migrated_schema
    command.upgrade(config, V2)
    tenant, kb, release, doc, chunk, shadow, added = [uuid4() for _ in range(7)]
    params = dict(
        tenant=tenant,
        kb=kb,
        release=release,
        doc=doc,
        chunk=chunk,
        shadow=shadow,
        added=added,
        vec="[" + ",".join(["0.25"] * 1536) + "]",
    )
    with engine.begin() as conn:

        def execute(statement):
            return conn.execute(text(statement), params)

        execute("INSERT INTO tenants (id,name) VALUES (:tenant,'tenant')")
        execute("INSERT INTO knowledge_bases (id,tenant_id,name) VALUES (:kb,:tenant,'kb')")
        execute("""INSERT INTO knowledge_documents
            (id,tenant_id,knowledge_base_id,original_filename,source_type,object_key,mime_type,
             file_size_bytes,status,active_index_version,chunk_count,sha256)
            VALUES (:doc,:tenant,:kb,'d','upload','first','text/plain',10,'ready',1,1,'source')""")
        execute("""INSERT INTO knowledge_chunks
            (id,tenant_id,knowledge_base_id,document_id,index_version,chunk_index,content,
             content_hash,embedding,metadata_json)
            VALUES (:chunk,:tenant,:kb,:doc,1,0,'content','hash',CAST(:vec AS vector),'{}')""")
        execute("""INSERT INTO knowledge_document_index_revisions
            (tenant_id,knowledge_base_id,document_id,index_version,source_sha256,
             chunk_count,reader_fingerprint,profile_origin)
            VALUES (:tenant,:kb,:doc,1,'source',1,'model','legacy_unknown')""")
        execute("""INSERT INTO knowledge_index_releases
            (id,tenant_id,knowledge_base_id,revision,expected_generation,request_key,
             config_json,embedding_fingerprint,status)
            VALUES (:release,:tenant,:kb,1,0,'request',
                    '{"embedding_profile": {}}','model','ready')""")
        execute("""INSERT INTO knowledge_index_members
            (tenant_id,knowledge_base_id,release_id,document_id,source_version,status,chunk_count,source_sha256)
            VALUES (:tenant,:kb,:release,:doc,1,'ready',1,'source')""")
        execute("""INSERT INTO knowledge_index_chunks
            (id,tenant_id,knowledge_base_id,release_id,document_id,chunk_index,content,
             content_hash,embedding,metadata_json)
            VALUES (:shadow,:tenant,:kb,:release,:doc,0,'shadow','h',CAST(:vec AS vector),'{}')""")
        execute("""INSERT INTO knowledge_index_states
            (tenant_id,knowledge_base_id,active_release_id,last_revision,generation)
            VALUES (:tenant,:kb,NULL,1,0)""")

    return engine, params


def test_old_writer_disables_published_reader_and_purges_deleted_content(ready_release):
    engine, params = ready_release
    with engine.begin() as conn:

        def execute(statement):
            return conn.execute(text(statement), params)

        execute("UPDATE knowledge_index_states SET active_release_id=:release, generation=1")
        # Old readers can accept new uploads without referencing any A1 model.
        execute("""INSERT INTO knowledge_documents
            (id,tenant_id,knowledge_base_id,original_filename,source_type,object_key,mime_type,
             file_size_bytes) VALUES (:added,:tenant,:kb,'new','upload','new','text/plain',2)""")
        state_sql = "SELECT active_release_id,generation FROM knowledge_index_states"
        assert execute(state_sql).one() == (None, 2)
        execute("UPDATE knowledge_index_states SET active_release_id=:release")
        execute("UPDATE knowledge_chunks SET content='replaced in place' WHERE id=:chunk")
        assert execute(state_sql).one() == (None, 3)
        assert execute("SELECT count(*) FROM knowledge_document_index_revisions").scalar_one() == 0
        execute("UPDATE knowledge_index_states SET active_release_id=:release")
        execute("UPDATE knowledge_documents SET status='deleting' WHERE id=:doc")
        execute("UPDATE knowledge_documents SET status='deleted' WHERE id=:doc")
        assert execute(state_sql).one() == (None, 4)
        assert execute("SELECT count(*) FROM knowledge_index_chunks").scalar_one() == 0


def test_delete_kb_rechecks_documents_after_concurrent_upload(migrated_schema):
    engine, config = migrated_schema
    command.upgrade(config, V2)
    tenant, kb, doc = [uuid4() for _ in range(3)]
    principal = SimpleNamespace(tenant=SimpleNamespace(id=tenant))
    pid_queue = Queue()

    def delete():
        with Session(engine) as session:
            pid_queue.put(session.scalar(text("SELECT pg_backend_pid()")))
            with pytest.raises(HTTPException) as caught:
                delete_knowledge_base(kb, principal, session)
            assert caught.value.detail["code"] == "knowledge_base_has_documents"

    with engine.begin() as setup:
        setup.execute(
            text("INSERT INTO tenants (id,name) VALUES (:tenant,'A')"), {"tenant": tenant}
        )
        setup.execute(
            text("INSERT INTO knowledge_bases (id,tenant_id,name) VALUES (:kb,:t,'K')"),
            {"kb": kb, "t": tenant},
        )
    with ThreadPoolExecutor(max_workers=1) as pool:
        with engine.begin() as upload:
            blocker = upload.scalar(text("SELECT pg_backend_pid()"))
            upload.execute(
                text("SELECT id FROM knowledge_bases WHERE id=:kb FOR UPDATE"), {"kb": kb}
            )
            deletion = pool.submit(delete)
            pid = pid_queue.get(timeout=5)
            _wait_for_lock(upload, blocker, pid)
            upload.execute(
                text("""INSERT INTO knowledge_documents
                (id,tenant_id,knowledge_base_id,original_filename,source_type,object_key,mime_type,
                 file_size_bytes) VALUES (:doc,:t,:kb,'d','upload','object','text/plain',1)"""),
                {"doc": doc, "t": tenant, "kb": kb},
            )
        deletion.result(timeout=5)
    with engine.connect() as conn:
        assert (
            conn.scalar(text("SELECT id FROM knowledge_documents WHERE id=:doc"), {"doc": doc})
            == doc
        )


def _wait_for_lock(conn, blocker, pid):
    deadline = time.monotonic() + 5
    while not conn.scalar(
        text("SELECT :blocker = ANY(pg_blocking_pids(:pid))"), {"blocker": blocker, "pid": pid}
    ):
        if time.monotonic() >= deadline:
            pytest.fail("concurrent transaction did not wait for its writer")
        time.sleep(0.01)


def test_old_writer_serializes_with_first_publication_from_v1(ready_release, monkeypatch):
    engine, params = ready_release
    monkeypatch.setattr(indexing, "resolve_snapshot_embedding_config", lambda *_args: None)
    pid_queue = Queue()

    def publish():
        with Session(engine) as session:
            pid_queue.put(session.scalar(text("SELECT pg_backend_pid()")))
            with pytest.raises(indexing.IndexConflict, match="index_manifest_changed"):
                indexing.switch_index_release(
                    session,
                    tenant_id=params["tenant"],
                    knowledge_base_id=params["kb"],
                    release_id=params["release"],
                    expected_generation=0,
                )

    with ThreadPoolExecutor(max_workers=1) as pool:
        with engine.begin() as old_writer:
            blocker = old_writer.scalar(text("SELECT pg_backend_pid()"))
            # This is a pre-A1 writer. It neither locks the KB nor updates the v2 pointer.
            old_writer.execute(
                text("UPDATE knowledge_documents SET sha256='new' WHERE id=:doc"), params
            )
            publication = pool.submit(publish)
            _wait_for_lock(old_writer, blocker, pid_queue.get(timeout=5))
        publication.result(timeout=5)
    with engine.connect() as conn:
        assert conn.execute(
            text("SELECT active_release_id,generation FROM knowledge_index_states")
        ).one() == (None, 0)
