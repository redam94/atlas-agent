"""Tests for GET /api/v1/sessions/{session_id}/messages."""

from uuid import uuid4

import pytest
from atlas_core.db.orm import MessageORM, ProjectORM, SessionORM


@pytest.mark.asyncio
async def test_list_messages_empty_for_unknown_session(app_client) -> None:
    """Missing session row → 200 [], not 404. Frontend mints session_ids before WS connect."""
    response = await app_client.get(f"/api/v1/sessions/{uuid4()}/messages")
    assert response.status_code == 200
    assert response.json() == []


@pytest.mark.asyncio
async def test_list_messages_returns_in_created_at_order(app_client, db_session) -> None:
    project = ProjectORM(user_id="matt", name="P", default_model="claude-sonnet-4-6")
    db_session.add(project)
    await db_session.flush()

    session = SessionORM(user_id="matt", project_id=project.id)
    db_session.add(session)
    await db_session.flush()

    # Insert in scrambled order; expect chronological response.
    db_session.add(MessageORM(user_id="matt", session_id=session.id, role="user", content="first"))
    await db_session.flush()
    db_session.add(MessageORM(user_id="matt", session_id=session.id, role="assistant", content="second"))
    await db_session.flush()
    db_session.add(MessageORM(user_id="matt", session_id=session.id, role="user", content="third"))
    await db_session.flush()
    await db_session.commit()

    response = await app_client.get(f"/api/v1/sessions/{session.id}/messages")
    assert response.status_code == 200
    contents = [m["content"] for m in response.json()]
    assert contents == ["first", "second", "third"]


@pytest.mark.asyncio
async def test_list_messages_403_for_other_user(app_client, db_session) -> None:
    project = ProjectORM(user_id="someone-else", name="X", default_model="claude-sonnet-4-6")
    db_session.add(project)
    await db_session.flush()
    session = SessionORM(user_id="someone-else", project_id=project.id)
    db_session.add(session)
    await db_session.flush()
    await db_session.commit()

    response = await app_client.get(f"/api/v1/sessions/{session.id}/messages")
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_list_messages_invalid_uuid_422(app_client) -> None:
    response = await app_client.get("/api/v1/sessions/not-a-uuid/messages")
    assert response.status_code == 422
