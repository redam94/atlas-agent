"""Pydantic domain models for Project + related enums.

Three model variants:
- ``Project`` — full domain entity, returned from the API
- ``ProjectCreate`` — POST body for creating a project
- ``ProjectUpdate`` — PATCH body, all fields optional
"""
from enum import StrEnum

from pydantic import Field

from atlas_core.models.base import (
    AtlasRequestModel,
    TimestampedModel,
)


class PrivacyLevel(StrEnum):
    CLOUD_OK = "cloud_ok"
    LOCAL_ONLY = "local_only"


class ProjectStatus(StrEnum):
    ACTIVE = "active"
    PAUSED = "paused"
    ARCHIVED = "archived"


class Project(TimestampedModel):
    """Full project entity. Returned from GET / POST / PATCH endpoints."""

    user_id: str
    name: str
    description: str | None = None
    status: ProjectStatus = ProjectStatus.ACTIVE
    privacy_level: PrivacyLevel = PrivacyLevel.CLOUD_OK
    default_model: str
    enabled_plugins: list[str] = Field(default_factory=list)


class ProjectCreate(AtlasRequestModel):
    """POST /projects body."""

    name: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    privacy_level: PrivacyLevel = PrivacyLevel.CLOUD_OK
    default_model: str = Field(min_length=1)
    enabled_plugins: list[str] = Field(default_factory=list)


class ProjectUpdate(AtlasRequestModel):
    """PATCH /projects/{id} body. All fields optional; provided fields overwrite."""

    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    status: ProjectStatus | None = None
    privacy_level: PrivacyLevel | None = None
    default_model: str | None = Field(default=None, min_length=1)
    enabled_plugins: list[str] | None = None
