from __future__ import annotations

import importlib.util
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, inspect, text

DATABASE_URL = os.getenv("SUPERNOVA_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not DATABASE_URL,
    reason="PostgreSQL migration integration tests require SUPERNOVA_DATABASE_URL",
)

MIGRATIONS_DIR = Path(__file__).resolve().parents[1] / "migrations" / "versions"


def _load_migration(filename: str):
    path = MIGRATIONS_DIR / filename
    spec = importlib.util.spec_from_file_location(filename.removesuffix(".py"), path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run_migration(connection, migration) -> None:
    context = MigrationContext.configure(connection)
    with Operations.context(context):
        migration.upgrade()


@pytest.fixture
def postgres_schema():
    engine = create_engine(DATABASE_URL)
    schema = f"test_chat_import_{uuid4().hex}"
    with engine.connect() as connection:
        connection.exec_driver_sql(f'CREATE SCHEMA "{schema}"')
        connection.commit()
        connection.exec_driver_sql(f'SET search_path TO "{schema}"')
        connection.commit()
        try:
            yield connection
        finally:
            connection.rollback()
            connection.exec_driver_sql("SET search_path TO public")
            connection.commit()
            connection.exec_driver_sql(f'DROP SCHEMA "{schema}" CASCADE')
            connection.commit()
    engine.dispose()


def _create_legacy_schema(connection) -> tuple:
    tenant_id = uuid4()
    source_id = uuid4()
    job_id = uuid4()
    file_id = uuid4()
    conversation_id = uuid4()
    message_id = uuid4()
    created_at = datetime.now(UTC) - timedelta(days=2)

    connection.exec_driver_sql(
        """
        CREATE TABLE tenants (
            id uuid PRIMARY KEY
        )
        """
    )
    connection.exec_driver_sql(
        """
        CREATE TABLE chat_sources (
            id uuid PRIMARY KEY,
            tenant_id uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            name varchar(120) NOT NULL,
            export_tool varchar(120),
            target_sender_ref varchar(255),
            target_sender_name varchar(160),
            identity_confirmed_at timestamptz,
            status varchar(16) NOT NULL DEFAULT 'active',
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT uq_chat_sources_tenant_id UNIQUE (tenant_id, id),
            CONSTRAINT ck_chat_sources_status
                CHECK (status IN ('active', 'deleting', 'deleted'))
        )
        """
    )
    connection.exec_driver_sql(
        """
        CREATE TABLE chat_import_jobs (
            id uuid PRIMARY KEY,
            tenant_id uuid NOT NULL,
            source_id uuid NOT NULL,
            idempotency_key varchar(128),
            status varchar(24) NOT NULL DEFAULT 'queued',
            progress integer NOT NULL DEFAULT 0,
            total_files integer NOT NULL DEFAULT 0,
            processed_files integer NOT NULL DEFAULT 0,
            parsed_messages bigint NOT NULL DEFAULT 0,
            imported_messages bigint NOT NULL DEFAULT 0,
            duplicate_messages bigint NOT NULL DEFAULT 0,
            error_count integer NOT NULL DEFAULT 0,
            error_summary varchar(1000),
            locked_at timestamptz,
            lease_expires_at timestamptz,
            worker_id varchar(120),
            attempts integer NOT NULL DEFAULT 0,
            max_attempts integer NOT NULL DEFAULT 5,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT uq_chat_import_jobs_tenant_id UNIQUE (tenant_id, id),
            CONSTRAINT uq_chat_import_jobs_tenant_source_id
                UNIQUE (tenant_id, source_id, id),
            CONSTRAINT uq_chat_import_jobs_idempotency
                UNIQUE (tenant_id, idempotency_key),
            CONSTRAINT fk_chat_import_jobs_source_same_tenant
                FOREIGN KEY (tenant_id, source_id)
                REFERENCES chat_sources (tenant_id, id) ON DELETE CASCADE,
            CONSTRAINT ck_chat_import_jobs_status
                CHECK (status IN (
                    'queued', 'processing', 'awaiting_identity',
                    'succeeded', 'failed', 'cancelled'
                )),
            CONSTRAINT ck_chat_import_jobs_progress
                CHECK (progress BETWEEN 0 AND 100)
        )
        """
    )
    connection.exec_driver_sql(
        """
        CREATE TABLE chat_import_files (
            id uuid PRIMARY KEY,
            tenant_id uuid NOT NULL,
            source_id uuid NOT NULL,
            job_id uuid NOT NULL,
            original_filename varchar(255) NOT NULL,
            extension varchar(12) NOT NULL,
            mime_type varchar(160) NOT NULL,
            file_size_bytes bigint NOT NULL,
            sha256 varchar(64) NOT NULL,
            object_key varchar(512) NOT NULL,
            status varchar(16) NOT NULL DEFAULT 'uploaded',
            parsed_message_count bigint NOT NULL DEFAULT 0,
            error_code varchar(80),
            error_message varchar(500),
            metadata_json json NOT NULL,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT uq_chat_import_files_tenant_id UNIQUE (tenant_id, id),
            CONSTRAINT uq_chat_import_files_tenant_source_id
                UNIQUE (tenant_id, source_id, id),
            CONSTRAINT uq_chat_import_files_source_sha256
                UNIQUE (tenant_id, source_id, sha256),
            CONSTRAINT uq_chat_import_files_object_key UNIQUE (object_key),
            CONSTRAINT fk_chat_import_files_source_same_tenant
                FOREIGN KEY (tenant_id, source_id)
                REFERENCES chat_sources (tenant_id, id) ON DELETE CASCADE,
            CONSTRAINT fk_chat_import_files_job_same_source
                FOREIGN KEY (tenant_id, source_id, job_id)
                REFERENCES chat_import_jobs (tenant_id, source_id, id) ON DELETE CASCADE,
            CONSTRAINT ck_chat_import_files_status
                CHECK (status IN ('uploaded', 'processing', 'parsed', 'failed', 'deleted')),
            CONSTRAINT ck_chat_import_files_size CHECK (file_size_bytes > 0)
        )
        """
    )
    connection.exec_driver_sql(
        """
        CREATE TABLE chat_conversations (
            id uuid PRIMARY KEY,
            tenant_id uuid NOT NULL,
            source_id uuid NOT NULL,
            external_conversation_id varchar(255) NOT NULL
        )
        """
    )
    connection.exec_driver_sql(
        """
        CREATE TABLE chat_participants (
            id uuid PRIMARY KEY,
            conversation_id uuid NOT NULL REFERENCES chat_conversations(id)
        )
        """
    )
    connection.exec_driver_sql(
        """
        CREATE TABLE chat_messages (
            id uuid PRIMARY KEY,
            conversation_id uuid NOT NULL REFERENCES chat_conversations(id),
            import_file_id uuid NOT NULL REFERENCES chat_import_files(id),
            content text NOT NULL
        )
        """
    )

    connection.execute(text("INSERT INTO tenants (id) VALUES (:id)"), {"id": tenant_id})
    connection.execute(
        text(
            "INSERT INTO chat_sources (id, tenant_id, name) "
            "VALUES (:id, :tenant_id, 'Legacy source')"
        ),
        {"id": source_id, "tenant_id": tenant_id},
    )
    connection.execute(
        text(
            """
            INSERT INTO chat_import_jobs (
                id, tenant_id, source_id, parsed_messages, imported_messages,
                duplicate_messages, status
            ) VALUES (
                :id, :tenant_id, :source_id, 17, 12, 5, 'awaiting_identity'
            )
            """
        ),
        {"id": job_id, "tenant_id": tenant_id, "source_id": source_id},
    )
    connection.execute(
        text(
            """
            INSERT INTO chat_import_files (
                id, tenant_id, source_id, job_id, original_filename, extension,
                mime_type, file_size_bytes, sha256, object_key, status,
                parsed_message_count, metadata_json, created_at
            ) VALUES (
                :id, :tenant_id, :source_id, :job_id, 'legacy.json', '.json',
                'application/json', 100, :sha256, 'legacy/object.json', 'deleted',
                17, '{}'::json, :created_at
            )
            """
        ),
        {
            "id": file_id,
            "tenant_id": tenant_id,
            "source_id": source_id,
            "job_id": job_id,
            "sha256": "a" * 64,
            "created_at": created_at,
        },
    )
    connection.execute(
        text(
            """
            INSERT INTO chat_conversations (
                id, tenant_id, source_id, external_conversation_id
            ) VALUES (:id, :tenant_id, :source_id, 'legacy-conversation')
            """
        ),
        {"id": conversation_id, "tenant_id": tenant_id, "source_id": source_id},
    )
    connection.execute(
        text(
            "INSERT INTO chat_messages (id, conversation_id, import_file_id, content) "
            "VALUES (:id, :conversation_id, :file_id, 'private chat text')"
        ),
        {"id": message_id, "conversation_id": conversation_id, "file_id": file_id},
    )
    return tenant_id, source_id, job_id, file_id, created_at


def test_0010_upgrades_legacy_0009_schema_without_losing_import_metadata(
    postgres_schema,
) -> None:
    connection = postgres_schema
    tenant_id, source_id, job_id, file_id, _created_at = _create_legacy_schema(connection)

    migration = _load_migration("0010_chat_import_text_only.py")
    _run_migration(connection, migration)
    _run_migration(connection, migration)

    inspector = inspect(connection)
    assert inspector.has_table("chat_import_sender_summaries")
    assert not inspector.has_table("chat_messages")
    assert not inspector.has_table("chat_participants")
    assert not inspector.has_table("chat_conversations")

    source_columns = {column["name"] for column in inspector.get_columns("chat_sources")}
    job_columns = {column["name"] for column in inspector.get_columns("chat_import_jobs")}
    file_columns = {column["name"] for column in inspector.get_columns("chat_import_files")}

    assert "platform" in source_columns
    assert "parsed_text_messages" in job_columns
    assert "ignored_non_text_messages" in job_columns
    assert "unique_senders" in job_columns
    assert "parsed_messages" not in job_columns
    assert "imported_messages" not in job_columns
    assert "duplicate_messages" not in job_columns
    assert "parsed_text_message_count" in file_columns
    assert "ignored_non_text_message_count" in file_columns
    assert "expires_at" in file_columns
    assert "parsed_message_count" not in file_columns
    assert "metadata_json" not in file_columns

    source = connection.execute(
        text("SELECT platform FROM chat_sources WHERE id = :id"),
        {"id": source_id},
    ).scalar_one()
    job = connection.execute(
        text(
            "SELECT parsed_text_messages, ignored_non_text_messages, unique_senders, status "
            "FROM chat_import_jobs WHERE id = :id"
        ),
        {"id": job_id},
    ).one()
    file_record = connection.execute(
        text(
            "SELECT parsed_text_message_count, ignored_non_text_message_count, "
            "expires_at, status, error_code FROM chat_import_files WHERE id = :id"
        ),
        {"id": file_id},
    ).one()

    assert source == "wechat"
    assert job.parsed_text_messages == 0
    assert job.ignored_non_text_messages == 0
    assert job.unique_senders == 0
    assert job.status == "awaiting_identity"
    assert file_record.parsed_text_message_count == 0
    assert file_record.ignored_non_text_message_count == 0
    assert file_record.expires_at <= datetime.now(UTC) + timedelta(seconds=5)
    assert file_record.status == "failed"
    assert file_record.error_code == "legacy_file_deleted"

    connection.execute(
        text("UPDATE chat_import_jobs SET status = 'ready_for_analysis' WHERE id = :id"),
        {"id": job_id},
    )
    connection.execute(
        text("UPDATE chat_import_files SET status = 'parsed' WHERE id = :id"),
        {"id": file_id},
    )
    connection.commit()

    assert connection.execute(
        text("SELECT COUNT(*) FROM chat_sources WHERE tenant_id = :tenant_id"),
        {"tenant_id": tenant_id},
    ).scalar_one() == 1


def test_0010_is_noop_after_revised_0009_on_a_fresh_database(postgres_schema) -> None:
    connection = postgres_schema
    connection.exec_driver_sql("CREATE TABLE tenants (id uuid PRIMARY KEY)")
    connection.commit()

    revised_0009 = _load_migration("0009_chat_import.py")
    migration_0010 = _load_migration("0010_chat_import_text_only.py")
    _run_migration(connection, revised_0009)
    _run_migration(connection, migration_0010)

    inspector = inspect(connection)
    assert inspector.has_table("chat_import_sender_summaries")
    assert not inspector.has_table("chat_messages")
    assert not inspector.has_table("chat_conversations")
    assert "platform" in {
        column["name"] for column in inspector.get_columns("chat_sources")
    }
    assert "parsed_text_messages" in {
        column["name"] for column in inspector.get_columns("chat_import_jobs")
    }
    assert "expires_at" in {
        column["name"] for column in inspector.get_columns("chat_import_files")
    }
