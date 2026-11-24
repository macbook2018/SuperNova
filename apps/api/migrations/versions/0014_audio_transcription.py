"""Track platform audio transcription requests in usage records.

Revision ID: 0014_audio_transcription
Revises: 0013_embed_verify
"""

import sqlalchemy as sa
from alembic import op

revision = "0014_audio_transcription"
down_revision = "0013_embed_verify"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "usage_records",
        sa.Column(
            "request_type",
            sa.String(length=32),
            server_default="chat",
            nullable=False,
        ),
    )
    op.create_check_constraint(
        "ck_usage_records_request_type",
        "usage_records",
        "request_type IN ('chat', 'audio_transcription')",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_usage_records_request_type",
        "usage_records",
        type_="check",
    )
    op.drop_column("usage_records", "request_type")
