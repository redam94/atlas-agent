"""SQLAlchemy ORM models for ATLAS.

Each table in the spec maps to one ORM class here. Plan 2 ships
`ProjectORM`; later plans append `SessionORM`, `MessageORM`, etc.
"""

from datetime import datetime
from uuid import UUID

from sqlalchemy import TIMESTAMP, Index, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from atlas_core.db.base import Base


class ProjectORM(Base):
    """Maps to the `projects` table."""

    __tablename__ = "projects"

    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
        server_default=func.gen_random_uuid(),
    )
    user_id: Mapped[str] = mapped_column(Text, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="active")
    privacy_level: Mapped[str] = mapped_column(Text, nullable=False, server_default="cloud_ok")
    default_model: Mapped[str] = mapped_column(Text, nullable=False)
    enabled_plugins: Mapped[list[str]] = mapped_column(JSONB, nullable=False, server_default="[]")
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (Index("projects_user_idx", "user_id"),)
