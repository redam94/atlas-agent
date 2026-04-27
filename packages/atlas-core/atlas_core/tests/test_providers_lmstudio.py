"""Tests for LMStudioProvider — uses a fake AsyncOpenAI transport."""
from typing import Any
from unittest.mock import MagicMock

import pytest

from atlas_core.models.llm import ModelEventType
from atlas_core.providers.lmstudio import LMStudioProvider


class _FakeOpenAIChunk:
    """Mimics openai.types.chat.ChatCompletionChunk shape."""

    def __init__(self, content: str | None = None, finish_reason: str | None = None,
                 prompt_tokens: int = 0, completion_tokens: int = 0):
        delta = MagicMock()
        delta.content = content
        choice = MagicMock()
        choice.delta = delta
        choice.finish_reason = finish_reason
        self.choices = [choice]
        if prompt_tokens or completion_tokens:
            usage = MagicMock()
            usage.prompt_tokens = prompt_tokens
            usage.completion_tokens = completion_tokens
            self.usage = usage
        else:
            self.usage = None


class _FakeOpenAIStream:
    def __init__(self, chunks: list[Any]) -> None:
        self._chunks = chunks

    def __aiter__(self):
        return self._gen()

    async def _gen(self):
        for c in self._chunks:
            yield c


@pytest.fixture
def fake_client():
    client = MagicMock()
    client.chat = MagicMock()
    client.chat.completions = MagicMock()
    return client


async def test_lmstudio_provider_streams_tokens_and_emits_done(fake_client):
    async def _create_stream(*args, **kwargs):
        return _FakeOpenAIStream(
            [
                _FakeOpenAIChunk(content="hello"),
                _FakeOpenAIChunk(content=" world"),
                _FakeOpenAIChunk(content=None, finish_reason="stop",
                                 prompt_tokens=11, completion_tokens=2),
            ]
        )

    fake_client.chat.completions.create = _create_stream

    provider = LMStudioProvider(
        base_url="http://x:1234/v1",
        model_id="gemma-3-12b",
        _client=fake_client,
    )

    events = []
    async for ev in provider.stream(
        messages=[{"role": "user", "content": "hi"}],
    ):
        events.append(ev)

    types = [e.type for e in events]
    assert types == [
        ModelEventType.TOKEN,
        ModelEventType.TOKEN,
        ModelEventType.DONE,
    ]
    assert events[-1].data["usage"]["input_tokens"] == 11
    assert events[-1].data["usage"]["output_tokens"] == 2


async def test_lmstudio_provider_emits_error_on_exception(fake_client):
    async def _raise(*args, **kwargs):
        raise RuntimeError("connection refused")

    fake_client.chat.completions.create = _raise

    provider = LMStudioProvider(
        base_url="http://x:1234/v1",
        model_id="gemma-3-12b",
        _client=fake_client,
    )

    events = []
    async for ev in provider.stream(messages=[{"role": "user", "content": "hi"}]):
        events.append(ev)

    assert len(events) == 1
    assert events[0].type == ModelEventType.ERROR


def test_lmstudio_provider_spec():
    provider = LMStudioProvider(
        base_url="http://x:1234/v1",
        model_id="gemma-3-12b",
    )
    assert provider.spec.provider == "lmstudio"
    assert provider.spec.model_id == "gemma-3-12b"
