"""add discord_channel_id and notified_at to ingestion_jobs

Revision ID: 0008
Revises: 0007
Create Date: 2026-04-30
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("ingestion_jobs", sa.Column("discord_channel_id", sa.Text(), nullable=True))
    op.add_column(
        "ingestion_jobs",
        sa.Column("notified_at", sa.TIMESTAMP(timezone=True), nullable=True),
    )
    op.create_index(
        "ingestion_jobs_status_notified_at_idx",
        "ingestion_jobs",
        ["status", "notified_at"],
    )


def downgrade() -> None:
    op.drop_index("ingestion_jobs_status_notified_at_idx", table_name="ingestion_jobs")
    op.drop_column("ingestion_jobs", "notified_at")
    op.drop_column("ingestion_jobs", "discord_channel_id")
