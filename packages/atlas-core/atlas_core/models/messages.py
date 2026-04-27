"""Pydantic models for messages and the WebSocket chat protocol."""
from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import Field

from atlas_core.models.base import AtlasModel, AtlasRequestModel
from atlas_core.models.sessions import MessageRole


class Message(AtlasModel):
    """A single conversation turn persisted in Postgres."""

    id: UUID
    user_id: str
    session_id: UUID
    role: MessageRole
    content: str
    tool_calls: list[dict[str, Any]] | None = None
    rag_context: list[dict[str, Any]] | None = None
    model: str | None = None
    token_count: int | None = None
    created_at: datetime


class ChatRequest(AtlasRequestModel):
    """Payload of a ``chat.message`` WebSocket event."""

    text: str = Field(min_length=1, max_length=32_000)
    project_id: UUID
    model_override: str | None = Field(default=None, pattern=r"^[a-zA-Z0-9._\-:/]+$")
    rag_enabled: bool = True  # Phase 1 ignores this; Plan 5 wires it in
    top_k_context: int = Field(default=8, ge=1, le=32)
    temperature: float = Field(default=0.7, ge=0.0, le=2.0)


class StreamEventType(StrEnum):
    """Server → client WebSocket event names."""

    TOKEN = "chat.token"
    TOOL_CALL = "chat.tool_use"
    TOOL_RESULT = "chat.tool_result"
    RAG_CONTEXT = "rag.context"
    DONE = "chat.done"
    ERROR = "chat.error"


class StreamEvent(AtlasModel):
    """One server → client WebSocket message.

    Phase 1: ``sequence`` is a monotonic per-connection counter so the
    client can detect drops or out-of-order arrival.
    """

    type: StreamEventType
    payload: dict[str, Any]
    sequence: int = Field(ge=0)
