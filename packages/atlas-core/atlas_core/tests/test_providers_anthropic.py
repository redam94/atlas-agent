"""Tests for AnthropicProvider — uses a fake AsyncAnthropic transport."""

from typing import Any
from unittest.mock import MagicMock

import pytest

from atlas_core.models.llm import ModelEventType
from atlas_core.providers.anthropic import AnthropicProvider


class _FakeAnthropicStream:
    """Mimics ``anthropic.AsyncMessageStreamManager`` for tests."""

    def __init__(self, events: list[Any]) -> None:
        self._events = events

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def __aiter__(self):
        for e in self._events:
            yield e


def _text_delta(text: str):
    """Emit an event matching anthropic SDK's RawContentBlockDeltaEvent shape (text)."""
    ev = MagicMock()
    ev.type = "content_block_delta"
    ev.delta = MagicMock()
    ev.delta.type = "text_delta"
    ev.delta.text = text
    return ev


def _message_start(input_tokens: int):
    """Emit an event matching Anthropic SDK's RawMessageStartEvent shape."""
    ev = MagicMock()
    ev.type = "message_start"
    ev.message = MagicMock()
    ev.message.usage = MagicMock()
    ev.message.usage.input_tokens = input_tokens
    ev.message.usage.output_tokens = 0
    return ev


def _message_delta(input_tokens: int | None, output_tokens: int):
    """Emit an event matching anthropic SDK's MessageDeltaEvent shape (carries usage)."""
    ev = MagicMock()
    ev.type = "message_delta"
    ev.usage = MagicMock()
    ev.usage.input_tokens = input_tokens  # Optional in real SDK
    ev.usage.output_tokens = output_tokens
    return ev


@pytest.fixture
def fake_client():
    client = MagicMock()
    client.messages = MagicMock()
    return client


async def test_anthropic_provider_streams_tokens_and_emits_done(fake_client):
    fake_client.messages.stream = MagicMock(
        return_value=_FakeAnthropicStream(
            [
                _message_start(input_tokens=12),
                _text_delta("hello"),
                _text_delta(" world"),
                _message_delta(input_tokens=None, output_tokens=2),
            ]
        )
    )

    provider = AnthropicProvider(
        api_key="sk-test",
        model_id="claude-sonnet-4-6",
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
    assert events[0].data["text"] == "hello"
    assert events[-1].data["usage"]["input_tokens"] == 12
    assert events[-1].data["usage"]["output_tokens"] == 2


async def test_anthropic_provider_emits_error_on_exception(fake_client):
    def _raise(*args, **kwargs):
        raise RuntimeError("network down")

    fake_client.messages.stream = _raise

    provider = AnthropicProvider(
        api_key="sk-test",
        model_id="claude-sonnet-4-6",
        _client=fake_client,
    )

    events = []
    async for ev in provider.stream(messages=[{"role": "user", "content": "hi"}]):
        events.append(ev)

    assert len(events) == 1
    assert events[0].type == ModelEventType.ERROR
    assert "network down" in events[0].data["message"]


def test_anthropic_provider_spec():
    provider = AnthropicProvider(
        api_key="sk-test",
        model_id="claude-sonnet-4-6",
        context_window=200_000,
    )
    assert provider.spec.provider == "anthropic"
    assert provider.spec.model_id == "claude-sonnet-4-6"
    assert provider.spec.context_window == 200_000
