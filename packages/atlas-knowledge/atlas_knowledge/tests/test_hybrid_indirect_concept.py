"""Plan 4 Definition-of-Done: hybrid finds an indirectly-connected chunk that vector misses.

Two chunks share an Entity (e.g., 'CircleK') but no surface keywords. A query
that names the Entity must surface both chunks under hybrid, only one under
vector-only.
"""
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
async def stack():
    if not _enabled():
        pytest.skip("set both integration env vars to run")
    pg_url = os.environ["ATLAS_DB__DATABASE_URL"].replace("postgresql://", "postgresql+asyncpg://")
    engine = create_async_engine(pg_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    driver = AsyncGraphDatabase.driver(
        os.environ["ATLAS_GRAPH__URI"],
        auth=("neo4j", os.environ["ATLAS_GRAPH__PASSWORD"]),
    )
    yield engine, factory, driver
    await engine.dispose()
    await driver.close()


@pytest.mark.asyncio
async def test_indirect_concept_recovered_by_hybrid_only(stack):
    from atlas_knowledge.models.retrieval import RetrievalQuery
    from atlas_knowledge.retrieval.hybrid.hybrid import HybridRetriever
    from atlas_knowledge.retrieval.hybrid.rerank import FakeReranker

    _engine, factory, driver = stack
    pid = uuid4()
    doc_id = uuid4()
    chunk_query_match = uuid4()  # Mentions CircleK by name -> matches both keyword and entity
    chunk_indirect = uuid4()      # Same Entity, no keyword overlap with query
    eid = uuid4()

    async with factory() as s:
        await s.execute(
            text(
                "INSERT INTO projects (id, user_id, name, status, default_model) "
                "VALUES (:id, 'matt', 'circlek', 'active', 'test-model')"
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
        await s.execute(
            text(
                "INSERT INTO knowledge_nodes (id, user_id, project_id, type, parent_id, text, metadata) "
                "VALUES (:id, 'matt', :pid, 'chunk', :doc, "
                ":text, '{}'::jsonb)"
            ),
            {
                "id": chunk_query_match, "pid": pid, "doc": doc_id,
                "text": "CircleK proposal scoping notes",
            },
        )
        await s.execute(
            text(
                "INSERT INTO knowledge_nodes (id, user_id, project_id, type, parent_id, text, metadata) "
                "VALUES (:id, 'matt', :pid, 'chunk', :doc, :text, '{}'::jsonb)"
            ),
            {
                "id": chunk_indirect, "pid": pid, "doc": doc_id,
                "text": "convenience store geo-lift methodology summary",
            },
        )
        await s.commit()

    async with driver.session() as ns:
        for cid in (chunk_query_match, chunk_indirect):
            await ns.run(
                "MERGE (c:Chunk {id: $id}) SET c.project_id = $pid, c.pagerank_global = 0.1",
                id=str(cid), pid=str(pid),
            )
        await ns.run(
            "MERGE (e:Entity {project_id: $pid, name: 'circlek', type: 'CLIENT'}) "
            "SET e.id = $eid",
            pid=str(pid), eid=str(eid),
        )
        for cid in (chunk_query_match, chunk_indirect):
            await ns.run(
                "MATCH (c:Chunk {id: $c}), (e:Entity {id: $eid}) "
                "MERGE (c)-[:REFERENCES]->(e)",
                c=str(cid), eid=str(eid),
            )

    class _Embedder:
        async def embed_query(self, t):
            return [0.0] * 384

    class _EmptyVector:
        async def search(self, **kw):
            return []

    rr = FakeReranker(scores={chunk_query_match: 0.9, chunk_indirect: 0.7})
    retr = HybridRetriever(
        embedder=_Embedder(),  # type: ignore[arg-type]
        vector_store=_EmptyVector(),  # type: ignore[arg-type]
        graph_store=GraphStore(driver),
        reranker=rr,
        session_factory=factory,
    )

    try:
        result = await retr.retrieve(
            RetrievalQuery(project_id=pid, text="CircleK", top_k=5)
        )
        ids = [c.chunk.id for c in result.chunks]
        # Hybrid retrieves both: BM25 finds the keyword match; expansion via
        # the shared Entity surfaces the indirect chunk.
        assert chunk_query_match in ids
        assert chunk_indirect in ids
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
