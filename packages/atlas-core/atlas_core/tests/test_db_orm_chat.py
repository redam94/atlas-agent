"""Structural tests for the chat-related ORM models."""
from sqlalchemy import inspect

from atlas_core.db.orm import MessageORM, ModelUsageORM, SessionORM


def test_session_orm_columns():
    cols = {c.name for c in inspect(SessionORM).columns}
    assert cols == {"id", "user_id", "project_id", "model", "created_at", "last_active_at"}


def test_message_orm_columns():
    cols = {c.name for c in inspect(MessageORM).columns}
    assert cols == {
        "id",
        "user_id",
        "session_id",
        "role",
        "content",
        "tool_calls",
        "rag_context",
        "model",
        "token_count",
        "created_at",
    }


def test_model_usage_orm_columns():
    cols = {c.name for c in inspect(ModelUsageORM).columns}
    assert cols == {
        "id",
        "user_id",
        "session_id",
        "project_id",
        "provider",
        "model_id",
        "input_tokens",
        "output_tokens",
        "latency_ms",
        "task_type",
        "created_at",
    }


def test_session_orm_has_project_fk():
    fks = list(inspect(SessionORM).columns["project_id"].foreign_keys)
    assert len(fks) == 1
    assert fks[0].column.table.name == "projects"


def test_message_orm_has_session_fk():
    fks = list(inspect(MessageORM).columns["session_id"].foreign_keys)
    assert len(fks) == 1
    assert fks[0].column.table.name == "sessions"
