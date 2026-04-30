"""FakeProvider — used by tests to exercise the WS layer without real API calls.

Importable from ``atlas_core.providers`` so test files don't need to reach
into private modules.
"""

from collections.abc import AsyncIterator
from typing import Any

from atlas_core.models.llm import ModelEvent, ModelEventType, ModelSpec
from atlas_core.providers.base import BaseModel


def _approx_tokens(content: Any) -> int:
    """Approximate token count from message content.

    content may be:
    - str  (normal user/assistant/system turns)
    - list (tool_use / tool_result blocks with dicts)
    """
    if isinstance(content, str):
        return len(content.split()) or 1
    if isinstance(content, list):
        total = 0
        for block in content:
            if isinstance(block, dict):
                # Count text/content fields if present
                for key in ("text", "content", "input"):
                    val = block.get(key, "")
                    if isinstance(val, str):
                        total += len(val.split()) or 1
                    elif isinstance(val, dict):
                        total += 2  # rough estimate for JSON structures
            else:
                total += 1
        return max(total, 1)
    return 1


class FakeProvider(BaseModel):
    """Streams a fixed sequence of token chunks, then emits ``done`` with usage.

    Can also script TOOL_CALL events via ``scripted_turns``.
    """

    def __init__(
        self,
        *,
        model_id: str = "fake-1",
        token_chunks: list[str] | None = None,
        error_on_call: bool = False,
        scripted_turns: list[dict] | None = None,
    ) -> None:
        self.spec = ModelSpec(
            provider="fake",
            model_id=model_id,
            context_window=8192,
            supports_tools=False,
            supports_streaming=True,
        )
        self.token_chunks = token_chunks or ["hello", " world"]
        self.error_on_call = error_on_call
        self.scripted_turns = scripted_turns
        self._turn_index = 0
        self.stream_calls: list[dict] = []

    async def stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
    ) -> AsyncIterator[ModelEvent]:
        self.stream_calls.append({"tools": tools, "messages": list(messages)})
        if self.error_on_call:
            yield ModelEvent(
                type=ModelEventType.ERROR,
                data={"code": "fake_error", "message": "configured to fail"},
            )
            return

        # If scripted_turns is configured, use it
        if self.scripted_turns is not None:
            if self._turn_index < len(self.scripted_turns):
                turn = self.scripted_turns[self._turn_index]
                self._turn_index += 1

                output_tokens = 0

                # Emit tool_calls if present
                if "tool_calls" in turn:
                    for tool_call in turn["tool_calls"]:
                        yield ModelEvent(
                            type=ModelEventType.TOOL_CALL,
                            data=tool_call,
                        )

                # Emit text tokens if present
                if "text" in turn:
                    text = turn["text"]
                    output_tokens += len(text.split()) or 1
                    yield ModelEvent(type=ModelEventType.TOKEN, data={"text": text})

                # Approximate input tokens from message content lengths
                input_tokens = sum(_approx_tokens(m.get("content", "")) for m in messages)

                yield ModelEvent(
                    type=ModelEventType.DONE,
                    data={
                        "usage": {
                            "provider": self.spec.provider,
                            "model_id": self.spec.model_id,
                            "input_tokens": input_tokens,
                            "output_tokens": output_tokens,
                            "latency_ms": 0,
                        }
                    },
                )
            else:
                # Exhausted turns; just return DONE
                input_tokens = sum(_approx_tokens(m.get("content", "")) for m in messages)
                yield ModelEvent(
                    type=ModelEventType.DONE,
                    data={
                        "usage": {
                            "provider": self.spec.provider,
                            "model_id": self.spec.model_id,
                            "input_tokens": input_tokens,
                            "output_tokens": 0,
                            "latency_ms": 0,
                        }
                    },
                )
        else:
            # Legacy behavior with token_chunks
            output_tokens = 0
            for chunk in self.token_chunks:
                output_tokens += len(chunk.split()) or 1
                yield ModelEvent(type=ModelEventType.TOKEN, data={"text": chunk})

            # Approximate input tokens from message content lengths
            input_tokens = sum(_approx_tokens(m.get("content", "")) for m in messages)

            yield ModelEvent(
                type=ModelEventType.DONE,
                data={
                    "usage": {
                        "provider": self.spec.provider,
                        "model_id": self.spec.model_id,
                        "input_tokens": input_tokens,
                        "output_tokens": output_tokens,
                        "latency_ms": 0,
                    }
                },
            )
