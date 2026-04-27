"""Base Pydantic model classes for ATLAS.

All ATLAS data models inherit from one of these bases:

- ``AtlasModel`` — strict, immutable. Use for value objects, requests, results.
- ``MutableAtlasModel`` — strict, but mutable. Use for stateful builders / agent state.
- ``TimestampedModel`` — adds ``id``, ``created_at``, ``updated_at``. Use for entities.
"""
from datetime import datetime, timezone
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class AtlasModel(BaseModel):
    """Strict, frozen base. Use ``model_copy(update=...)`` to derive new instances.

    Note: ``updated_at`` on ``TimestampedModel`` is authoritative at the DB level
    (Postgres ``ON UPDATE`` trigger or SQLAlchemy ``onupdate=func.now()``);
    in-memory ``model_copy`` does not refresh it.
    """

    model_config = ConfigDict(
        strict=True,
        frozen=True,
        populate_by_name=True,
    )


class MutableAtlasModel(AtlasModel):
    """Strict, mutable base for stateful objects (e.g. agent state)."""

    model_config = ConfigDict(
        **{**AtlasModel.model_config, "frozen": False, "validate_assignment": True},
    )


class TimestampedModel(AtlasModel):
    """Frozen entity with an auto-generated id and timestamps."""

    id: UUID = Field(default_factory=uuid4)
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)
