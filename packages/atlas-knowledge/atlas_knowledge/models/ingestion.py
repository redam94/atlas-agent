"""Ingestion request + job-state shapes."""

from datetime import datetime
from enum import StrEnum
from uuid import UUID

from atlas_core.models.base import AtlasModel, AtlasRequestModel
from pydantic import Field, HttpUrl, model_validator


class SourceType(StrEnum):
    MARKDOWN = "markdown"
    PDF = "pdf"
    URL = "url"


class IngestionStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class IngestRequest(AtlasRequestModel):
    """Payload for POST /api/v1/knowledge/ingest (text/markdown path).

    For PDF uploads the API uses multipart form, not this model — a separate
    handler reads the bytes and calls the service directly.
    """

    project_id: UUID
    source_type: SourceType
    text: str | None = Field(default=None, max_length=2_000_000)
    source_filename: str | None = None

    @model_validator(mode="after")
    def _require_text_or_filename(self) -> "IngestRequest":
        if self.source_type is SourceType.MARKDOWN and not self.text:
            raise ValueError("markdown ingest requires non-empty `text`")
        return self


class UrlIngestRequest(AtlasRequestModel):
    """Payload for POST /api/v1/knowledge/ingest/url.

    Pydantic v2 HttpUrl handles scheme + structural validation; the router
    additionally runs validate_url() for the SSRF / private-IP guard.
    """

    project_id: UUID
    url: HttpUrl


class IngestionJob(AtlasModel):
    """Persisted ingestion job state (mirrors IngestionJobORM)."""

    id: UUID
    user_id: str
    project_id: UUID
    source_type: SourceType
    source_filename: str | None = None
    status: IngestionStatus
    node_ids: list[UUID] = Field(default_factory=list)
    error: str | None = None
    created_at: datetime
    completed_at: datetime | None = None
