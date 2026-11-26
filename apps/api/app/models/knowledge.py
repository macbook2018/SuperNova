from datetime import datetime
from uuid import UUID, uuid4

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    DDL,
    JSON,
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    event,
    func,
)
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.knowledge_triggers_0015 import CREATE_LEGACY_GUARDS, DROP_LEGACY_GUARDS

# Test and metadata-driven schema creation may bypass Alembic migrations.
event.listen(
    Base.metadata,
    "before_create",
    DDL("CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA public").execute_if(
        dialect="postgresql"
    ),
)
event.listen(
    Base.metadata,
    "before_create",
    DDL("CREATE EXTENSION IF NOT EXISTS pg_trgm WITH SCHEMA public").execute_if(
        dialect="postgresql"
    ),
)


@compiles(Vector, "sqlite")
def _compile_vector_for_sqlite(_type, _compiler, **_kw):
    # SQLite is used for fast API unit tests; production vector search runs on PostgreSQL.
    return "JSON"


class PlatformEmbeddingModel(Base):
    __tablename__ = "platform_embedding_models"
    __table_args__ = (
        UniqueConstraint("code", name="uq_platform_embedding_models_code"),
        CheckConstraint("dimensions = 1536", name="ck_platform_embedding_dimensions"),
        Index("ix_platform_embedding_models_enabled", "enabled"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    code: Mapped[str] = mapped_column(String(80), nullable=False)
    display_name: Mapped[str] = mapped_column(String(100), nullable=False)
    provider_type: Mapped[str] = mapped_column(String(32), nullable=False)
    upstream_model_name: Mapped[str] = mapped_column(String(120), nullable=False)
    base_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    credential_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    dimensions: Mapped[int] = mapped_column(Integer, nullable=False, default=1536)
    enabled: Mapped[bool] = mapped_column(nullable=False, default=False, server_default="false")
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class KnowledgeDocument(Base):
    __tablename__ = "knowledge_documents"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_knowledge_documents_tenant_id"),
        UniqueConstraint("tenant_id", "knowledge_base_id", "id", name="uq_ki_document_scope"),
        UniqueConstraint(
            "tenant_id", "knowledge_base_id", "sha256",
            name="uq_knowledge_documents_kb_sha256",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "knowledge_base_id"],
            ["knowledge_bases.tenant_id", "knowledge_bases.id"],
            name="fk_knowledge_documents_kb_same_tenant",
            ondelete="CASCADE",
        ),
        CheckConstraint(
            "source_type IN ('upload', 'url')",
            name="ck_knowledge_documents_source_type",
        ),
        CheckConstraint(
            "status IN ('queued', 'processing', 'ready', 'failed', 'deleting', 'deleted')",
            name="ck_knowledge_documents_status",
        ),
        CheckConstraint("file_size_bytes >= 0", name="ck_knowledge_documents_size"),
        Index(
            "ix_knowledge_documents_scope_status",
            "tenant_id", "knowledge_base_id", "status", "created_at",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    knowledge_base_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    source_type: Mapped[str] = mapped_column(String(16), nullable=False)
    source_url: Mapped[str | None] = mapped_column(String(2048))
    object_key: Mapped[str] = mapped_column(String(512), nullable=False, unique=True)
    mime_type: Mapped[str] = mapped_column(String(160), nullable=False)
    file_size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    sha256: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="queued")
    error_code: Mapped[str | None] = mapped_column(String(80))
    error_message: Mapped[str | None] = mapped_column(String(500))
    active_index_version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    pending_index_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    chunk_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class KnowledgeIngestionJob(Base):
    __tablename__ = "knowledge_ingestion_jobs"
    __table_args__ = (
        UniqueConstraint("tenant_id", "dedupe_key", name="uq_knowledge_jobs_dedupe"),
        ForeignKeyConstraint(
            ["tenant_id", "knowledge_base_id"],
            ["knowledge_bases.tenant_id", "knowledge_bases.id"],
            name="fk_knowledge_jobs_kb_same_tenant",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "document_id"],
            ["knowledge_documents.tenant_id", "knowledge_documents.id"],
            name="fk_knowledge_jobs_document_same_tenant",
            ondelete="CASCADE",
        ),
        CheckConstraint(
            "job_type IN ('ingest', 'reindex', 'delete')",
            name="ck_knowledge_jobs_type",
        ),
        CheckConstraint(
            "status IN ('queued', 'running', 'retry_wait', 'succeeded', "
            "'dead_letter', 'cancelled')",
            name="ck_knowledge_jobs_status",
        ),
        Index("ix_knowledge_jobs_claim", "status", "available_at", "created_at"),
        Index("ix_knowledge_jobs_scope", "tenant_id", "knowledge_base_id", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    knowledge_base_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    document_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    job_type: Mapped[str] = mapped_column(String(16), nullable=False, default="ingest")
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="queued")
    dedupe_key: Mapped[str] = mapped_column(String(180), nullable=False)
    index_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=5)
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    worker_id: Mapped[str | None] = mapped_column(String(120))
    last_error: Mapped[str | None] = mapped_column(String(1000))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class KnowledgeChunk(Base):
    __tablename__ = "knowledge_chunks"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "document_id", "index_version", "chunk_index",
            name="uq_knowledge_chunks_document_version_index",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "knowledge_base_id"],
            ["knowledge_bases.tenant_id", "knowledge_bases.id"],
            name="fk_knowledge_chunks_kb_same_tenant",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "document_id"],
            ["knowledge_documents.tenant_id", "knowledge_documents.id"],
            name="fk_knowledge_chunks_document_same_tenant",
            ondelete="CASCADE",
        ),
        Index(
            "ix_knowledge_chunks_scope",
            "tenant_id", "knowledge_base_id", "document_id", "index_version",
        ),
        Index(
            "ix_knowledge_chunks_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
        Index(
            "ix_knowledge_chunks_content_trgm",
            "content",
            postgresql_using="gin",
            postgresql_ops={"content": "gin_trgm_ops"},
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    knowledge_base_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    document_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    index_version: Mapped[int] = mapped_column(Integer, nullable=False)
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    embedding: Mapped[list[float]] = mapped_column(Vector(1536), nullable=False)
    metadata_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class KnowledgeUsageRecord(Base):
    __tablename__ = "knowledge_usage_records"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "knowledge_base_id"],
            ["knowledge_bases.tenant_id", "knowledge_bases.id"],
            name="fk_knowledge_usage_kb_same_tenant",
            ondelete="CASCADE",
        ),
        Index("ix_knowledge_usage_tenant_created", "tenant_id", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    knowledge_base_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    document_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("knowledge_documents.id", ondelete="SET NULL")
    )
    operation: Mapped[str] = mapped_column(String(32), nullable=False)
    embedding_model_name: Mapped[str | None] = mapped_column(String(120))
    input_tokens: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    request_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    storage_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    chunk_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class KnowledgeDocumentIndexRevision(Base):
    __tablename__ = "knowledge_document_index_revisions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "knowledge_base_id", "document_id"],
            [
                "knowledge_documents.tenant_id",
                "knowledge_documents.knowledge_base_id",
                "knowledge_documents.id",
            ],
            ondelete="CASCADE",
            name="fk_ki_doc_revision_scope",
        ),
        CheckConstraint("index_version > 0 AND chunk_count > 0", name="ck_ki_doc_revision_counts"),
        CheckConstraint(
            "profile_origin IN ('generated','legacy_unknown')", name="ck_ki_doc_revision_origin"
        ),
    )
    tenant_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    knowledge_base_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    document_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    index_version: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    chunk_count: Mapped[int] = mapped_column(Integer, nullable=False)
    reader_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    profile_origin: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


