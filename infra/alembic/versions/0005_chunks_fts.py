"""add tsvector + partial GIN index on knowledge_nodes for BM25

Revision ID: 0005
Revises: 0004
Create Date: 2026-04-29
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE knowledge_nodes "
        "ADD COLUMN fts tsvector "
        "GENERATED ALWAYS AS (to_tsvector('english', coalesce(text, ''))) STORED"
    )
    op.execute(
        "CREATE INDEX knowledge_nodes_fts_chunk_idx "
        "ON knowledge_nodes USING GIN (fts) "
        "WHERE type = 'chunk'"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS knowledge_nodes_fts_chunk_idx")
    op.execute("ALTER TABLE knowledge_nodes DROP COLUMN IF EXISTS fts")
