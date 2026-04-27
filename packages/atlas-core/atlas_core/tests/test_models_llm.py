"""Tests for atlas_core.models.llm."""

from atlas_core.models.llm import (
    ModelEvent,
    ModelEventType,
    ModelSpec,
    ModelUsage,
    ToolCall,
    ToolResult,
    ToolSchema,
)


def test_model_event_type_values():
    assert ModelEventType.TOKEN == "token"
    assert ModelEventType.TOOL_CALL == "tool_call"
    assert ModelEventType.TOOL_RESULT == "tool_result"
    assert ModelEventType.DONE == "done"
    assert ModelEventType.ERROR == "error"


def test_model_spec_construction():
    spec = ModelSpec(
        provider="anthropic",
        model_id="claude-sonnet-4-6",
        context_window=200_000,
        supports_tools=True,
        supports_streaming=True,
    )
    assert spec.provider == "anthropic"


def test_model_event_token():
    e = ModelEvent(type=ModelEventType.TOKEN, data={"text": "hello"})
    assert e.type == "token"
    assert e.data["text"] == "hello"


def test_model_event_done_with_usage():
    usage = ModelUsage(input_tokens=42, output_tokens=17, model_id="x", provider="y")
    e = ModelEvent(type=ModelEventType.DONE, data={"usage": usage.model_dump(mode="python")})
    assert e.type == "done"
    assert e.data["usage"]["input_tokens"] == 42


def test_tool_schema_round_trip():
    ts = ToolSchema(
        name="x.y",
        description="test tool",
        parameters={"type": "object", "properties": {}},
        plugin="x",
    )
    assert ts.requires_confirmation is False  # default


def test_tool_call_and_result_pair():
    call = ToolCall(id="t-1", tool="github.search", args={"q": "x"})
    result = ToolResult(call_id="t-1", tool="github.search", result={"hits": []})
    assert call.id == result.call_id
