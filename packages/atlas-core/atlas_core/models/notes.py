"""Pydantic models for the notes API (Plan 6)."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import Field

from atlas_core.models.base import AtlasModel


class Note(AtlasModel):
    """Full note row returned by GET / POST / PATCH / index endpoints."""
    id: UUID
    user_id: str
    project_id: UUID
    knowledge_node_id: UUID | None = None
    title: str
    body_markdown: str
    mention_entity_ids: list[UUID] = Field(default_factory=list)
    indexed_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class NoteListItem(AtlasModel):
    """Row in GET /api/v1/notes list response — light fields only."""
    id: UUID
    title: str
    updated_at: datetime
    indexed_at: datetime | None = None


class CreateNoteRequest(AtlasModel):
    model_config = {"strict": False}
    project_id: UUID
    title: str = "Untitled"
    body_markdown: str = ""


class PatchNoteRequest(AtlasModel):
    model_config = {"strict": False}
    title: str | None = None
    body_markdown: str | None = None
    mention_entity_ids: list[UUID] | None = None
