"""LM Studio provider — uses openai.AsyncOpenAI against the LM Studio endpoint."""

import time
from collections.abc import AsyncIterator
from typing import Any

import openai

from atlas_core.models.llm import ModelEvent, ModelEventType, ModelSpec
from atlas_core.providers.base import BaseModel


class LMStudioProvider(BaseModel):
    """Streaming OpenAI-compatible provider pointed at LM Studio.

    LM Studio's `/v1/chat/completions` endpoint is wire-compatible with
    OpenAI; we just point the SDK at the local URL.
    """

    def __init__(
        self,
        base_url: str,
        model_id: str,
        *,
        context_window: int = 8192,
        supports_tools: bool = False,  # local models vary; default off
        api_key: str = "lm-studio",  # ignored by LM Studio but required by SDK
        _client: Any | None = None,
    ) -> None:
        self.spec = ModelSpec(
            provider="lmstudio",
            model_id=model_id,
            context_window=context_window,
            supports_tools=supports_tools,
            supports_streaming=True,
        )
        self._client = _client or openai.AsyncOpenAI(base_url=base_url, api_key=api_key)

    async def stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
    ) -> AsyncIterator[ModelEvent]:
        kwargs: dict[str, Any] = {
            "model": self.spec.model_id,
            "messages": messages,
            "stream": True,
            "stream_options": {"include_usage": True},
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if tools and self.spec.supports_tools:
            kwargs["tools"] = tools

        started = time.monotonic()
        input_tokens = 0
        output_tokens = 0

        try:
            stream = await self._client.chat.completions.create(**kwargs)
            async for chunk in stream:
                if not chunk.choices:
                    # The final chunk in include_usage mode has empty choices but a usage block
                    if getattr(chunk, "usage", None) is not None:
                        input_tokens = chunk.usage.prompt_tokens or 0
                        output_tokens = chunk.usage.completion_tokens or 0
                    continue
                choice = chunk.choices[0]
                delta = getattr(choice, "delta", None)
                if delta is not None and getattr(delta, "content", None):
                    yield ModelEvent(
                        type=ModelEventType.TOKEN,
                        data={"text": delta.content},
                    )
                if getattr(chunk, "usage", None) is not None:
                    input_tokens = chunk.usage.prompt_tokens or input_tokens
                    output_tokens = chunk.usage.completion_tokens or output_tokens
        except Exception as e:
            yield ModelEvent(
                type=ModelEventType.ERROR,
                data={"code": "lmstudio_error", "message": str(e)},
            )
            return

        latency_ms = int((time.monotonic() - started) * 1000)
        yield ModelEvent(
            type=ModelEventType.DONE,
            data={
                "usage": {
                    "provider": "lmstudio",
                    "model_id": self.spec.model_id,
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                    "latency_ms": latency_ms,
                }
            },
        )
