"""Tests for atlas_core.models.messages."""

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError

from atlas_core.models.messages import (
    ChatRequest,
    Message,
    StreamEvent,
    StreamEventType,
)
from atlas_core.models.sessions import MessageRole


def test_message_construction():
    m = Message(
        id=uuid4(),
        user_id="matt",
        session_id=uuid4(),
        role=MessageRole.USER,
        content="hello",
        created_at=datetime.now(UTC),
    )
    assert m.role is MessageRole.USER


def test_message_optional_fields_default_none():
    m = Message(
        id=uuid4(),
        user_id="matt",
        session_id=uuid4(),
        role=MessageRole.USER,
        content="hi",
        created_at=datetime.now(UTC),
    )
    assert m.tool_calls is None
    assert m.rag_context is None
    assert m.model is None
    assert m.token_count is None


def test_chat_request_minimal():
    cr = ChatRequest.model_validate({"text": "hello", "project_id": str(uuid4())})
    assert cr.text == "hello"
    assert cr.model_override is None


def test_chat_request_rejects_empty_text():
    with pytest.raises(ValidationError):
        ChatRequest.model_validate({"text": "", "project_id": str(uuid4())})


def test_chat_request_text_too_long():
    long_text = "x" * 32_001
    with pytest.raises(ValidationError):
        ChatRequest.model_validate({"text": long_text, "project_id": str(uuid4())})


def test_stream_event_token_type():
    e = StreamEvent(
        type=StreamEventType.TOKEN,
        payload={"token": "hello"},
        sequence=1,
    )
    assert e.type == "chat.token"


def test_stream_event_type_values():
    assert StreamEventType.TOKEN == "chat.token"
    assert StreamEventType.TOOL_CALL == "chat.tool_use"
    assert StreamEventType.TOOL_RESULT == "chat.tool_result"
    assert StreamEventType.DONE == "chat.done"
    assert StreamEventType.ERROR == "chat.error"
    assert StreamEventType.RAG_CONTEXT == "rag.context"
