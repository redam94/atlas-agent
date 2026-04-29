"""Integration tests for GET /api/v1/knowledge/entities (Plan 6)."""

from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from atlas_core.db.orm import ProjectORM
from atlas_graph.errors import GraphUnavailableError

from atlas_api.deps import get_graph_store
from atlas_api.main import app


@pytest.fixture
def fake_graph_store():
    store = AsyncMock()
    store.list_entities.return_value = [
        {"id": "11111111-1111-1111-1111-111111111111", "name": "Llama 3",
         "entity_type": "PRODUCT", "pagerank": 0.5},
    ]
    return store


@pytest.fixture
def app_with_graph_overrides(app_client, fake_graph_store):
    app.dependency_overrides[get_graph_store] = lambda: fake_graph_store
    yield app_client
    app.dependency_overrides.pop(get_graph_store, None)


@pytest.mark.asyncio
async def test_list_entities_happy_path(app_with_graph_overrides, db_session, fake_graph_store):
    project = ProjectORM(user_id="matt", name="P", default_model="claude-sonnet-4-6")
    db_session.add(project)
    await db_session.flush()

    resp = await app_with_graph_overrides.get(
        "/api/v1/knowledge/entities",
        params={"project_id": str(project.id), "prefix": "Lla", "limit": 5},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["name"] == "Llama 3"
    args, kwargs = fake_graph_store.list_entities.call_args
    assert kwargs["prefix"] == "Lla"
    assert kwargs["limit"] == 5


@pytest.mark.asyncio
async def test_list_entities_empty_prefix_default(app_with_graph_overrides, db_session, fake_graph_store):
    project = ProjectORM(user_id="matt", name="P", default_model="claude-sonnet-4-6")
    db_session.add(project)
    await db_session.flush()

    resp = await app_with_graph_overrides.get(
        "/api/v1/knowledge/entities",
        params={"project_id": str(project.id)},
    )
    assert resp.status_code == 200
    args, kwargs = fake_graph_store.list_entities.call_args
    assert kwargs["prefix"] == ""
    assert kwargs["limit"] == 10  # default


@pytest.mark.asyncio
async def test_list_entities_unknown_project_404(app_with_graph_overrides):
    resp = await app_with_graph_overrides.get(
        "/api/v1/knowledge/entities",
        params={"project_id": str(uuid4())},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_list_entities_503_when_graph_unavailable(
    app_with_graph_overrides, db_session, fake_graph_store
):
    project = ProjectORM(user_id="matt", name="P", default_model="claude-sonnet-4-6")
    db_session.add(project)
    await db_session.flush()
    fake_graph_store.list_entities.side_effect = GraphUnavailableError("down")

    resp = await app_with_graph_overrides.get(
        "/api/v1/knowledge/entities",
        params={"project_id": str(project.id)},
    )
    assert resp.status_code == 503
    assert resp.json()["detail"] == "graph_unavailable"
