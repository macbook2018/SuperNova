"""Phase 6 knowledge ingestion, vector index and usage foundation.

Revision ID: 0008_phase_6_knowledge
Revises: 0007_phase_5c_metering
Create Date: 2026-11-12
"""

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector

revision = "0008_phase_6_knowledge"
down_revision = "0007_phase_5c_metering"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")

    op.create_table(
        "platform_embedding_models",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("code", sa.String(length=80), nullable=False),
        sa.Column("display_name", sa.String(length=100), nullable=False),
        sa.Column("provider_type", sa.String(length=32), nullable=False),
        sa.Column("upstream_model_name", sa.String(length=120), nullable=False),
        sa.Column("base_url", sa.String(length=2048), nullable=False),
        sa.Column("credential_ref", sa.String(length=255), nullable=False),
        sa.Column("dimensions", sa.Integer(), nullable=False, server_default="1536"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("dimensions = 1536", name="ck_platform_embedding_dimensions"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code", name="uq_platform_embedding_models_code"),
    )
    op.create_index(
        "ix_platform_embedding_models_enabled",
        "platform_embedding_models",
        ["enabled"],
    )

    with op.batch_alter_table("knowledge_bases") as batch:
        batch.add_column(
            sa.Column("embedding_source", sa.String(length=16), nullable=False, server_default="platform")
        )
        batch.add_column(sa.Column("platform_embedding_model_id", sa.Uuid(as_uuid=True), nullable=True))
        batch.add_column(sa.Column("embedding_provider_id", sa.Uuid(as_uuid=True), nullable=True))
        batch.add_column(sa.Column("embedding_model_name", sa.String(length=120), nullable=True))
        batch.add_column(
            sa.Column("embedding_dimensions", sa.Integer(), nullable=False, server_default="1536")
        )
        batch.add_column(sa.Column("chunk_size", sa.Integer(), nullable=False, server_default="800"))
        batch.add_column(sa.Column("chunk_overlap", sa.Integer(), nullable=False, server_default="120"))
        batch.add_column(sa.Column("retrieval_top_k", sa.Integer(), nullable=False, server_default="5"))
        batch.add_column(
            sa.Column("retrieval_score_threshold", sa.Float(), nullable=False, server_default="0.15")
        )
        batch.add_column(sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()))
        batch.add_column(sa.Column("active_index_version", sa.Integer(), nullable=False, server_default="1"))
        batch.create_foreign_key(
            "fk_knowledge_bases_platform_embedding_model",
            "platform_embedding_models",
            ["platform_embedding_model_id"],
            ["id"],
            ondelete="RESTRICT",
        )
        batch.create_foreign_key(
            "fk_knowledge_bases_embedding_provider_same_tenant",
            "model_providers",
            ["tenant_id", "embedding_provider_id"],
            ["tenant_id", "id"],
            ondelete="RESTRICT",
        )
        batch.create_check_constraint(
            "ck_knowledge_bases_embedding_source",
            "embedding_source IN ('platform', 'byok')",
        )
        batch.create_check_constraint(
            "ck_knowledge_bases_embedding_source_reference",
            "(embedding_source = 'platform' AND embedding_provider_id IS NULL) "
            "OR (embedding_source = 'byok' AND platform_embedding_model_id IS NULL)",
        )
        batch.create_check_constraint(
            "ck_knowledge_bases_embedding_dimensions",
            "embedding_dimensions = 1536",
        )
        batch.create_check_constraint(
            "ck_knowledge_bases_chunk_settings",
            "chunk_size BETWEEN 100 AND 4000 AND chunk_overlap >= 0 AND chunk_overlap < chunk_size",
        )
        batch.create_check_constraint(
            "ck_knowledge_bases_retrieval_top_k",
            "retrieval_top_k BETWEEN 1 AND 20",
        )
        batch.create_check_constraint(
            "ck_knowledge_bases_retrieval_threshold",
            "retrieval_score_threshold BETWEEN 0 AND 1",
        )

    op.create_table(
        "knowledge_documents",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("tenant_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("knowledge_base_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("original_filename", sa.String(length=255), nullable=False),
        sa.Column("source_type", sa.String(length=16), nullable=False),
        sa.Column("source_url", sa.String(length=2048), nullable=True),
        sa.Column("object_key", sa.String(length=512), nullable=False),
        sa.Column("mime_type", sa.String(length=160), nullable=False),
        sa.Column("file_size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="queued"),
        sa.Column("error_code", sa.String(length=80), nullable=True),
        sa.Column("error_message", sa.String(length=500), nullable=True),
        sa.Column("active_index_version", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("pending_index_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("chunk_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id", "knowledge_base_id"],
            ["knowledge_bases.tenant_id", "knowledge_bases.id"],
            name="fk_knowledge_documents_kb_same_tenant",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint("source_type IN ('upload', 'url')", name="ck_knowledge_documents_source_type"),
        sa.CheckConstraint(
            "status IN ('queued', 'processing', 'ready', 'failed', 'deleting', 'deleted')",
            name="ck_knowledge_documents_status",
        ),
        sa.CheckConstraint("file_size_bytes >= 0", name="ck_knowledge_documents_size"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_knowledge_documents_tenant_id"),
        sa.UniqueConstraint(
            "tenant_id", "knowledge_base_id", "sha256",
            name="uq_knowledge_documents_kb_sha256",
        ),
        sa.UniqueConstraint("object_key", name="uq_knowledge_documents_object_key"),
    )
    op.create_index(
        "ix_knowledge_documents_scope_status",
        "knowledge_documents",
        ["tenant_id", "knowledge_base_id", "status", "created_at"],
    )

    op.create_table(
        "knowledge_ingestion_jobs",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("tenant_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("knowledge_base_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("document_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("job_type", sa.String(length=16), nullable=False, server_default="ingest"),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="queued"),
        sa.Column("dedupe_key", sa.String(length=180), nullable=False),
        sa.Column("index_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="5"),
        sa.Column("available_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("locked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("worker_id", sa.String(length=120), nullable=True),
        sa.Column("last_error", sa.String(length=1000), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id", "knowledge_base_id"],
            ["knowledge_bases.tenant_id", "knowledge_bases.id"],
            name="fk_knowledge_jobs_kb_same_tenant",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "document_id"],
            ["knowledge_documents.tenant_id", "knowledge_documents.id"],
            name="fk_knowledge_jobs_document_same_tenant",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            "job_type IN ('ingest', 'reindex', 'delete')",
            name="ck_knowledge_jobs_type",
        ),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'retry_wait', 'succeeded', 'dead_letter', 'cancelled')",
            name="ck_knowledge_jobs_status",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "dedupe_key", name="uq_knowledge_jobs_dedupe"),
    )
    op.create_index(
        "ix_knowledge_jobs_claim",
        "knowledge_ingestion_jobs",
        ["status", "available_at", "created_at"],
    )
    op.create_index(
        "ix_knowledge_jobs_scope",
        "knowledge_ingestion_jobs",
        ["tenant_id", "knowledge_base_id", "created_at"],
    )

    op.create_table(
        "knowledge_chunks",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("tenant_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("knowledge_base_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("document_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("index_version", sa.Integer(), nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("embedding", Vector(1536), nullable=False),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id", "knowledge_base_id"],
            ["knowledge_bases.tenant_id", "knowledge_bases.id"],
            name="fk_knowledge_chunks_kb_same_tenant",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "document_id"],
            ["knowledge_documents.tenant_id", "knowledge_documents.id"],
            name="fk_knowledge_chunks_document_same_tenant",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id", "document_id", "index_version", "chunk_index",
            name="uq_knowledge_chunks_document_version_index",
        ),
    )
    op.create_index(
        "ix_knowledge_chunks_scope",
        "knowledge_chunks",
        ["tenant_id", "knowledge_base_id", "document_id", "index_version"],
    )
    op.create_index(
        "ix_knowledge_chunks_embedding_hnsw",
        "knowledge_chunks",
        ["embedding"],
        postgresql_using="hnsw",
        postgresql_ops={"embedding": "vector_cosine_ops"},
    )
    op.create_index(
        "ix_knowledge_chunks_content_trgm",
        "knowledge_chunks",
        ["content"],
        postgresql_using="gin",
        postgresql_ops={"content": "gin_trgm_ops"},
    )

    op.create_table(
        "knowledge_usage_records",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("tenant_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("knowledge_base_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("document_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("operation", sa.String(length=32), nullable=False),
        sa.Column("embedding_model_name", sa.String(length=120), nullable=True),
        sa.Column("input_tokens", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("request_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("storage_bytes", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("chunk_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("latency_ms", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id", "knowledge_base_id"],
            ["knowledge_bases.tenant_id", "knowledge_bases.id"],
            name="fk_knowledge_usage_kb_same_tenant",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["knowledge_documents.id"],
            name="fk_knowledge_usage_document",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_knowledge_usage_tenant_created",
        "knowledge_usage_records",
        ["tenant_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_knowledge_usage_tenant_created", table_name="knowledge_usage_records")
    op.drop_table("knowledge_usage_records")
    op.drop_index("ix_knowledge_chunks_content_trgm", table_name="knowledge_chunks")
    op.drop_index("ix_knowledge_chunks_embedding_hnsw", table_name="knowledge_chunks")
    op.drop_index("ix_knowledge_chunks_scope", table_name="knowledge_chunks")
    op.drop_table("knowledge_chunks")
    op.drop_index("ix_knowledge_jobs_scope", table_name="knowledge_ingestion_jobs")
    op.drop_index("ix_knowledge_jobs_claim", table_name="knowledge_ingestion_jobs")
    op.drop_table("knowledge_ingestion_jobs")
    op.drop_index("ix_knowledge_documents_scope_status", table_name="knowledge_documents")
    op.drop_table("knowledge_documents")
    with op.batch_alter_table("knowledge_bases") as batch:
        batch.drop_constraint("ck_knowledge_bases_retrieval_threshold", type_="check")
        batch.drop_constraint("ck_knowledge_bases_retrieval_top_k", type_="check")
        batch.drop_constraint("ck_knowledge_bases_chunk_settings", type_="check")
        batch.drop_constraint("ck_knowledge_bases_embedding_dimensions", type_="check")
        batch.drop_constraint("ck_knowledge_bases_embedding_source_reference", type_="check")
        batch.drop_constraint("ck_knowledge_bases_embedding_source", type_="check")
        batch.drop_constraint("fk_knowledge_bases_embedding_provider_same_tenant", type_="foreignkey")
        batch.drop_constraint("fk_knowledge_bases_platform_embedding_model", type_="foreignkey")
        batch.drop_column("active_index_version")
        batch.drop_column("enabled")
        batch.drop_column("retrieval_score_threshold")
        batch.drop_column("retrieval_top_k")
        batch.drop_column("chunk_overlap")
        batch.drop_column("chunk_size")
        batch.drop_column("embedding_dimensions")
        batch.drop_column("embedding_model_name")
        batch.drop_column("embedding_provider_id")
        batch.drop_column("platform_embedding_model_id")
        batch.drop_column("embedding_source")
    op.drop_index("ix_platform_embedding_models_enabled", table_name="platform_embedding_models")
    op.drop_table("platform_embedding_models")
