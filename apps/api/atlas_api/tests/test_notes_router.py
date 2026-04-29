"""Integration tests for /api/v1/notes (Plan 6)."""

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from atlas_core.db.orm import NoteORM, ProjectORM
from sqlalchemy import select


@pytest.mark.asyncio
async def test_create_note_default_fields(app_client, db_session):
    project = ProjectORM(user_id="matt", name="P", default_model="claude-sonnet-4-6")
    db_session.add(project)
    await db_session.flush()

    resp = await app_client.post(
        "/api/v1/notes",
        json={"project_id": str(project.id)},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["title"] == "Untitled"
    assert body["body_markdown"] == ""
    assert body["mention_entity_ids"] == []
    assert body["knowledge_node_id"] is None
    assert body["indexed_at"] is None


@pytest.mark.asyncio
async def test_list_notes_orders_by_updated_at_desc(app_client, db_session):
    project = ProjectORM(user_id="matt", name="P", default_model="claude-sonnet-4-6")
    db_session.add(project)
    await db_session.flush()
    older = NoteORM(user_id="matt", project_id=project.id, title="A",
                    updated_at=datetime(2026, 4, 1, tzinfo=UTC))
    newer = NoteORM(user_id="matt", project_id=project.id, title="B",
                    updated_at=datetime(2026, 4, 28, tzinfo=UTC))
    db_session.add_all([older, newer])
    await db_session.flush()

    resp = await app_client.get(f"/api/v1/notes?project_id={project.id}")
    assert resp.status_code == 200
    titles = [n["title"] for n in resp.json()]
    assert titles == ["B", "A"]


@pytest.mark.asyncio
async def test_get_note_returns_full_row(app_client, db_session):
    project = ProjectORM(user_id="matt", name="P", default_model="claude-sonnet-4-6")
    db_session.add(project)
    await db_session.flush()
    note = NoteORM(user_id="matt", project_id=project.id, title="Test",
                   body_markdown="hello")
    db_session.add(note)
    await db_session.flush()

    resp = await app_client.get(f"/api/v1/notes/{note.id}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["title"] == "Test"
    assert body["body_markdown"] == "hello"


@pytest.mark.asyncio
async def test_get_unknown_note_404(app_client):
    resp = await app_client.get(f"/api/v1/notes/{uuid4()}")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_patch_note_updates_fields(app_client, db_session):
    project = ProjectORM(user_id="matt", name="P", default_model="claude-sonnet-4-6")
    db_session.add(project)
    await db_session.flush()
    note = NoteORM(user_id="matt", project_id=project.id, title="Old")
    db_session.add(note)
    await db_session.flush()
    eid_a, eid_b = uuid4(), uuid4()

    resp = await app_client.patch(
        f"/api/v1/notes/{note.id}",
        json={"title": "New", "body_markdown": "body",
              "mention_entity_ids": [str(eid_a), str(eid_b)]},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["title"] == "New"
    assert body["body_markdown"] == "body"
    assert sorted(body["mention_entity_ids"]) == sorted([str(eid_a), str(eid_b)])


@pytest.mark.asyncio
async def test_patch_unknown_note_404(app_client):
    resp = await app_client.patch(f"/api/v1/notes/{uuid4()}", json={"title": "x"})
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_delete_note_removes_row(app_client, db_session):
    project = ProjectORM(user_id="matt", name="P", default_model="claude-sonnet-4-6")
    db_session.add(project)
    await db_session.flush()
    note = NoteORM(user_id="matt", project_id=project.id, title="Doomed")
    db_session.add(note)
    await db_session.flush()
    note_id = note.id

    resp = await app_client.delete(f"/api/v1/notes/{note_id}")
    assert resp.status_code == 204

    rows = (await db_session.execute(select(NoteORM).where(NoteORM.id == note_id))).all()
    assert rows == []


@pytest.mark.asyncio
async def test_delete_unknown_note_404(app_client):
    resp = await app_client.delete(f"/api/v1/notes/{uuid4()}")
    assert resp.status_code == 404
