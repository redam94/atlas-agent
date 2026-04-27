"""Integration tests for /api/v1/knowledge/* — uses FakeEmbedder + tmp Chroma."""

import pytest
from atlas_core.db.orm import KnowledgeNodeORM, ProjectORM
from atlas_knowledge.embeddings import FakeEmbedder
from atlas_knowledge.ingestion.service import IngestionService
from atlas_knowledge.retrieval.retriever import Retriever
from atlas_knowledge.vector.chroma import ChromaVectorStore
from sqlalchemy import select

from atlas_api.deps import get_ingestion_service, get_retriever
from atlas_api.main import app


@pytest.fixture
def fake_knowledge_stack(tmp_path):
    embedder = FakeEmbedder(dim=16)
    store = ChromaVectorStore(persist_dir=str(tmp_path), user_id="matt")
    return {
        "ingestion": IngestionService(embedder=embedder, vector_store=store),
        "retriever": Retriever(embedder=embedder, vector_store=store),
    }


@pytest.fixture
def app_with_knowledge_overrides(app_client, fake_knowledge_stack):
    app.dependency_overrides[get_ingestion_service] = lambda: fake_knowledge_stack["ingestion"]
    app.dependency_overrides[get_retriever] = lambda: fake_knowledge_stack["retriever"]
    yield app_client
    app.dependency_overrides.pop(get_ingestion_service, None)
    app.dependency_overrides.pop(get_retriever, None)


@pytest.mark.asyncio
async def test_ingest_markdown_then_search(app_with_knowledge_overrides, db_session):
    project = ProjectORM(user_id="matt", name="P", default_model="claude-sonnet-4-6")
    db_session.add(project)
    await db_session.flush()

    body = {
        "project_id": str(project.id),
        "source_type": "markdown",
        "text": "# Notes\n\n" + ("alpha beta " * 600),
        "source_filename": "notes.md",
    }
    resp = await app_with_knowledge_overrides.post("/api/v1/knowledge/ingest", json=body)
    assert resp.status_code == 202
    job = resp.json()
    assert job["status"] == "completed"

    nodes = (await db_session.execute(select(KnowledgeNodeORM))).scalars().all()
    assert any(n.type == "document" for n in nodes)
    assert any(n.type == "chunk" for n in nodes)

    search = await app_with_knowledge_overrides.get(
        "/api/v1/knowledge/search",
        params={"project_id": str(project.id), "query": "alpha beta", "top_k": 3},
    )
    assert search.status_code == 200
    body = search.json()
    assert body["query"] == "alpha beta"
    assert len(body["chunks"]) >= 1


@pytest.mark.asyncio
async def test_get_unknown_job_returns_404(app_with_knowledge_overrides):
    from uuid import uuid4

    resp = await app_with_knowledge_overrides.get(f"/api/v1/knowledge/jobs/{uuid4()}")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_delete_unknown_node_returns_404(app_with_knowledge_overrides):
    from uuid import uuid4

    resp = await app_with_knowledge_overrides.delete(f"/api/v1/knowledge/nodes/{uuid4()}")
    assert resp.status_code == 404
