"""FakeProvider — used by tests to exercise the WS layer without real API calls.

Importable from ``atlas_core.providers`` so test files don't need to reach
into private modules.
"""

from collections.abc import AsyncIterator
from typing import Any

from atlas_core.models.llm import ModelEvent, ModelEventType, ModelSpec
from atlas_core.providers.base import BaseModel


class FakeProvider(BaseModel):
    """Streams a fixed sequence of token chunks, then emits ``done`` with usage."""

    def __init__(
        self,
        *,
        model_id: str = "fake-1",
        token_chunks: list[str] | None = None,
        error_on_call: bool = False,
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

    async def stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
    ) -> AsyncIterator[ModelEvent]:
        if self.error_on_call:
            yield ModelEvent(
                type=ModelEventType.ERROR,
                data={"code": "fake_error", "message": "configured to fail"},
            )
            return

        output_tokens = 0
        for chunk in self.token_chunks:
            output_tokens += len(chunk.split()) or 1
            yield ModelEvent(type=ModelEventType.TOKEN, data={"text": chunk})

        # Approximate input tokens from message content lengths
        input_tokens = sum(len(m.get("content", "").split()) for m in messages)

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
