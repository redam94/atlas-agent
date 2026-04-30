"""Tests for AnthropicProvider's tool_use event emission."""

from typing import Any
from unittest.mock import MagicMock

import pytest
from atlas_core.models.llm import ModelEventType
from atlas_core.providers.anthropic import AnthropicProvider


class _FakeAnthropicStream:
    """Yields scripted Anthropic streaming events.

    The real SDK emits content_block_start, content_block_delta (with
    input_json_delta), and content_block_stop for tool_use blocks. We
    simulate those with simple namedtuple-shaped objects.
    """

    def __init__(self, events: list[dict[str, Any]]) -> None:
        self._events = events

    async def __aenter__(self) -> "_FakeAnthropicStream":
        return self

    async def __aexit__(self, *_) -> None:
        pass

    def __aiter__(self):
        return self._gen()

    async def _gen(self):
        for e in self._events:
            yield _dict_to_obj(e)


def _dict_to_obj(d: dict[str, Any]) -> Any:
    """Recursively convert dicts to objects with attribute access."""
    if isinstance(d, dict):
        m = MagicMock()
        for k, v in d.items():
            setattr(m, k, _dict_to_obj(v))
        return m
    if isinstance(d, list):
        return [_dict_to_obj(x) for x in d]
    return d


@pytest.fixture
def fake_client():
    client = MagicMock()
    return client


@pytest.mark.asyncio
async def test_emits_tool_call_event_when_stream_contains_tool_use(fake_client):
    # Scripted event sequence: a tool_use content block with name "fake.echo"
    # and input streamed via input_json_delta.
    events = [
        {"type": "message_start", "message": {"usage": {"input_tokens": 10}}},
        {"type": "content_block_start", "index": 0,
         "content_block": {"type": "tool_use", "id": "tu_01", "name": "fake.echo", "input": {}}},
        {"type": "content_block_delta", "index": 0,
         "delta": {"type": "input_json_delta", "partial_json": '{"text":"hi'}},
        {"type": "content_block_delta", "index": 0,
         "delta": {"type": "input_json_delta", "partial_json": '"}'}},
        {"type": "content_block_stop", "index": 0},
        {"type": "message_delta", "usage": {"output_tokens": 5}},
        {"type": "message_stop"},
    ]
    fake_client.messages.stream = MagicMock(return_value=_FakeAnthropicStream(events))

    provider = AnthropicProvider(api_key="x", model_id="claude-sonnet-4-6", _client=fake_client)
    out = []
    async for ev in provider.stream(messages=[{"role": "user", "content": "hi"}]):
        out.append(ev)

    tool_calls = [e for e in out if e.type == ModelEventType.TOOL_CALL]
    assert len(tool_calls) == 1
    assert tool_calls[0].data["id"] == "tu_01"
    assert tool_calls[0].data["tool"] == "fake.echo"
    assert tool_calls[0].data["args"] == {"text": "hi"}


@pytest.mark.asyncio
async def test_text_stream_unchanged_with_no_tool_use(fake_client):
    events = [
        {"type": "message_start", "message": {"usage": {"input_tokens": 5}}},
        {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "hello"}},
        {"type": "message_delta", "usage": {"output_tokens": 1}},
        {"type": "message_stop"},
    ]
    fake_client.messages.stream = MagicMock(return_value=_FakeAnthropicStream(events))

    provider = AnthropicProvider(api_key="x", model_id="claude-sonnet-4-6", _client=fake_client)
    out = []
    async for ev in provider.stream(messages=[{"role": "user", "content": "hi"}]):
        out.append(ev)

    tool_calls = [e for e in out if e.type == ModelEventType.TOOL_CALL]
    assert tool_calls == []
    tokens = [e for e in out if e.type == ModelEventType.TOKEN]
    assert tokens[0].data["text"] == "hello"


