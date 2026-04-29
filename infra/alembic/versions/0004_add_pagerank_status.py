"""add pagerank_status to ingestion_jobs

Revision ID: 0004
Revises: 0003
Create Date: 2026-04-28
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "ingestion_jobs",
        sa.Column(
            "pagerank_status",
            sa.Text(),
            nullable=False,
            server_default="skipped",
        ),
    )


def downgrade() -> None:
    op.drop_column("ingestion_jobs", "pagerank_status")
