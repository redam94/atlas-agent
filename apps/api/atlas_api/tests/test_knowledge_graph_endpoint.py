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


@pytest.mark.asyncio
async def test_expand_mode_via_seed_node_ids(
    app_with_graph_overrides, db_session, fake_graph_store
):
    project = ProjectORM(user_id="matt", name="P", default_model="claude-sonnet-4-6")
    db_session.add(project)
    await db_session.flush()
    seed = uuid4()
    fake_graph_store.fetch_subgraph_by_seeds.return_value = (
        [
            {
                "id": str(seed),
                "type": "Entity",
                "label": "Seed",
                "pagerank": 0.0,
                "metadata": {},
            },
            {
                "id": "22222222-2222-2222-2222-222222222222",
                "type": "Chunk",
                "label": "neighbor chunk",
                "pagerank": None,
                "metadata": {"document_id": str(uuid4()), "chunk_index": 0, "text_preview": "..."},
            },
        ],
        [
            {
                "id": "rel-1",
                "source": str(seed),
                "target": "22222222-2222-2222-2222-222222222222",
                "type": "MENTIONS",
            },
        ],
    )

    resp = await app_with_graph_overrides.get(
        "/api/v1/knowledge/graph",
        params={"project_id": str(project.id), "seed_node_ids": str(seed)},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["meta"]["mode"] == "expand"
    assert len(body["nodes"]) == 2
    assert len(body["edges"]) == 1
    fake_graph_store.fetch_subgraph_by_seeds.assert_called_once()
    args, kwargs = fake_graph_store.fetch_subgraph_by_seeds.call_args
    assert kwargs["seed_ids"] == [seed]


@pytest.mark.asyncio
async def test_expand_mode_priority_node_ids_over_chunk_ids(
    app_with_graph_overrides, db_session, fake_graph_store
):
    project = ProjectORM(user_id="matt", name="P", default_model="claude-sonnet-4-6")
    db_session.add(project)
    await db_session.flush()
    fake_graph_store.fetch_subgraph_by_seeds.return_value = ([], [])

    node_seed = uuid4()
    chunk_seed = uuid4()
    resp = await app_with_graph_overrides.get(
        "/api/v1/knowledge/graph",
        params={
            "project_id": str(project.id),
            "seed_node_ids": str(node_seed),
            "seed_chunk_ids": str(chunk_seed),
        },
    )
    assert resp.status_code == 200
    args, kwargs = fake_graph_store.fetch_subgraph_by_seeds.call_args
    # node_seed wins over chunk_seed.
    assert kwargs["seed_ids"] == [node_seed]


@pytest.mark.asyncio
async def test_node_types_filter_excludes_unwanted_types(
    app_with_graph_overrides, db_session, fake_graph_store
):
    project = ProjectORM(user_id="matt", name="P", default_model="claude-sonnet-4-6")
    db_session.add(project)
    await db_session.flush()

    fake_graph_store.fetch_subgraph_by_seeds.return_value = (
        [
            {"id": str(uuid4()), "type": "Entity", "label": "E", "pagerank": 0.0, "metadata": {}},
            {"id": str(uuid4()), "type": "Chunk", "label": "C", "pagerank": None, "metadata": {}},
        ],
        [],
    )

    resp = await app_with_graph_overrides.get(
        "/api/v1/knowledge/graph",
        params={
            "project_id": str(project.id),
            "seed_node_ids": str(uuid4()),
            "node_types": "Entity",
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert all(n["type"] == "Entity" for n in body["nodes"])


@pytest.mark.asyncio
async def test_unknown_node_types_returns_422(app_with_graph_overrides, db_session):
    project = ProjectORM(user_id="matt", name="P", default_model="claude-sonnet-4-6")
    db_session.add(project)
    await db_session.flush()

    resp = await app_with_graph_overrides.get(
        "/api/v1/knowledge/graph",
        params={"project_id": str(project.id), "node_types": "Bogus"},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_search_mode_calls_retriever_then_expands_chunk_hits(
    app_with_graph_overrides, db_session, fake_graph_store
):
    from datetime import UTC, datetime

    from atlas_knowledge.models.nodes import KnowledgeNode, KnowledgeNodeType
    from atlas_knowledge.models.retrieval import RetrievalResult, ScoredChunk

    from atlas_api.deps import get_retriever

    project = ProjectORM(user_id="matt", name="P", default_model="claude-sonnet-4-6")
    db_session.add(project)
    await db_session.flush()

    chunk_id = uuid4()
    fake_retriever = AsyncMock()
    fake_retriever.retrieve.return_value = RetrievalResult(
        query="hello",
        chunks=[
            ScoredChunk(
                chunk=KnowledgeNode(
                    id=chunk_id,
                    user_id="matt",
                    project_id=project.id,
                    type=KnowledgeNodeType.CHUNK,
                    text="hello world",
                    title="c",
                    created_at=datetime.now(UTC),
                    metadata={},
                ),
                score=0.9,
            )
        ],
    )

    fake_graph_store.fetch_subgraph_by_seeds.return_value = (
        [
            {"id": str(chunk_id), "type": "Chunk", "label": "hello world", "pagerank": None, "metadata": {}},
        ],
        [],
    )

    app.dependency_overrides[get_retriever] = lambda: fake_retriever
    try:
        resp = await app_with_graph_overrides.get(
            "/api/v1/knowledge/graph",
            params={"project_id": str(project.id), "q": "hello"},
        )
    finally:
        app.dependency_overrides.pop(get_retriever, None)

    assert resp.status_code == 200
    body = resp.json()
    assert body["meta"]["mode"] == "search"
    assert body["meta"]["hit_node_ids"] == [str(chunk_id)]
    assert len(body["nodes"]) == 1
    fake_retriever.retrieve.assert_awaited_once()
    fake_graph_store.fetch_subgraph_by_seeds.assert_called_once()


@pytest.mark.asyncio
async def test_search_mode_priority_q_over_seeds(
    app_with_graph_overrides, db_session, fake_graph_store
):
    """When q AND seeds are both set, q wins."""
    from atlas_knowledge.models.retrieval import RetrievalResult

    from atlas_api.deps import get_retriever

    project = ProjectORM(user_id="matt", name="P", default_model="claude-sonnet-4-6")
    db_session.add(project)
    await db_session.flush()

    fake_retriever = AsyncMock()
    fake_retriever.retrieve.return_value = RetrievalResult(query="x", chunks=[])
    fake_graph_store.fetch_subgraph_by_seeds.return_value = ([], [])

    app.dependency_overrides[get_retriever] = lambda: fake_retriever
    try:
        resp = await app_with_graph_overrides.get(
            "/api/v1/knowledge/graph",
            params={
                "project_id": str(project.id),
                "q": "x",
                "seed_node_ids": str(uuid4()),
            },
        )
    finally:
        app.dependency_overrides.pop(get_retriever, None)

    assert resp.status_code == 200
    assert resp.json()["meta"]["mode"] == "search"
    fake_retriever.retrieve.assert_awaited_once()


@pytest.mark.asyncio
async def test_top_entities_returns_503_when_graph_unavailable(
    app_with_graph_overrides, db_session, fake_graph_store
):
    from atlas_graph.errors import GraphUnavailableError

    project = ProjectORM(user_id="matt", name="P", default_model="claude-sonnet-4-6")
    db_session.add(project)
    await db_session.flush()

    fake_graph_store.fetch_top_entities.side_effect = GraphUnavailableError("neo4j down")

    resp = await app_with_graph_overrides.get(
        "/api/v1/knowledge/graph",
        params={"project_id": str(project.id)},
    )
    assert resp.status_code == 503
    assert resp.json()["detail"] == "graph_unavailable"


@pytest.mark.asyncio
async def test_expand_returns_503_when_graph_unavailable(
    app_with_graph_overrides, db_session, fake_graph_store
):
    from atlas_graph.errors import GraphUnavailableError

    project = ProjectORM(user_id="matt", name="P", default_model="claude-sonnet-4-6")
    db_session.add(project)
    await db_session.flush()

    fake_graph_store.fetch_subgraph_by_seeds.side_effect = GraphUnavailableError("neo4j down")

    resp = await app_with_graph_overrides.get(
        "/api/v1/knowledge/graph",
        params={"project_id": str(project.id), "seed_node_ids": str(uuid4())},
    )
    assert resp.status_code == 503


@pytest.mark.asyncio
async def test_search_falls_back_to_chunks_only_when_graph_unavailable(
    app_with_graph_overrides, db_session, fake_graph_store
):
    from datetime import datetime

    from atlas_graph.errors import GraphUnavailableError
    from atlas_knowledge.models.nodes import KnowledgeNode, KnowledgeNodeType
    from atlas_knowledge.models.retrieval import RetrievalResult, ScoredChunk

    from atlas_api.deps import get_retriever

    project = ProjectORM(user_id="matt", name="P", default_model="claude-sonnet-4-6")
    db_session.add(project)
    await db_session.flush()

    chunk_id = uuid4()
    fake_retriever = AsyncMock()
    fake_retriever.retrieve.return_value = RetrievalResult(
        query="x",
        chunks=[
            ScoredChunk(
                chunk=KnowledgeNode(
                    id=chunk_id, user_id="matt", project_id=project.id,
                    type=KnowledgeNodeType.CHUNK,
                    title="c", text="x", metadata={},
                    created_at=datetime.utcnow(),
                ),
                score=0.5,
            )
        ],
    )
    fake_graph_store.fetch_subgraph_by_seeds.side_effect = GraphUnavailableError("neo4j down")

    app.dependency_overrides[get_retriever] = lambda: fake_retriever
    try:
        resp = await app_with_graph_overrides.get(
            "/api/v1/knowledge/graph",
            params={"project_id": str(project.id), "q": "x"},
        )
    finally:
        app.dependency_overrides.pop(get_retriever, None)

    assert resp.status_code == 200
    body = resp.json()
    assert body["meta"]["mode"] == "search"
    assert body["meta"]["degraded_stages"] == ["graph_unavailable"]
    assert body["edges"] == []
    # Hit chunk synthesized as a node so the UI has something to render.
    assert len(body["nodes"]) == 1
    assert body["nodes"][0]["id"] == str(chunk_id)
