"""Track successful platform Embedding model verification.

Revision ID: 0013_embed_verify
Revises: 0012_platform_rbac
"""

import sqlalchemy as sa
from alembic import op

revision = "0013_embed_verify"
down_revision = "0012_platform_rbac"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "platform_embedding_models",
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("platform_embedding_models", "verified_at")
