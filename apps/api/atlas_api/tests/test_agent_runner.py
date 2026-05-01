"""Tests for the extracted agent runner."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest
from atlas_core.providers._fake import FakeProvider
from atlas_plugins import FakePlugin, HealthStatus, PluginRegistry
from atlas_plugins.context import is_interactive

from atlas_api.services.agent_runner import (
    AgentEvent,
    AgentEventType,
    run_tool_loop,
    run_turn_collected,
)


@pytest.fixture
def fake_registry():
    plugin = FakePlugin(credentials=AsyncMock())
    reg = PluginRegistry([plugin])
    reg._health = {"fake": HealthStatus(ok=True)}
    return reg


@pytest.mark.asyncio
async def test_run_tool_loop_text_only_emits_text_delta_and_done():
    provider = FakeProvider(token_chunks=["hello", " world"])
    messages = [{"role": "user", "content": "hi"}]
    events = []
    async for event in run_tool_loop(
        provider=provider,
        messages=messages,
        tools_payload=None,
        plugin_registry=None,
        interactive=True,
    ):
        events.append(event)

    types = [e.type for e in events]
    assert AgentEventType.TEXT_DELTA in types
    assert types[-1] == AgentEventType.DONE
    done = events[-1]
    assert done.data["text"] == "hello world"
    assert done.data["tool_calls"] == []


@pytest.mark.asyncio
async def test_run_tool_loop_single_tool_call(fake_registry):
    provider = FakeProvider(
        scripted_turns=[
            {"tool_calls": [{"id": "c1", "tool": "fake__echo", "args": {"text": "ping"}}]},
            {"text": "the echo was ping"},
        ]
    )
    messages = [{"role": "user", "content": "echo ping"}]
    events = []
    async for event in run_tool_loop(
        provider=provider,
        messages=messages,
        tools_payload=[],
        plugin_registry=fake_registry,
        interactive=True,
    ):
        events.append(event)

    types = [e.type for e in events]
    assert AgentEventType.TOOL_CALL in types
    assert AgentEventType.TOOL_RESULT in types
    assert types[-1] == AgentEventType.DONE
    done = events[-1]
    assert len(done.data["tool_calls"]) == 1
    assert done.data["tool_calls"][0]["tool"] == "fake.echo"


@pytest.mark.asyncio
async def test_run_tool_loop_10_turn_cap_drops_tools(fake_registry):
    """On the 11th stream call, tools must be None (cap enforcement)."""
    scripted = [
        {"tool_calls": [{"id": f"c{i}", "tool": "fake__recurse", "args": {"depth": i}}]}
        for i in range(10)
    ] + [{"text": "done"}]
    provider = FakeProvider(scripted_turns=scripted)
    messages = [{"role": "user", "content": "recurse"}]

    async for _ in run_tool_loop(
        provider=provider,
        messages=messages,
        tools_payload=[{"name": "fake__recurse", "description": "", "input_schema": {}}],
        plugin_registry=fake_registry,
        interactive=True,
    ):
        pass

    assert len(provider.stream_calls) == 11
    assert provider.stream_calls[10]["tools"] is None


@pytest.mark.asyncio
async def test_run_tool_loop_sets_interactive_contextvar(fake_registry):
    interactive_flag: list[bool] = []
    original_invoke = fake_registry.invoke

    async def _capturing_invoke(tool, args, *, call_id):
        interactive_flag.append(is_interactive())
        return await original_invoke(tool, args, call_id=call_id)

    fake_registry.invoke = _capturing_invoke

    provider = FakeProvider(
        scripted_turns=[
            {"tool_calls": [{"id": "c1", "tool": "fake__echo", "args": {"text": "x"}}]},
            {"text": "done"},
        ]
    )
    messages = [{"role": "user", "content": "test"}]
    async for _ in run_tool_loop(
        provider=provider,
        messages=messages,
        tools_payload=[],
        plugin_registry=fake_registry,
        interactive=False,
    ):
        pass

    assert interactive_flag == [False]


@pytest.mark.asyncio
async def test_run_turn_collected_returns_final_text():
    provider = FakeProvider(token_chunks=["foo", " bar"])
    text = await run_turn_collected(
        provider=provider,
        messages=[{"role": "user", "content": "hi"}],
        tools_payload=None,
        plugin_registry=None,
        interactive=False,
    )
    assert text == "foo bar"


@pytest.mark.asyncio
async def test_run_tool_loop_contextvar_reset_after_done():
    """interactive contextvar must be restored to its previous value after the loop."""
    from atlas_plugins.context import reset_interactive, set_interactive

    token = set_interactive(True)  # set outer context to True
    try:
        provider = FakeProvider(token_chunks=["hi"])
        async for _ in run_tool_loop(
            provider=provider,
            messages=[{"role": "user", "content": "hi"}],
            tools_payload=None,
            plugin_registry=None,
            interactive=False,
        ):
            pass
        assert is_interactive() is True  # restored after generator exhausted
    finally:
        reset_interactive(token)


@pytest.mark.asyncio
async def test_run_tool_loop_provider_error_yields_error_event():
    """Provider error event → runner yields ERROR event and stops."""
    provider = FakeProvider(error_on_call=True)
    messages = [{"role": "user", "content": "hi"}]
    events = []
    async for event in run_tool_loop(
        provider=provider,
        messages=messages,
        tools_payload=None,
        plugin_registry=None,
        interactive=True,
    ):
        events.append(event)

    assert any(e.type == AgentEventType.ERROR for e in events)
    # No DONE event on error
    assert all(e.type != AgentEventType.DONE for e in events)


@pytest.mark.asyncio
async def test_run_tool_loop_tool_error_returns_error_result(fake_registry):
    """Tool that raises → ToolResult(error=...) returned, loop continues."""
    provider = FakeProvider(
        scripted_turns=[
            {"tool_calls": [{"id": "c1", "tool": "fake__fail", "args": {}}]},
            {"text": "tool failed but I handled it"},
        ]
    )
    messages = [{"role": "user", "content": "fail"}]
    events = []
    async for event in run_tool_loop(
        provider=provider,
        messages=messages,
        tools_payload=[],
        plugin_registry=fake_registry,
        interactive=True,
    ):
        events.append(event)

    tool_result_events = [e for e in events if e.type == AgentEventType.TOOL_RESULT]
    assert len(tool_result_events) == 1
    assert tool_result_events[0].data["ok"] is False
    # Loop continues to DONE despite tool error
    assert events[-1].type == AgentEventType.DONE
    done_tool_calls = events[-1].data["tool_calls"]
    assert done_tool_calls[0]["error"] is not None


@pytest.mark.asyncio
async def test_run_tool_loop_cap_injects_instruction_message(fake_registry):
    """At the 10-turn cap, a system instruction message is appended to messages."""
    scripted = [
        {"tool_calls": [{"id": f"c{i}", "tool": "fake__recurse", "args": {"depth": i}}]}
        for i in range(10)
    ] + [{"text": "done"}]
    provider = FakeProvider(scripted_turns=scripted)
    messages = [{"role": "user", "content": "recurse"}]

    async for _ in run_tool_loop(
        provider=provider,
        messages=messages,
        tools_payload=[{"name": "fake__recurse", "description": "", "input_schema": {}}],
        plugin_registry=fake_registry,
        interactive=True,
    ):
        pass

    # The instruction message is appended to the messages list on the 10th tool turn
    assert any(
        isinstance(m.get("content"), str) and "Tool call limit reached" in m["content"]
        for m in messages
    )
