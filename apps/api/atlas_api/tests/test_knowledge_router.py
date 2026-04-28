"""Integration tests for /api/v1/knowledge/* — uses FakeEmbedder + tmp Chroma."""

from unittest.mock import patch
from uuid import uuid4

import pytest
from atlas_core.db.orm import KnowledgeNodeORM, ProjectORM
from atlas_knowledge.embeddings import FakeEmbedder
from atlas_knowledge.ingestion.service import IngestionService
from atlas_knowledge.parsers.markdown import ParsedDocument
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


@pytest.mark.asyncio
async def test_ingest_unknown_project_returns_404(app_with_knowledge_overrides):
    from uuid import uuid4

    body = {
        "project_id": str(uuid4()),
        "source_type": "markdown",
        "text": "# hello\n\nbody",
    }
    resp = await app_with_knowledge_overrides.post("/api/v1/knowledge/ingest", json=body)
    assert resp.status_code == 404
    assert resp.json()["detail"] == "project not found"


@pytest.mark.asyncio
async def test_ingest_url_happy_path(app_with_knowledge_overrides, db_session):
    project = ProjectORM(user_id="matt", name="P", default_model="claude-sonnet-4-6")
    db_session.add(project)
    await db_session.flush()

    parsed = ParsedDocument(
        text="alpha beta " * 200,
        title="Geo-Lift Methodology",
        source_type="url",
        metadata={"source_url": "https://blog.example.com/geo-lift"},
    )

    async def fake_parse_url(_url):
        return parsed

    with patch(
        "atlas_api.routers.knowledge.parse_url",
        side_effect=fake_parse_url,
    ), patch(
        "atlas_api.routers.knowledge.validate_url",
        side_effect=lambda u: u,
    ):
        resp = await app_with_knowledge_overrides.post(
            "/api/v1/knowledge/ingest/url",
            json={
                "project_id": str(project.id),
                "url": "https://blog.example.com/geo-lift",
            },
        )

    assert resp.status_code == 202, resp.text
    body = resp.json()
    assert body["status"] == "completed"
    assert body["source_type"] == "url"
    assert body["source_filename"] == "https://blog.example.com/geo-lift"


@pytest.mark.asyncio
async def test_ingest_url_unknown_project_returns_404(app_with_knowledge_overrides):
    resp = await app_with_knowledge_overrides.post(
        "/api/v1/knowledge/ingest/url",
        json={"project_id": str(uuid4()), "url": "https://example.com/x"},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_ingest_url_invalid_scheme_returns_422(app_with_knowledge_overrides, db_session):
    project = ProjectORM(user_id="matt", name="P", default_model="claude-sonnet-4-6")
    db_session.add(project)
    await db_session.flush()

    resp = await app_with_knowledge_overrides.post(
        "/api/v1/knowledge/ingest/url",
        json={"project_id": str(project.id), "url": "ftp://example.com/x"},
    )
    # pydantic v2 HttpUrl rejects non-http schemes at the request boundary → 422.
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_ingest_url_ssrf_block_returns_400(app_with_knowledge_overrides, db_session):
    project = ProjectORM(user_id="matt", name="P", default_model="claude-sonnet-4-6")
    db_session.add(project)
    await db_session.flush()

    def boom(_url):
        raise ValueError("url resolves to disallowed address '10.0.0.1'")

    with patch("atlas_api.routers.knowledge.validate_url", side_effect=boom):
        resp = await app_with_knowledge_overrides.post(
            "/api/v1/knowledge/ingest/url",
            json={"project_id": str(project.id), "url": "https://internal.example/x"},
        )
    assert resp.status_code == 400
    assert "disallowed" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_ingest_url_fetch_failure_returns_502(app_with_knowledge_overrides, db_session):
    project = ProjectORM(user_id="matt", name="P", default_model="claude-sonnet-4-6")
    db_session.add(project)
    await db_session.flush()

    async def fake_parse_url(_url):
        raise RuntimeError("playwright nav timeout")

    with patch(
        "atlas_api.routers.knowledge.parse_url", side_effect=fake_parse_url
    ), patch(
        "atlas_api.routers.knowledge.validate_url", side_effect=lambda u: u
    ):
        resp = await app_with_knowledge_overrides.post(
            "/api/v1/knowledge/ingest/url",
            json={"project_id": str(project.id), "url": "https://example.com/x"},
        )
    assert resp.status_code == 502
    assert "fetch failed" in resp.json()["detail"].lower()


@pytest.mark.asyncio
async def test_ingest_url_extraction_empty_returns_502(app_with_knowledge_overrides, db_session):
    project = ProjectORM(user_id="matt", name="P", default_model="claude-sonnet-4-6")
    db_session.add(project)
    await db_session.flush()

    async def fake_parse_url(_url):
        raise ValueError("no extractable content from URL")

    with patch(
        "atlas_api.routers.knowledge.parse_url", side_effect=fake_parse_url
    ), patch(
        "atlas_api.routers.knowledge.validate_url", side_effect=lambda u: u
    ):
        resp = await app_with_knowledge_overrides.post(
            "/api/v1/knowledge/ingest/url",
            json={"project_id": str(project.id), "url": "https://example.com/x"},
        )
    assert resp.status_code == 502
    assert "extract" in resp.json()["detail"].lower()
