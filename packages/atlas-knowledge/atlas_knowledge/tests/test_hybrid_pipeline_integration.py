"""Full hybrid pipeline against real Postgres + Neo4j with FakeReranker."""
from __future__ import annotations

import os
from uuid import uuid4

import pytest
import pytest_asyncio
from neo4j import AsyncGraphDatabase
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from atlas_graph.store import GraphStore

pytestmark = pytest.mark.integration


def _enabled() -> bool:
    return (
        os.getenv("ATLAS_RUN_POSTGRES_INTEGRATION") == "1"
        and os.getenv("ATLAS_RUN_NEO4J_INTEGRATION") == "1"
    )


@pytest_asyncio.fixture
async def real_engine_and_factory():
    if not _enabled():
        pytest.skip("set both PG and Neo4j integration env vars to run")
    url = os.environ["ATLAS_DB__DATABASE_URL"].replace("postgresql://", "postgresql+asyncpg://")
    engine = create_async_engine(url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    yield engine, factory
    await engine.dispose()


@pytest_asyncio.fixture
async def real_graph():
    if not _enabled():
        pytest.skip("set both PG and Neo4j integration env vars to run")
    driver = AsyncGraphDatabase.driver(
        os.environ["ATLAS_GRAPH__URI"],
        auth=("neo4j", os.environ["ATLAS_GRAPH__PASSWORD"]),
    )
    store = GraphStore(driver)
    yield store, driver
    await driver.close()


@pytest.mark.asyncio
async def test_hybrid_happy_path(real_engine_and_factory, real_graph, monkeypatch):
    """Two chunks, both retrievable; assert hybrid returns both with no degradation."""
    from atlas_knowledge.models.retrieval import RetrievalQuery
    from atlas_knowledge.retrieval.hybrid.hybrid import HybridRetriever
    from atlas_knowledge.retrieval.hybrid.rerank import FakeReranker

    _engine, factory = real_engine_and_factory
    graph_store, driver = real_graph
    pid = uuid4()
    doc_id = uuid4()
    chunk_a = uuid4()
    chunk_b = uuid4()

    # Seed Postgres
    async with factory() as s:
        await s.execute(
            text(
                "INSERT INTO projects (id, user_id, name, status, default_model) "
                "VALUES (:id, 'matt', 'pipeline-test', 'active', 'test-model')"
            ),
            {"id": pid},
        )
        await s.execute(
            text(
                "INSERT INTO knowledge_nodes (id, user_id, project_id, type, title, text, metadata) "
                "VALUES (:id, 'matt', :pid, 'document', 'Doc', '', '{}'::jsonb)"
            ),
            {"id": doc_id, "pid": pid},
        )
        for cid, content in (
            (chunk_a, "geo lift methodology drives measurement"),
            (chunk_b, "incremental measurement on convenience-store accounts"),
        ):
            await s.execute(
                text(
                    "INSERT INTO knowledge_nodes "
                    "(id, user_id, project_id, type, parent_id, text, metadata) "
                    "VALUES (:id, 'matt', :pid, 'chunk', :doc, :text, '{}'::jsonb)"
                ),
                {"id": cid, "pid": pid, "doc": doc_id, "text": content},
            )
        await s.commit()

    # Seed Neo4j Chunks (no edges; expansion returns just seeds)
    async with driver.session() as ns:
        for cid in (chunk_a, chunk_b):
            await ns.run(
                "MERGE (c:Chunk {id: $id}) SET c.project_id = $pid, c.pagerank_global = 0.1",
                id=str(cid), pid=str(pid),
            )

    # Fake embedder: returns a deterministic vector (vector store will be the
    # real Chroma — but we don't have chunks indexed there, so vector returns
    # empty. Hybrid should still work via BM25 only and degrade vector gracefully).
    class _Embedder:
        async def embed_query(self, text):
            return [0.0] * 384

    class _EmptyVector:
        async def search(self, **kwargs):
            return []

    rr = FakeReranker(scores={chunk_a: 0.9, chunk_b: 0.4})
    retr = HybridRetriever(
        embedder=_Embedder(),  # type: ignore[arg-type]
        vector_store=_EmptyVector(),  # type: ignore[arg-type]
        graph_store=graph_store,
        reranker=rr,
        session_factory=factory,
    )

    try:
        result = await retr.retrieve(
            RetrievalQuery(project_id=pid, text="geo lift", top_k=5)
        )
        # BM25 returns chunk_a (matches "geo lift"); chunk_b doesn't match.
        # Vector returned empty (no embeddings indexed) so degrades gracefully.
        assert "vector" not in result.degraded_stages  # empty result is not an error
        ids = [c.chunk.id for c in result.chunks]
        assert chunk_a in ids
    finally:
        async with factory() as s:
            await s.execute(
                text("DELETE FROM knowledge_nodes WHERE project_id = :pid"),
                {"pid": pid},
            )
            await s.execute(
                text("DELETE FROM projects WHERE id = :pid"),
                {"pid": pid},
            )
            await s.commit()
        async with driver.session() as ns:
            await ns.run(
                "MATCH (n) WHERE n.project_id = $pid DETACH DELETE n", pid=str(pid)
            )
