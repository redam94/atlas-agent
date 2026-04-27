"""Tests for atlas_core.models.sessions."""
from datetime import datetime, timezone
from uuid import uuid4

import pytest
from pydantic import ValidationError

from atlas_core.models.sessions import MessageRole, Session, SessionCreate


def test_message_role_values():
    assert MessageRole.SYSTEM == "system"
    assert MessageRole.USER == "user"
    assert MessageRole.ASSISTANT == "assistant"
    assert MessageRole.TOOL == "tool"


def test_session_construction_with_all_fields():
    s = Session(
        id=uuid4(),
        user_id="matt",
        project_id=uuid4(),
        model="claude-sonnet-4-6",
        created_at=datetime.now(timezone.utc),
        last_active_at=datetime.now(timezone.utc),
    )
    assert s.user_id == "matt"


def test_session_create_minimal_payload():
    payload = SessionCreate.model_validate({"project_id": str(uuid4())})
    assert payload.model is None  # optional


def test_session_create_requires_project_id():
    with pytest.raises(ValidationError):
        SessionCreate.model_validate({})