# V2 builds use their own tables so deployed v1 workers cannot claim a shadow job
# or collect a retained release. A missing state row (or a null pointer) selects v1.
class KnowledgeIndexRelease(Base):
    __tablename__ = "knowledge_index_releases"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "knowledge_base_id"],
            ["knowledge_bases.tenant_id", "knowledge_bases.id"],
            ondelete="CASCADE",
            name="fk_ki_release_kb",
        ),
        UniqueConstraint("tenant_id", "knowledge_base_id", "id", name="uq_ki_release_scope"),
        UniqueConstraint("tenant_id", "knowledge_base_id", "revision", name="uq_ki_release_rev"),
        UniqueConstraint("tenant_id", "knowledge_base_id", "request_key", name="uq_ki_release_req"),
        CheckConstraint("revision > 0 AND expected_generation >= 0", name="ck_ki_release_rev"),
        CheckConstraint(
            "status IN ('building','ready','failed','retired')", name="ck_ki_release_status"
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    knowledge_base_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    revision: Mapped[int] = mapped_column(BigInteger, nullable=False)
    expected_generation: Mapped[int] = mapped_column(BigInteger, nullable=False)
    request_key: Mapped[str] = mapped_column(String(180), nullable=False)
    config_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    embedding_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="building", server_default="building")
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class KnowledgeIndexState(Base):
    __tablename__ = "knowledge_index_states"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "knowledge_base_id"],
            ["knowledge_bases.tenant_id", "knowledge_bases.id"],
            ondelete="CASCADE",
            name="fk_ki_state_kb",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "knowledge_base_id", "active_release_id"],
            [
                "knowledge_index_releases.tenant_id",
                "knowledge_index_releases.knowledge_base_id",
                "knowledge_index_releases.id",
            ],
            name="fk_ki_state_release",
        ),
        CheckConstraint("last_revision >= 0 AND generation >= 0", name="ck_ki_state_generation"),
    )

    tenant_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    knowledge_base_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    active_release_id: Mapped[UUID | None] = mapped_column(Uuid)
    last_revision: Mapped[int] = mapped_column(BigInteger, default=0, server_default="0")
    generation: Mapped[int] = mapped_column(BigInteger, default=0, server_default="0")