@pytest.mark.asyncio
async def test_emits_tool_call_with_empty_args_when_no_input_deltas(fake_client):
    # Tool use block with NO input_json_delta events — model decides on tool with no args.
    events = [
        {"type": "message_start", "message": {"usage": {"input_tokens": 10}}},
        {"type": "content_block_start", "index": 0,
         "content_block": {"type": "tool_use", "id": "tu_02", "name": "fake.noop", "input": {}}},
        {"type": "content_block_stop", "index": 0},
        {"type": "message_delta", "usage": {"output_tokens": 5}},
        {"type": "message_stop"},
    ]
    fake_client.messages.stream = MagicMock(return_value=_FakeAnthropicStream(events))

    provider = AnthropicProvider(api_key="x", model_id="claude-sonnet-4-6", _client=fake_client)
    out = []
    async for ev in provider.stream(messages=[{"role": "user", "content": "hi"}]):
        out.append(ev)

    tool_calls = [e for e in out if e.type == ModelEventType.TOOL_CALL]
    assert len(tool_calls) == 1
    assert tool_calls[0].data["id"] == "tu_02"
    assert tool_calls[0].data["tool"] == "fake.noop"
    assert tool_calls[0].data["args"] == {}


@pytest.mark.asyncio
async def test_emits_tool_call_with_empty_args_on_malformed_json(fake_client):
    # Tool use block with malformed/partial JSON — should default to {}.
    events = [
        {"type": "message_start", "message": {"usage": {"input_tokens": 10}}},
        {"type": "content_block_start", "index": 0,
         "content_block": {"type": "tool_use", "id": "tu_03", "name": "fake.broken", "input": {}}},
        {"type": "content_block_delta", "index": 0,
         "delta": {"type": "input_json_delta", "partial_json": '{"foo":'}},
        {"type": "content_block_stop", "index": 0},
        {"type": "message_delta", "usage": {"output_tokens": 5}},
        {"type": "message_stop"},
    ]
    fake_client.messages.stream = MagicMock(return_value=_FakeAnthropicStream(events))

    provider = AnthropicProvider(api_key="x", model_id="claude-sonnet-4-6", _client=fake_client)
    out = []
    async for ev in provider.stream(messages=[{"role": "user", "content": "hi"}]):
        out.append(ev)

    tool_calls = [e for e in out if e.type == ModelEventType.TOOL_CALL]
    assert len(tool_calls) == 1
    assert tool_calls[0].data["id"] == "tu_03"
    assert tool_calls[0].data["tool"] == "fake.broken"
    assert tool_calls[0].data["args"] == {}


@pytest.mark.asyncio
async def test_emits_two_tool_call_events_for_two_tool_use_blocks_in_same_message(fake_client):
    # Two tool_use blocks at index 0 and 1, each with start → input_json_delta → stop.
    events = [
        {"type": "message_start", "message": {"usage": {"input_tokens": 10}}},
        # First tool_use block
        {"type": "content_block_start", "index": 0,
         "content_block": {"type": "tool_use", "id": "tu_04", "name": "tool.first", "input": {}}},
        {"type": "content_block_delta", "index": 0,
         "delta": {"type": "input_json_delta", "partial_json": '{"x":1'}},
        {"type": "content_block_delta", "index": 0,
         "delta": {"type": "input_json_delta", "partial_json": '}'}},
        {"type": "content_block_stop", "index": 0},
        # Second tool_use block
        {"type": "content_block_start", "index": 1,
         "content_block": {"type": "tool_use", "id": "tu_05", "name": "tool.second", "input": {}}},
        {"type": "content_block_delta", "index": 1,
         "delta": {"type": "input_json_delta", "partial_json": '{"y":2'}},
        {"type": "content_block_delta", "index": 1,
         "delta": {"type": "input_json_delta", "partial_json": '}'}},
        {"type": "content_block_stop", "index": 1},
        {"type": "message_delta", "usage": {"output_tokens": 5}},
        {"type": "message_stop"},
    ]
    fake_client.messages.stream = MagicMock(return_value=_FakeAnthropicStream(events))

    provider = AnthropicProvider(api_key="x", model_id="claude-sonnet-4-6", _client=fake_client)
    out = []
    async for ev in provider.stream(messages=[{"role": "user", "content": "hi"}]):
        out.append(ev)

    tool_calls = [e for e in out if e.type == ModelEventType.TOOL_CALL]
    assert len(tool_calls) == 2
    # First tool call
    assert tool_calls[0].data["id"] == "tu_04"
    assert tool_calls[0].data["tool"] == "tool.first"
    assert tool_calls[0].data["args"] == {"x": 1}
    # Second tool call
    assert tool_calls[1].data["id"] == "tu_05"
    assert tool_calls[1].data["tool"] == "tool.second"
    assert tool_calls[1].data["args"] == {"y": 2}
