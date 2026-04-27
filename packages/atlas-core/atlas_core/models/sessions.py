"""Pydantic models for chat sessions."""

from datetime import datetime
from enum import StrEnum
from uuid import UUID

from atlas_core.models.base import AtlasModel, AtlasRequestModel


class MessageRole(StrEnum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


class Session(AtlasModel):
    """A chat session — one WebSocket connection, one project."""

    id: UUID
    user_id: str
    project_id: UUID
    model: str | None = None
    created_at: datetime
    last_active_at: datetime


class SessionCreate(AtlasRequestModel):
    """Body to create a session via REST (Phase 1: also created on WS connect)."""

    project_id: UUID
    model: str | None = None
