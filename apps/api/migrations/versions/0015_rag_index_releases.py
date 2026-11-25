"""Add opt-in knowledge index releases alongside the existing v1 store."""

import sqlalchemy as sa
from alembic import op
from app.db.knowledge_triggers_0015 import CREATE_LEGACY_GUARDS, DROP_LEGACY_GUARDS
from pgvector.sqlalchemy import Vector

revision = "0015_rag_index_releases"
down_revision = "0014_audio_transcription"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # The globally unique document id already makes this candidate key unique.
    # Scope it to the KB as well so a manifest cannot reference another KB's document.
    op.create_unique_constraint(
        "uq_ki_document_scope", "knowledge_documents", ["tenant_id", "knowledge_base_id", "id"]
    )
    op.create_table(
        "knowledge_document_index_revisions",
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("knowledge_base_id", sa.Uuid(), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("index_version", sa.Integer(), nullable=False),
        sa.Column("source_sha256", sa.String(64), nullable=False),
        sa.Column("chunk_count", sa.Integer(), nullable=False),
        sa.Column("reader_fingerprint", sa.String(64), nullable=False),
        sa.Column("profile_origin", sa.String(16), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.PrimaryKeyConstraint("tenant_id", "knowledge_base_id", "document_id", "index_version"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "knowledge_base_id", "document_id"],
            [
                "knowledge_documents.tenant_id",
                "knowledge_documents.knowledge_base_id",
                "knowledge_documents.id",
            ],
            ondelete="CASCADE",
            name="fk_ki_doc_revision_scope",
        ),
        sa.CheckConstraint(
            "index_version > 0 AND chunk_count > 0", name="ck_ki_doc_revision_counts"
        ),
        sa.CheckConstraint(
            "profile_origin IN ('generated','legacy_unknown')", name="ck_ki_doc_revision_origin"
        ),
    )
    op.create_table(
        "knowledge_index_releases",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("knowledge_base_id", sa.Uuid(), nullable=False),
        sa.Column("revision", sa.BigInteger(), nullable=False),
        sa.Column("expected_generation", sa.BigInteger(), nullable=False),
        sa.Column("request_key", sa.String(length=180), nullable=False),
        sa.Column("config_json", sa.JSON(), nullable=False),
        sa.Column("embedding_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=16), server_default="building", nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('building','ready','failed','retired')", name="ck_ki_release_status"
        ),
        sa.CheckConstraint("revision > 0 AND expected_generation >= 0", name="ck_ki_release_rev"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "knowledge_base_id"],
            ["knowledge_bases.tenant_id", "knowledge_bases.id"],
            name="fk_ki_release_kb",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "knowledge_base_id", "id", name="uq_ki_release_scope"),
        sa.UniqueConstraint(
            "tenant_id", "knowledge_base_id", "request_key", name="uq_ki_release_req"
        ),
        sa.UniqueConstraint("tenant_id", "knowledge_base_id", "revision", name="uq_ki_release_rev"),
    )
    op.create_table(
        "knowledge_index_states",
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("knowledge_base_id", sa.Uuid(), nullable=False),
        sa.Column("active_release_id", sa.Uuid(), nullable=True),
        sa.Column("last_revision", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("generation", sa.BigInteger(), server_default="0", nullable=False),
        sa.CheckConstraint("last_revision >= 0 AND generation >= 0", name="ck_ki_state_generation"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "knowledge_base_id", "active_release_id"],
            [
                "knowledge_index_releases.tenant_id",
                "knowledge_index_releases.knowledge_base_id",
                "knowledge_index_releases.id",
            ],
            name="fk_ki_state_release",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "knowledge_base_id"],
            ["knowledge_bases.tenant_id", "knowledge_bases.id"],
            name="fk_ki_state_kb",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("tenant_id", "knowledge_base_id"),
    )
    op.create_table(
        "knowledge_index_members",
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("knowledge_base_id", sa.Uuid(), nullable=False),
        sa.Column("release_id", sa.Uuid(), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("source_version", sa.Integer(), nullable=False),
        sa.Column("source_sha256", sa.String(length=64), nullable=True),
        sa.Column("status", sa.String(length=12), server_default="pending", nullable=False),
        sa.Column("chunk_count", sa.Integer(), server_default="0", nullable=False),
        sa.CheckConstraint("status IN ('pending','ready','failed')", name="ck_ki_member_status"),
        sa.CheckConstraint("source_version >= 0 AND chunk_count >= 0", name="ck_ki_member_counts"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "knowledge_base_id", "document_id"],
            [
                "knowledge_documents.tenant_id",
                "knowledge_documents.knowledge_base_id",
                "knowledge_documents.id",
            ],
            name="fk_ki_member_document",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "knowledge_base_id", "release_id"],
            [
                "knowledge_index_releases.tenant_id",
                "knowledge_index_releases.knowledge_base_id",
                "knowledge_index_releases.id",
            ],
            name="fk_ki_member_release",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("tenant_id", "knowledge_base_id", "release_id", "document_id"),
    )
    op.create_table(
        "knowledge_index_build_jobs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("knowledge_base_id", sa.Uuid(), nullable=False),
        sa.Column("release_id", sa.Uuid(), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(length=16), server_default="queued", nullable=False),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("max_attempts", sa.Integer(), server_default="5", nullable=False),
        sa.Column(
            "available_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("lease_token", sa.Uuid(), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.String(length=1000), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('queued','running','retry_wait','succeeded','dead_letter','cancelled')",
            name="ck_ki_job_status",
        ),
        sa.CheckConstraint("attempts >= 0 AND max_attempts > 0", name="ck_ki_job_attempts"),
        sa.CheckConstraint(
            "(status = 'running' AND lease_token IS NOT NULL AND lease_expires_at IS NOT NULL) "
            "OR (status <> 'running' AND lease_token IS NULL AND lease_expires_at IS NULL)",
            name="ck_ki_job_lease",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "knowledge_base_id", "release_id", "document_id"],
            [
                "knowledge_index_members.tenant_id",
                "knowledge_index_members.knowledge_base_id",
                "knowledge_index_members.release_id",
                "knowledge_index_members.document_id",
            ],
            name="fk_ki_job_member",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id",
            "knowledge_base_id",
            "release_id",
            "document_id",
            "id",
            name="uq_ki_job_scope_id",
        ),
        sa.UniqueConstraint(
            "tenant_id", "knowledge_base_id", "release_id", "document_id", name="uq_ki_job_member"
        ),
    )
    op.create_index(
        "ix_ki_job_claim",
        "knowledge_index_build_jobs",
        ["status", "available_at", "lease_expires_at"],
        unique=False,
    )
    op.create_table(
        "knowledge_index_chunks",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("knowledge_base_id", sa.Uuid(), nullable=False),
        sa.Column("release_id", sa.Uuid(), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("embedding", Vector(dim=1536), nullable=False),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.CheckConstraint("chunk_index >= 0", name="ck_ki_chunk_index"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "knowledge_base_id", "release_id", "document_id"],
            [
                "knowledge_index_members.tenant_id",
                "knowledge_index_members.knowledge_base_id",
                "knowledge_index_members.release_id",
                "knowledge_index_members.document_id",
            ],
            name="fk_ki_chunk_member",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id",
            "knowledge_base_id",
            "release_id",
            "document_id",
            "chunk_index",
            name="uq_ki_chunk_index",
        ),
    )
    op.create_index(
        "ix_ki_chunk_hnsw",
        "knowledge_index_chunks",
        ["embedding"],
        unique=False,
        postgresql_using="hnsw",
        postgresql_ops={"embedding": "vector_cosine_ops"},
    )
    op.create_index(
        "ix_ki_chunk_trgm",
        "knowledge_index_chunks",
        ["content"],
        unique=False,
        postgresql_using="gin",
        postgresql_ops={"content": "gin_trgm_ops"},
    )
    op.create_table(
        "knowledge_index_observations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("knowledge_base_id", sa.Uuid(), nullable=False),
        sa.Column("release_id", sa.Uuid(), nullable=True),
        sa.Column("document_id", sa.Uuid(), nullable=True),
        sa.Column("job_id", sa.Uuid(), nullable=True),
        sa.Column("request_id", sa.String(length=180), nullable=True),
        sa.Column("operation", sa.String(length=32), nullable=False),
        sa.Column("model_name", sa.String(length=120), nullable=True),
        sa.Column("status", sa.String(length=16), server_default="started", nullable=False),
        sa.Column("input_tokens", sa.BigInteger(), nullable=True),
        sa.Column("output_tokens", sa.BigInteger(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('started','succeeded','failed','unknown')", name="ck_ki_obs_status"
        ),
        sa.CheckConstraint("input_tokens >= 0 AND output_tokens >= 0", name="ck_ki_obs_tokens"),
        sa.CheckConstraint(
            "job_id IS NULL OR (document_id IS NOT NULL AND release_id IS NOT NULL)",
            name="ck_ki_obs_job_scope",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "knowledge_base_id", "document_id"],
            [
                "knowledge_documents.tenant_id",
                "knowledge_documents.knowledge_base_id",
                "knowledge_documents.id",
            ],
            name="fk_ki_obs_document",
        ),
        sa.ForeignKeyConstraint(
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
        sa.ForeignKeyConstraint(
            ["tenant_id", "knowledge_base_id", "release_id"],
            [
                "knowledge_index_releases.tenant_id",
                "knowledge_index_releases.knowledge_base_id",
                "knowledge_index_releases.id",
            ],
            name="fk_ki_obs_release",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "knowledge_base_id"],
            ["knowledge_bases.tenant_id", "knowledge_bases.id"],
            name="fk_ki_obs_kb",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_ki_obs_scope_created",
        "knowledge_index_observations",
        ["tenant_id", "knowledge_base_id", "created_at"],
        unique=False,
    )
    op.execute("""
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
""")
    op.execute("""
CREATE TRIGGER ki_guard_release BEFORE UPDATE ON knowledge_index_releases
FOR EACH ROW EXECUTE FUNCTION sn_ki_guard_release()
""")

    for statement in CREATE_LEGACY_GUARDS:
        op.execute(statement)


def downgrade() -> None:
    # Reverting application images or selecting v1 never requires dropping these tables.
    # A schema downgrade is safe only for an unused installation. Lock before checking so
    # a concurrent build cannot be accepted between the check and dropping the tables.
    tables = (
        "knowledge_document_index_revisions",
        "knowledge_index_observations",
        "knowledge_index_chunks",
        "knowledge_index_build_jobs",
        "knowledge_index_members",
        "knowledge_index_states",
        "knowledge_index_releases",
    )
    op.execute("LOCK TABLE " + ", ".join(tables) + " IN ACCESS EXCLUSIVE MODE")
    for table_name in tables:
        if op.get_bind().scalar(sa.text(f"SELECT EXISTS(SELECT 1 FROM {table_name})")):
            raise RuntimeError(
                "RAG index data exists; preserve it and roll back the reader instead"
            )
    for statement in DROP_LEGACY_GUARDS:
        op.execute(statement)
    for table_name in tables:
        op.drop_table(table_name)
    op.execute("DROP FUNCTION sn_ki_guard_release()")
    op.drop_constraint("uq_ki_document_scope", "knowledge_documents", type_="unique")
