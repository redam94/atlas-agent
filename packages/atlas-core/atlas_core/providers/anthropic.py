"""Anthropic provider — wraps anthropic.AsyncAnthropic.messages.stream."""

import json
import time
from collections.abc import AsyncIterator
from typing import Any

import anthropic

from atlas_core.models.llm import ModelEvent, ModelEventType, ModelSpec
from atlas_core.providers.base import BaseModel


class AnthropicProvider(BaseModel):
    """Streaming Anthropic provider.

    The ``_client`` keyword is for tests — pass a stub to bypass the SDK.
    """

    def __init__(
        self,
        api_key: str,
        model_id: str,
        *,
        context_window: int = 200_000,
        supports_tools: bool = True,
        _client: Any | None = None,
    ) -> None:
        self.spec = ModelSpec(
            provider="anthropic",
            model_id=model_id,
            context_window=context_window,
            supports_tools=supports_tools,
            supports_streaming=True,
        )
        self._client = _client or anthropic.AsyncAnthropic(api_key=api_key)

    async def stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
    ) -> AsyncIterator[ModelEvent]:
        # Anthropic requires a separate `system` arg, not a system message.
        system, user_messages = _split_system(messages)

        kwargs: dict[str, Any] = {
            "model": self.spec.model_id,
            "messages": user_messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if system:
            kwargs["system"] = system
        if tools:
            kwargs["tools"] = tools

        started = time.monotonic()
        input_tokens = 0
        output_tokens = 0
        tool_use_blocks: dict[int, dict[str, Any]] = {}

        try:
            async with self._client.messages.stream(**kwargs) as stream:
                async for event in stream:
                    et = getattr(event, "type", None)
                    if et == "content_block_start":
                        idx = getattr(event, "index", None)
                        block = getattr(event, "content_block", None)
                        if block is not None and getattr(block, "type", None) == "tool_use" and idx is not None:
                            tool_use_blocks[idx] = {
                                "id": getattr(block, "id", ""),
                                "name": getattr(block, "name", ""),
                                "input_json": "",
                            }
                    elif et == "content_block_delta":
                        idx = getattr(event, "index", None)
                        delta = getattr(event, "delta", None)
                        delta_type = getattr(delta, "type", None) if delta is not None else None
                        if delta_type == "text_delta" and delta is not None:
                            yield ModelEvent(
                                type=ModelEventType.TOKEN,
                                data={"text": delta.text},
                            )
                        elif delta_type == "input_json_delta" and idx in tool_use_blocks and delta is not None:
                            tool_use_blocks[idx]["input_json"] += getattr(delta, "partial_json", "")
                    elif et == "content_block_stop":
                        idx = getattr(event, "index", None)
                        if idx in tool_use_blocks:
                            buf = tool_use_blocks.pop(idx)
                            try:
                                args = json.loads(buf["input_json"]) if buf["input_json"] else {}
                            except json.JSONDecodeError:
                                args = {}
                            yield ModelEvent(
                                type=ModelEventType.TOOL_CALL,
                                data={"id": buf["id"], "tool": buf["name"], "args": args},
                            )
                    elif et == "message_start":
                        msg = getattr(event, "message", None)
                        if msg is not None:
                            msg_usage = getattr(msg, "usage", None)
                            if msg_usage is not None:
                                new_input = getattr(msg_usage, "input_tokens", None)
                                if new_input is not None:
                                    input_tokens = new_input
                    elif et in ("message_delta", "message_stop"):
                        usage = getattr(event, "usage", None)
                        if usage is not None:
                            # output_tokens is reliably present on message_delta
                            new_output = getattr(usage, "output_tokens", None)
                            if new_output is not None:
                                output_tokens = new_output
                            # input_tokens is Optional on message_delta — only update if present
                            new_input = getattr(usage, "input_tokens", None)
                            if new_input is not None:
                                input_tokens = new_input
        except Exception as e:
            yield ModelEvent(
                type=ModelEventType.ERROR,
                data={"code": "anthropic_error", "message": str(e)},
            )
            return

        latency_ms = int((time.monotonic() - started) * 1000)
        yield ModelEvent(
            type=ModelEventType.DONE,
            data={
                "usage": {
                    "provider": "anthropic",
                    "model_id": self.spec.model_id,
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                    "latency_ms": latency_ms,
                }
            },
        )


def _split_system(
    messages: list[dict[str, Any]],
) -> tuple[str | None, list[dict[str, Any]]]:
    """Anthropic's API takes ``system`` as a top-level arg, not a message role."""
    system_parts: list[str] = []
    rest: list[dict[str, Any]] = []
    for m in messages:
        if m.get("role") == "system":
            system_parts.append(m.get("content", ""))
        else:
            rest.append(m)
    return ("\n\n".join(system_parts) if system_parts else None), rest
