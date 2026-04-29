"""Integration tests for GET /api/v1/knowledge/graph (Plan 5)."""

from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from atlas_core.db.orm import ProjectORM

from atlas_api.deps import get_graph_store
from atlas_api.main import app


@pytest.fixture
def fake_graph_store():
    store = AsyncMock()
    store.fetch_top_entities.return_value = (
        [
            {
                "id": "11111111-1111-1111-1111-111111111111",
                "type": "Entity",
                "label": "Llama 3",
                "pagerank": 0.5,
                "metadata": {"entity_type": "PRODUCT", "mention_count": 3},
            },
        ],
        [],
    )
    store.fetch_subgraph_by_seeds.return_value = ([], [])
    return store


@pytest.fixture
def app_with_graph_overrides(app_client, fake_graph_store):
    app.dependency_overrides[get_graph_store] = lambda: fake_graph_store
    yield app_client
    app.dependency_overrides.pop(get_graph_store, None)


@pytest.mark.asyncio
async def test_top_entities_mode_returns_entities_when_no_query_or_seeds(
    app_with_graph_overrides, db_session, fake_graph_store
):
    project = ProjectORM(user_id="matt", name="P", default_model="claude-sonnet-4-6")
    db_session.add(project)
    await db_session.flush()

    resp = await app_with_graph_overrides.get(
        "/api/v1/knowledge/graph",
        params={"project_id": str(project.id)},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["meta"]["mode"] == "top_entities"
    assert body["meta"]["truncated"] is False
    assert len(body["nodes"]) == 1
    assert body["nodes"][0]["label"] == "Llama 3"
    fake_graph_store.fetch_top_entities.assert_called_once()
    fake_graph_store.fetch_subgraph_by_seeds.assert_not_called()


@pytest.mark.asyncio
async def test_unknown_project_returns_404(app_with_graph_overrides):
    resp = await app_with_graph_overrides.get(
        "/api/v1/knowledge/graph",
        params={"project_id": str(uuid4())},
    )
    assert resp.status_code == 404
