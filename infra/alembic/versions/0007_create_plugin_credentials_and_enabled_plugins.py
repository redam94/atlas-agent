"""create plugin_credentials table + add enabled_plugins to projects

Revision ID: 0007
Revises: 0006
Create Date: 2026-04-29
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "plugin_credentials",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("plugin_name", sa.Text(), nullable=False),
        sa.Column("account_id", sa.Text(), nullable=False, server_default="default"),
        sa.Column("ciphertext", sa.LargeBinary(), nullable=False),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint(
            "plugin_name", "account_id", name="plugin_credentials_plugin_account_unique"
        ),
    )
    # Check if enabled_plugins column exists; if so, convert from JSONB to ARRAY(Text)
    # Otherwise, add it.
    op.execute(
        "ALTER TABLE projects "
        "DROP COLUMN IF EXISTS enabled_plugins"
    )
    op.add_column(
        "projects",
        sa.Column(
            "enabled_plugins",
            postgresql.ARRAY(sa.Text()),
            nullable=False,
            server_default="{}",
        ),
    )


def downgrade() -> None:
    op.drop_column("projects", "enabled_plugins")
    op.drop_table("plugin_credentials")
