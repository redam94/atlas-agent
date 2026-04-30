"""Integration tests for /api/v1/notes (Plan 6)."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from atlas_core.db.orm import IngestionJobORM, KnowledgeNodeORM, NoteORM, ProjectORM
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


@pytest.mark.asyncio
async def test_index_first_time_runs_ingest_and_tags(app_client, db_session):
    """First-time index: cleanup NOT called; ingest called; tag_note called; row updated."""
    project = ProjectORM(user_id="matt", name="P", default_model="claude-sonnet-4-6")
    db_session.add(project)
    await db_session.flush()

    eid = uuid4()
    note = NoteORM(
        user_id="matt", project_id=project.id, title="t", body_markdown="hello",
        mention_entity_ids=[eid],
    )
    db_session.add(note)
    await db_session.flush()
    note_id = note.id

    fake_service = AsyncMock()
    new_doc_id = uuid4()
    fake_job_id = uuid4()
    from atlas_knowledge.ingestion.service import IngestionResult
    fake_service.ingest.return_value = IngestionResult(
        job_id=fake_job_id, document_id=new_doc_id
    )

    fake_graph_store = AsyncMock()

    # Create the knowledge node so the FK constraint passes
    doc_node = KnowledgeNodeORM(
        id=new_doc_id, user_id="matt", project_id=project.id,
        type="document", title="t", text="hello"
    )
    db_session.add(doc_node)
    job_row = IngestionJobORM(
        id=fake_job_id, user_id="matt", project_id=project.id,
        source_type="markdown", source_filename=None, status="completed",
    )
    db_session.add(job_row)
    await db_session.flush()

    from atlas_api.deps import get_graph_store, get_ingestion_service
    from atlas_api.main import app
    app.dependency_overrides[get_ingestion_service] = lambda: fake_service
    app.dependency_overrides[get_graph_store] = lambda: fake_graph_store
    try:
        resp = await app_client.post(f"/api/v1/notes/{note_id}/index")
    finally:
        app.dependency_overrides.pop(get_ingestion_service, None)
        app.dependency_overrides.pop(get_graph_store, None)

    assert resp.status_code == 200

    fake_service.cleanup_document.assert_not_called()
    fake_service.ingest.assert_awaited_once()
    args, kwargs = fake_service.ingest.call_args
    assert kwargs["source_type"] == "note"

    fake_graph_store.tag_note.assert_awaited_once()
    args, kwargs = fake_graph_store.tag_note.call_args
    assert kwargs["note_id"] == new_doc_id
    assert kwargs["entity_ids"] == [eid]

    refreshed = await db_session.get(NoteORM, note_id)
    await db_session.refresh(refreshed)
    assert refreshed.knowledge_node_id == new_doc_id
    assert refreshed.indexed_at is not None


@pytest.mark.asyncio
async def test_index_reindex_calls_cleanup_first(app_client, db_session):
    """Re-index: cleanup_document called with the previous knowledge_node_id."""
    project = ProjectORM(user_id="matt", name="P", default_model="claude-sonnet-4-6")
    db_session.add(project)
    await db_session.flush()

    prev_doc_id = uuid4()
    # Create the previous knowledge node
    prev_doc_node = KnowledgeNodeORM(
        id=prev_doc_id, user_id="matt", project_id=project.id,
        type="document", title="old", text="hello"
    )
    db_session.add(prev_doc_node)
    await db_session.flush()

    note = NoteORM(
        user_id="matt", project_id=project.id, title="t", body_markdown="hello",
        knowledge_node_id=prev_doc_id,
    )
    db_session.add(note)
    await db_session.flush()
    note_id = note.id

    fake_service = AsyncMock()
    new_doc_id = uuid4()
    fake_job_id = uuid4()
    from atlas_knowledge.ingestion.service import IngestionResult
    fake_service.ingest.return_value = IngestionResult(
        job_id=fake_job_id, document_id=new_doc_id
    )

    fake_graph_store = AsyncMock()

    # Create the new knowledge node so FK constraint passes
    new_doc_node = KnowledgeNodeORM(
        id=new_doc_id, user_id="matt", project_id=project.id,
        type="document", title="t", text="hello"
    )
    db_session.add(new_doc_node)
    job_row = IngestionJobORM(
        id=fake_job_id, user_id="matt", project_id=project.id,
        source_type="markdown", source_filename=None, status="completed",
    )
    db_session.add(job_row)
    await db_session.flush()

    from atlas_api.deps import get_graph_store, get_ingestion_service
    from atlas_api.main import app
    app.dependency_overrides[get_ingestion_service] = lambda: fake_service
    app.dependency_overrides[get_graph_store] = lambda: fake_graph_store
    try:
        resp = await app_client.post(f"/api/v1/notes/{note_id}/index")
    finally:
        app.dependency_overrides.pop(get_ingestion_service, None)
        app.dependency_overrides.pop(get_graph_store, None)

    assert resp.status_code == 200
    fake_service.cleanup_document.assert_awaited_once()
    args, kwargs = fake_service.cleanup_document.call_args
    assert kwargs["document_id"] == prev_doc_id


@pytest.mark.asyncio
async def test_index_no_mentions_skips_tag_note(app_client, db_session):
    project = ProjectORM(user_id="matt", name="P", default_model="claude-sonnet-4-6")
    db_session.add(project)
    await db_session.flush()
    note = NoteORM(
        user_id="matt", project_id=project.id, title="t", body_markdown="hi",
        mention_entity_ids=[],
    )
    db_session.add(note)
    await db_session.flush()
    note_id = note.id

    fake_service = AsyncMock()
    fake_job_id = uuid4()
    doc_id = uuid4()
    from atlas_knowledge.ingestion.service import IngestionResult
    fake_service.ingest.return_value = IngestionResult(
        job_id=fake_job_id, document_id=doc_id
    )
    fake_graph_store = AsyncMock()

    # Create the knowledge node so FK constraint passes
    doc_node = KnowledgeNodeORM(
        id=doc_id, user_id="matt", project_id=project.id,
        type="document", title="t", text="hi"
    )
    db_session.add(doc_node)
    job_row = IngestionJobORM(
        id=fake_job_id, user_id="matt", project_id=project.id,
        source_type="markdown", source_filename=None, status="completed",
    )
    db_session.add(job_row)
    await db_session.flush()

    from atlas_api.deps import get_graph_store, get_ingestion_service
    from atlas_api.main import app
    app.dependency_overrides[get_ingestion_service] = lambda: fake_service
    app.dependency_overrides[get_graph_store] = lambda: fake_graph_store
    try:
        resp = await app_client.post(f"/api/v1/notes/{note_id}/index")
    finally:
        app.dependency_overrides.pop(get_ingestion_service, None)
        app.dependency_overrides.pop(get_graph_store, None)

    assert resp.status_code == 200
    fake_graph_store.tag_note.assert_not_called()


@pytest.mark.asyncio
async def test_index_unknown_note_404(app_client):
    resp = await app_client.post(f"/api/v1/notes/{uuid4()}/index")
    assert resp.status_code == 404
