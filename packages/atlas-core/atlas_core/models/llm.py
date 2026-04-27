"""Provider-agnostic LLM types.

The ``BaseModel`` provider ABC streams ``ModelEvent`` instances. Each
provider implementation translates its native chunks into these types.
``ToolCall`` / ``ToolResult`` / ``ToolSchema`` exist for the Phase 3
plugin layer; Phase 1 providers never emit tool events but the type
plumbing is in place.
"""
from enum import StrEnum
from typing import Any

from pydantic import Field

from atlas_core.models.base import AtlasModel


class ModelSpec(AtlasModel):
    """Describes one LLM choice surfaced via ``GET /api/v1/models``."""

    provider: str  # "anthropic" | "lmstudio" | future
    model_id: str  # e.g. "claude-sonnet-4-6"
    context_window: int = Field(ge=1)
    supports_tools: bool
    supports_streaming: bool = True


class ModelUsage(AtlasModel):
    """Token + cost metrics for one model invocation."""

    provider: str
    model_id: str
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    latency_ms: int | None = Field(default=None, ge=0)


class ModelEventType(StrEnum):
    TOKEN = "token"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    DONE = "done"
    ERROR = "error"


class ModelEvent(AtlasModel):
    """One normalized event in a streaming model response.

    ``data`` shape per type:
    - ``token``: ``{"text": "..."}``
    - ``tool_call``: serialized ``ToolCall``
    - ``tool_result``: serialized ``ToolResult``
    - ``done``: ``{"usage": ...}``
    - ``error``: ``{"code": "...", "message": "..."}``
    """

    type: ModelEventType
    data: dict[str, Any] = Field(default_factory=dict)


class ToolSchema(AtlasModel):
    """JSON-Schema description of a tool, exposed to the model."""

    name: str  # e.g. "github.search_code"
    description: str
    parameters: dict[str, Any]  # full JSON Schema
    plugin: str
    requires_confirmation: bool = False


class ToolCall(AtlasModel):
    """A tool invocation requested by the model (Phase 1: never emitted)."""

    id: str
    tool: str
    args: dict[str, Any]


class ToolResult(AtlasModel):
    """The result of executing a ``ToolCall``."""

    call_id: str
    tool: str
    result: Any  # tool-specific shape
    error: str | None = None
