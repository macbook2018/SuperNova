"""Create tenant-scoped model providers, personas and knowledge bases.

Revision ID: 0003_persona_provider_catalog
Revises: 0002_identity_tenant
Create Date: 2026-11-12
"""

import sqlalchemy as sa
from alembic import op

revision = "0003_persona_provider_catalog"
down_revision = "0002_identity_tenant"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "model_providers",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("tenant_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=80), nullable=False),
        sa.Column("provider_type", sa.String(length=32), nullable=False),
        sa.Column("base_url", sa.String(length=2048), nullable=False),
        sa.Column("api_key_ciphertext", sa.Text(), nullable=True),
        sa.Column("api_key_last4", sa.String(length=4), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_model_providers_tenant_id"),
        sa.UniqueConstraint("tenant_id", "name", name="uq_model_providers_tenant_name"),
    )
    op.create_index(
        "ix_model_providers_tenant_created",
        "model_providers",
        ["tenant_id", "created_at"],
    )

    op.create_table(
        "knowledge_bases",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("tenant_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("description", sa.String(length=500), nullable=False, server_default=""),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_knowledge_bases_tenant_id"),
        sa.UniqueConstraint("tenant_id", "name", name="uq_knowledge_bases_tenant_name"),
    )
    op.create_index(
        "ix_knowledge_bases_tenant_created",
        "knowledge_bases",
        ["tenant_id", "created_at"],
    )

    op.create_table(
        "personas",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("tenant_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("slug", sa.String(length=80), nullable=False),
        sa.Column("description", sa.String(length=500), nullable=False, server_default=""),
        sa.Column("system_prompt", sa.Text(), nullable=False),
        sa.Column("provider_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("model_name", sa.String(length=120), nullable=False),
        sa.Column("temperature", sa.Float(), nullable=False, server_default="0.7"),
        sa.Column("top_p", sa.Float(), nullable=False, server_default="1.0"),
        sa.Column("max_tokens", sa.Integer(), nullable=False, server_default="2048"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "provider_id"],
            ["model_providers.tenant_id", "model_providers.id"],
            name="fk_personas_provider_same_tenant",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_personas_tenant_id"),
        sa.UniqueConstraint("tenant_id", "slug", name="uq_personas_tenant_slug"),
    )
    op.create_index("ix_personas_tenant_enabled", "personas", ["tenant_id", "enabled"])
    op.create_index("ix_personas_tenant_created", "personas", ["tenant_id", "created_at"])

    op.create_table(
        "persona_knowledge_bases",
        sa.Column("tenant_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("persona_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("knowledge_base_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "persona_id"],
            ["personas.tenant_id", "personas.id"],
            name="fk_persona_knowledge_persona_same_tenant",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "knowledge_base_id"],
            ["knowledge_bases.tenant_id", "knowledge_bases.id"],
            name="fk_persona_knowledge_base_same_tenant",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("tenant_id", "persona_id", "knowledge_base_id"),
    )
    op.create_index(
        "ix_persona_knowledge_bases_knowledge_base",
        "persona_knowledge_bases",
        ["tenant_id", "knowledge_base_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_persona_knowledge_bases_knowledge_base", table_name="persona_knowledge_bases")
    op.drop_table("persona_knowledge_bases")
    op.drop_index("ix_personas_tenant_created", table_name="personas")
    op.drop_index("ix_personas_tenant_enabled", table_name="personas")
    op.drop_table("personas")
    op.drop_index("ix_knowledge_bases_tenant_created", table_name="knowledge_bases")
    op.drop_table("knowledge_bases")
    op.drop_index("ix_model_providers_tenant_created", table_name="model_providers")
    op.drop_table("model_providers")
