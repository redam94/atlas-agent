"""Base Pydantic model classes for ATLAS.

All ATLAS data models inherit from one of these bases:

- ``AtlasModel`` — strict, immutable. Use for value objects, requests, results.
- ``MutableAtlasModel`` — strict, but mutable. Use for stateful builders / agent state.
- ``TimestampedModel`` — adds ``id``, ``created_at``, ``updated_at``. Use for entities.
"""

from datetime import UTC, datetime
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field


def _utcnow() -> datetime:
    return datetime.now(UTC)


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


class AtlasRequestModel(BaseModel):
    """Base for HTTP request bodies and JSON-inbound models.

    Lenient (no ``strict=True``): allows Pydantic's standard coercion
    (e.g. JSON string ``"local_only"`` → ``PrivacyLevel.LOCAL_ONLY``) so
    that FastAPI can deserialize request bodies. Still ``frozen=True`` so
    parsed DTOs are immutable.

    Use ``AtlasModel`` for internal value objects where strict type
    identity matters; use ``AtlasRequestModel`` for anything that arrives
    over HTTP / JSON.
    """

    model_config = ConfigDict(
        frozen=True,
        populate_by_name=True,
    )


class TimestampedModel(AtlasModel):
    """Frozen entity with an auto-generated id and timestamps."""

    id: UUID = Field(default_factory=uuid4)
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)
