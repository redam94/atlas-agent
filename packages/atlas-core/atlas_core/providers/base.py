"""LLM provider ABC and shared error type."""

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from typing import Any

from atlas_core.models.llm import ModelEvent, ModelSpec


class ProviderError(Exception):
    """Wraps any provider-side failure (network, auth, rate limit, ...)."""

    def __init__(self, code: str, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable


class BaseModel(ABC):
    """Abstract async streaming LLM provider.

    Concrete implementations live alongside this file. Plan 3 ships
    ``AnthropicProvider`` and ``LMStudioProvider``; later phases add more.
    """

    spec: ModelSpec  # set by subclass __init__

    @abstractmethod
    async def stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
    ) -> AsyncIterator[ModelEvent]:
        """Yield normalized ``ModelEvent`` instances until the response ends.

        Always concludes with one ``ModelEventType.DONE`` event whose ``data["usage"]``
        carries the input/output token counts. On failure, yields one ``ModelEventType.ERROR``
        and stops (rather than raising — the WS handler catches via the event type).
        """
        if False:  # pragma: no cover — ABC contract
            yield  # type: ignore[unreachable]
