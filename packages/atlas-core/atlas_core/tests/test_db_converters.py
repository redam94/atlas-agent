"""Tests for atlas_core.db.converters — pure conversion logic, no DB roundtrip."""

from datetime import UTC, datetime
from uuid import uuid4

from atlas_core.db.converters import message_from_orm, session_from_orm
from atlas_core.db.orm import MessageORM, SessionORM


def _build_session_row() -> SessionORM:
    return SessionORM(
        id=uuid4(),
        user_id="matt",
        project_id=uuid4(),
        model="claude-sonnet-4-6",
        created_at=datetime.now(UTC),
        last_active_at=datetime.now(UTC),
    )


def _build_message_row() -> MessageORM:
    return MessageORM(
        id=uuid4(),
        user_id="matt",
        session_id=uuid4(),
        role="assistant",
        content="hi",
        tool_calls=None,
        rag_context=None,
        model="claude-sonnet-4-6",
        token_count=10,
        created_at=datetime.now(UTC),
    )


def test_session_from_orm_roundtrip():
    row = _build_session_row()
    s = session_from_orm(row)
    assert s.user_id == row.user_id
    assert s.project_id == row.project_id
    assert s.model == row.model


def test_message_from_orm_roundtrip():
    row = _build_message_row()
    m = message_from_orm(row)
    assert m.user_id == row.user_id
    assert m.role == "assistant"
    assert m.content == "hi"


def test_message_from_orm_handles_jsonb_none():
    row = _build_message_row()
    row.tool_calls = None
    row.rag_context = None
    m = message_from_orm(row)
    assert m.tool_calls is None
    assert m.rag_context is None