class KnowledgeIndexMember(Base):
    __tablename__ = "knowledge_index_members"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "knowledge_base_id", "release_id"],
            [
                "knowledge_index_releases.tenant_id",
                "knowledge_index_releases.knowledge_base_id",
                "knowledge_index_releases.id",
            ],
            ondelete="CASCADE",
            name="fk_ki_member_release",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "knowledge_base_id", "document_id"],
            [
                "knowledge_documents.tenant_id",
                "knowledge_documents.knowledge_base_id",
                "knowledge_documents.id",
            ],
            ondelete="CASCADE",
            name="fk_ki_member_document",
        ),
        CheckConstraint("status IN ('pending','ready','failed')", name="ck_ki_member_status"),
        CheckConstraint("source_version >= 0 AND chunk_count >= 0", name="ck_ki_member_counts"),
    )

    tenant_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    knowledge_base_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    release_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    document_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    source_version: Mapped[int] = mapped_column(Integer, nullable=False)
    source_sha256: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(12), default="pending", server_default="pending")
    chunk_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")


class KnowledgeIndexChunk(Base):
    __tablename__ = "knowledge_index_chunks"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "knowledge_base_id", "release_id", "document_id"],
            [
                "knowledge_index_members.tenant_id",
                "knowledge_index_members.knowledge_base_id",
                "knowledge_index_members.release_id",
                "knowledge_index_members.document_id",
            ],
            ondelete="CASCADE",
            name="fk_ki_chunk_member",
        ),
        UniqueConstraint(
            "tenant_id",
            "knowledge_base_id",
            "release_id",
            "document_id",
            "chunk_index",
            name="uq_ki_chunk_index",
        ),
        CheckConstraint("chunk_index >= 0", name="ck_ki_chunk_index"),
        Index(
            "ix_ki_chunk_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
        Index(
            "ix_ki_chunk_trgm",
            "content",
            postgresql_using="gin",
            postgresql_ops={"content": "gin_trgm_ops"},
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    knowledge_base_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    release_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    document_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    embedding: Mapped[list[float]] = mapped_column(Vector(1536), nullable=False)
    metadata_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)


class KnowledgeIndexBuildJob(Base):
    __tablename__ = "knowledge_index_build_jobs"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "knowledge_base_id", "release_id", "document_id"],
            [
                "knowledge_index_members.tenant_id",
                "knowledge_index_members.knowledge_base_id",
                "knowledge_index_members.release_id",
                "knowledge_index_members.document_id",
            ],
            ondelete="CASCADE",
            name="fk_ki_job_member",
        ),
        UniqueConstraint(
            "tenant_id", "knowledge_base_id", "release_id", "document_id", name="uq_ki_job_member"
        ),
        UniqueConstraint(
            "tenant_id",
            "knowledge_base_id",
            "release_id",
            "document_id",
            "id",
            name="uq_ki_job_scope_id",
        ),
        CheckConstraint(
            "status IN ('queued','running','retry_wait','succeeded','dead_letter','cancelled')",
            name="ck_ki_job_status",
        ),
        CheckConstraint("attempts >= 0 AND max_attempts > 0", name="ck_ki_job_attempts"),
        CheckConstraint(
            "(status = 'running' AND lease_token IS NOT NULL AND lease_expires_at IS NOT NULL) "
            "OR (status <> 'running' AND lease_token IS NULL AND lease_expires_at IS NULL)",
            name="ck_ki_job_lease",
        ),
        Index("ix_ki_job_claim", "status", "available_at", "lease_expires_at"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    knowledge_base_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    release_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    document_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="queued", server_default="queued")
    attempts: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    max_attempts: Mapped[int] = mapped_column(Integer, default=5, server_default="5")
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    lease_token: Mapped[UUID | None] = mapped_column(Uuid)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(String(1000))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class KnowledgeIndexObservation(Base):
    __tablename__ = "knowledge_index_observations"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "knowledge_base_id"],
            ["knowledge_bases.tenant_id", "knowledge_bases.id"],
            ondelete="CASCADE",
            name="fk_ki_obs_kb",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "knowledge_base_id", "release_id"],
            [
                "knowledge_index_releases.tenant_id",
                "knowledge_index_releases.knowledge_base_id",
                "knowledge_index_releases.id",
            ],
            name="fk_ki_obs_release",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "knowledge_base_id", "document_id"],
            [
                "knowledge_documents.tenant_id",
                "knowledge_documents.knowledge_base_id",
                "knowledge_documents.id",
            ],
            name="fk_ki_obs_document",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "knowledge_base_id", "release_id", "document_id", "job_id"],
            [
                "knowledge_index_build_jobs.tenant_id",
                "knowledge_index_build_jobs.knowledge_base_id",
                "knowledge_index_build_jobs.release_id",
                "knowledge_index_build_jobs.document_id",
                "knowledge_index_build_jobs.id",
            ],
            name="fk_ki_obs_job",
        ),
        CheckConstraint(
            "job_id IS NULL OR (document_id IS NOT NULL AND release_id IS NOT NULL)",
            name="ck_ki_obs_job_scope",
        ),
        CheckConstraint(
            "status IN ('started','succeeded','failed','unknown')", name="ck_ki_obs_status"
        ),
        CheckConstraint("input_tokens >= 0 AND output_tokens >= 0", name="ck_ki_obs_tokens"),
        Index("ix_ki_obs_scope_created", "tenant_id", "knowledge_base_id", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    knowledge_base_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    release_id: Mapped[UUID | None] = mapped_column(Uuid)
    document_id: Mapped[UUID | None] = mapped_column(Uuid)
    job_id: Mapped[UUID | None] = mapped_column(Uuid)
    request_id: Mapped[str | None] = mapped_column(String(180))
    operation: Mapped[str] = mapped_column(String(32), nullable=False)
    model_name: Mapped[str | None] = mapped_column(String(120))
    status: Mapped[str] = mapped_column(String(16), default="started", server_default="started")
    input_tokens: Mapped[int | None] = mapped_column(BigInteger)
    output_tokens: Mapped[int | None] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


# The candidate's identity and semantic configuration are write-once. The service
# changes only its lifecycle status and the timestamp of its first publication.
_RELEASE_GUARD_FUNCTION = """
CREATE FUNCTION sn_ki_guard_release() RETURNS trigger AS $$
BEGIN
    IF (OLD.id, OLD.tenant_id, OLD.knowledge_base_id, OLD.revision,
        OLD.expected_generation, OLD.request_key, OLD.embedding_fingerprint, OLD.config_json::jsonb)
       IS DISTINCT FROM
       (NEW.id, NEW.tenant_id, NEW.knowledge_base_id, NEW.revision,
        NEW.expected_generation, NEW.request_key, NEW.embedding_fingerprint, NEW.config_json::jsonb)
       OR (OLD.published_at IS NOT NULL AND OLD.published_at IS DISTINCT FROM NEW.published_at)
    THEN
        RAISE EXCEPTION 'knowledge index release identity and configuration are immutable';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql
"""
_RELEASE_GUARD_TRIGGER = """
CREATE TRIGGER ki_guard_release BEFORE UPDATE ON knowledge_index_releases
FOR EACH ROW EXECUTE FUNCTION sn_ki_guard_release()
"""
event.listen(
    KnowledgeIndexRelease.__table__,
    "after_create",
    DDL(_RELEASE_GUARD_FUNCTION).execute_if(dialect="postgresql"),
)
event.listen(
    KnowledgeIndexRelease.__table__,
    "after_create",
    DDL(_RELEASE_GUARD_TRIGGER).execute_if(dialect="postgresql"),
)
event.listen(
    KnowledgeIndexRelease.__table__,
    "after_drop",
    DDL("DROP FUNCTION sn_ki_guard_release()").execute_if(dialect="postgresql"),
)


# Observation is downstream of the v2 build tables. Its creation is the last
# step at which a legacy writer can acquire its version-aware database triggers.
for statement in CREATE_LEGACY_GUARDS:
    event.listen(
        KnowledgeIndexObservation.__table__,
        "after_create",
        DDL(statement).execute_if(dialect="postgresql"),
    )
for statement in DROP_LEGACY_GUARDS:
    event.listen(
        KnowledgeIndexObservation.__table__,
        "before_drop",
        DDL(statement).execute_if(dialect="postgresql"),
    )
