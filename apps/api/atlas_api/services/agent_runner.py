"""Extracted Anthropic tool-use loop.

run_tool_loop:      async generator → AgentEvent stream
run_turn_collected: drains the generator → final text string

The ``interactive`` flag is written to a ContextVar in atlas_plugins.context
so plugins can read it without it passing through the call stack.
"""

from __future__ import annotations

import json
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

import structlog
from atlas_core.models.llm import ModelEventType, ToolResult, ToolSchema
from atlas_plugins import PluginRegistry
from atlas_plugins.context import reset_interactive, set_interactive

log = structlog.get_logger("atlas.api.agent_runner")

MAX_TOOL_TURNS = 10


class AgentEventType(StrEnum):
    TEXT_DELTA = "text_delta"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    DONE = "done"
    ERROR = "error"


@dataclass
class AgentEvent:
    type: AgentEventType
    data: dict[str, Any] = field(default_factory=dict)


def _encode_tool_name(name: str) -> str:
    return name.replace(".", "__")


def _decode_tool_name(name: str) -> str:
    return name.replace("__", ".")


def to_anthropic_tool(s: ToolSchema) -> dict[str, Any]:
    return {
        "name": _encode_tool_name(s.name),
        "description": s.description,
        "input_schema": s.parameters,
    }


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


async def run_tool_loop(
    *,
    provider: Any,
    messages: list[dict[str, Any]],
    tools_payload: list[dict[str, Any]] | None,
    plugin_registry: PluginRegistry | None,
    interactive: bool = True,
    temperature: float = 1.0,
) -> AsyncIterator[AgentEvent]:
    """Async generator yielding AgentEvents for the full tool-use loop."""
    token = set_interactive(interactive)
    try:
        assistant_text_parts: list[str] = []
        all_tool_calls: list[dict[str, Any]] = []
        tool_turn = 0
        started = time.monotonic()
        usage: dict[str, Any] = {}
        error_occurred = False

        while True:
            pending_tool_calls: list[dict[str, Any]] = []

            async for event in provider.stream(
                messages=messages,
                tools=tools_payload,
                temperature=temperature,
            ):
                if event.type == ModelEventType.TOKEN:
                    text = event.data.get("text", "")
                    assistant_text_parts.append(text)
                    yield AgentEvent(AgentEventType.TEXT_DELTA, {"text": text})
                elif event.type == ModelEventType.TOOL_CALL:
                    call = dict(event.data)
                    call["tool"] = _decode_tool_name(call["tool"])
                    yield AgentEvent(
                        AgentEventType.TOOL_CALL,
                        {"id": call["id"], "tool": call["tool"], "started_at": _now_iso()},
                    )
                    pending_tool_calls.append(call)
                elif event.type == ModelEventType.ERROR:
                    yield AgentEvent(AgentEventType.ERROR, event.data)
                    error_occurred = True
                    break
                elif event.type == ModelEventType.DONE:
                    usage = event.data.get("usage", {})

            if error_occurred:
                break

            if not pending_tool_calls:
                break

            tool_turn += 1
            tool_results = []
            for call in pending_tool_calls:
                call_started = time.monotonic()
                if plugin_registry is None:
                    result = ToolResult(
                        call_id=call["id"],
                        tool=call["tool"],
                        result=None,
                        error="no plugin registry available",
                    )
                else:
                    result = await plugin_registry.invoke(
                        call["tool"], call["args"], call_id=call["id"]
                    )
                duration_ms = int((time.monotonic() - call_started) * 1000)
                ok = result.error is None
                yield AgentEvent(
                    AgentEventType.TOOL_RESULT,
                    {
                        "tool": call["tool"],
                        "call_id": call["id"],
                        "ok": ok,
                        "duration_ms": duration_ms,
                    },
                )
                tool_results.append(result)
                all_tool_calls.append(
                    {
                        "call_id": call["id"],
                        "tool": call["tool"],
                        "args": call["args"],
                        "result": result.result if ok else None,
                        "error": result.error,
                    }
                )

            messages.append(
                {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "tool_use",
                            "id": c["id"],
                            "name": _encode_tool_name(c["tool"]),
                            "input": c["args"],
                        }
                        for c in pending_tool_calls
                    ],
                }
            )
            messages.append(
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": r.call_id,
                            "content": (
                                json.dumps(r.result) if r.error is None else f"Error: {r.error}"
                            ),
                            "is_error": r.error is not None,
                        }
                        for r in tool_results
                    ],
                }
            )

            if tool_turn >= MAX_TOOL_TURNS:
                tools_payload = None
                messages.append(
                    {
                        "role": "user",
                        "content": "Tool call limit reached; respond to the user without using tools.",
                    }
                )

        if not error_occurred:
            latency_ms = int((time.monotonic() - started) * 1000)
            yield AgentEvent(
                AgentEventType.DONE,
                {
                    "text": "".join(assistant_text_parts),
                    "tool_calls": all_tool_calls,
                    "usage": usage,
                    "latency_ms": latency_ms,
                },
            )
    finally:
        reset_interactive(token)


async def run_turn_collected(
    *,
    provider: Any,
    messages: list[dict[str, Any]],
    tools_payload: list[dict[str, Any]] | None,
    plugin_registry: PluginRegistry | None,
    interactive: bool = False,
    temperature: float = 1.0,
) -> str:
    """Drain run_tool_loop; return the final assembled text."""
    async for event in run_tool_loop(
        provider=provider,
        messages=messages,
        tools_payload=tools_payload,
        plugin_registry=plugin_registry,
        interactive=interactive,
        temperature=temperature,
    ):
        if event.type == AgentEventType.DONE:
            return event.data.get("text", "")
        if event.type == AgentEventType.ERROR:
            raise RuntimeError(event.data.get("message", "agent error"))
    return ""
