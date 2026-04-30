"""Test fake provider's tool-call scripting."""

import pytest
from atlas_core.models.llm import ModelEventType
from atlas_core.providers._fake import FakeProvider


@pytest.mark.asyncio
async def test_scripted_tool_call_emitted():
    """First turn emits a TOOL_CALL event, second turn emits TOKEN."""
    provider = FakeProvider(scripted_turns=[
        {"tool_calls": [{"id": "tu_1", "tool": "fake.echo", "args": {"text": "hi"}}]},
        {"text": "Got the echo: hi"},
    ])
    # First turn: should emit a TOOL_CALL event.
    events_t1 = [e async for e in provider.stream(messages=[{"role": "user", "content": "x"}])]
    types_t1 = [e.type for e in events_t1]
    assert ModelEventType.TOOL_CALL in types_t1

    # Second turn: text only.
    events_t2 = [e async for e in provider.stream(messages=[{"role": "user", "content": "x"}])]
    types_t2 = [e.type for e in events_t2]
    assert ModelEventType.TOKEN in types_t2
    assert ModelEventType.TOOL_CALL not in types_t2
