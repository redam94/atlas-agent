"""HybridRetriever orchestrator with mocked component edges.

Exercises pipeline wiring + per-stage degradation policy without real Postgres
or Neo4j. The integration test (test_hybrid_pipeline_integration.py) covers
the same pipeline against real infra.
"""
from __future__ import annotations

from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from atlas_graph.expansion import ExpansionSubgraph
from atlas_knowledge.models.nodes import KnowledgeNode, KnowledgeNodeType
from atlas_knowledge.models.retrieval import RetrievalQuery, ScoredChunk
from atlas_knowledge.retrieval.hybrid.hybrid import HybridRetriever
from atlas_knowledge.retrieval.hybrid.hydrate import ChunkText
from atlas_knowledge.retrieval.hybrid.rerank import FakeReranker


def _node(cid, text="t") -> KnowledgeNode:
    from datetime import UTC, datetime
    return KnowledgeNode(
        id=cid,
        user_id="matt",
        project_id=uuid4(),
        type=KnowledgeNodeType.CHUNK,
        text=text,
        title=None,
        metadata={},
        created_at=datetime.now(UTC),
    )


def _scored(cid, score=0.5) -> ScoredChunk:
    return ScoredChunk(chunk=_node(cid), score=score, parent_title="Doc")


@pytest.fixture
def hybrid_with_mocks(monkeypatch):
    """Build a HybridRetriever where every external dep is mocked."""
    embedder = AsyncMock()
    embedder.embed_query.return_value = [0.1] * 8
    vector_store = AsyncMock()
    graph_store = AsyncMock()
    session_factory = AsyncMock()

    return {
        "embedder": embedder,
        "vector_store": vector_store,
        "graph_store": graph_store,
        "session_factory": session_factory,
    }


@pytest.mark.asyncio
async def test_happy_path_returns_top_k_with_no_degradation(hybrid_with_mocks, monkeypatch):
    a, b, c = uuid4(), uuid4(), uuid4()
    # Vector returns [(a, 1), (b, 2)]
    hybrid_with_mocks["vector_store"].search.return_value = [_scored(a), _scored(b)]
    # BM25 mocked via monkeypatch on the bm25 module
    from atlas_knowledge.retrieval.hybrid import bm25 as bm25_mod
    from atlas_knowledge.retrieval.hybrid import hydrate as hydrate_mod

    async def _bm25(session, project_id, query, top_k):
        return [(a, 1), (c, 2)]
    monkeypatch.setattr(bm25_mod, "search", _bm25)

    async def _hydrate(session, ids):
        from datetime import UTC, datetime
        return {
            i: ChunkText(
                id=i,
                user_id="matt",
                text="hello",
                parent_id=uuid4(),
                parent_title="Doc",
                created_at=datetime.now(UTC),
            )
            for i in ids
        }
    monkeypatch.setattr(hydrate_mod, "hydrate", _hydrate)

    hybrid_with_mocks["graph_store"].expand_chunks.return_value = ExpansionSubgraph(
        nodes={a: 0.5, b: 0.3, c: 0.2}, edges=[(a, b, 1.0), (a, c, 1.0)]
    )

    rr = FakeReranker(scores={a: 0.95, b: 0.7, c: 0.4})

    # Patch session_scope so the with-block is a no-op.
    from contextlib import asynccontextmanager
    @asynccontextmanager
    async def _fake_session(*_args, **_kwargs):
        yield AsyncMock()
    monkeypatch.setattr(
        "atlas_knowledge.retrieval.hybrid.hybrid.session_scope", _fake_session
    )

    retr = HybridRetriever(
        embedder=hybrid_with_mocks["embedder"],
        vector_store=hybrid_with_mocks["vector_store"],
        graph_store=hybrid_with_mocks["graph_store"],
        reranker=rr,
        session_factory=hybrid_with_mocks["session_factory"],
    )
    result = await retr.retrieve(
        RetrievalQuery(project_id=uuid4(), text="q", top_k=2)
    )
    assert result.degraded_stages == []
    assert len(result.chunks) == 2
    # `a` should rank first (highest rerank * (log + small) * ppr).
    assert result.chunks[0].chunk.id == a


@pytest.mark.asyncio
async def test_expansion_failure_degrades_gracefully(hybrid_with_mocks, monkeypatch):
    a = uuid4()
    hybrid_with_mocks["vector_store"].search.return_value = [_scored(a)]
    from atlas_knowledge.retrieval.hybrid import bm25 as bm25_mod
    from atlas_knowledge.retrieval.hybrid import hydrate as hydrate_mod

    async def _bm25(*a, **kw):
        return [(uuid4(), 1)]
    monkeypatch.setattr(bm25_mod, "search", _bm25)

    async def _hydrate(session, ids):
        from datetime import UTC, datetime
        return {
            i: ChunkText(
                id=i,
                user_id="matt",
                text="x",
                parent_id=uuid4(),
                parent_title=None,
                created_at=datetime.now(UTC),
            )
            for i in ids
        }
    monkeypatch.setattr(hydrate_mod, "hydrate", _hydrate)

    hybrid_with_mocks["graph_store"].expand_chunks.side_effect = RuntimeError("neo4j down")

    from contextlib import asynccontextmanager
    @asynccontextmanager
    async def _fake_session(*_args, **_kwargs):
        yield AsyncMock()
    monkeypatch.setattr(
        "atlas_knowledge.retrieval.hybrid.hybrid.session_scope", _fake_session
    )

    rr = FakeReranker(scores={})
    retr = HybridRetriever(
        embedder=hybrid_with_mocks["embedder"],
        vector_store=hybrid_with_mocks["vector_store"],
        graph_store=hybrid_with_mocks["graph_store"],
        reranker=rr,
        session_factory=hybrid_with_mocks["session_factory"],
    )
    result = await retr.retrieve(
        RetrievalQuery(project_id=uuid4(), text="q", top_k=2)
    )
    assert "expansion" in result.degraded_stages
    assert "personalized_pagerank" in result.degraded_stages  # PPR drops because subgraph empty
    assert len(result.chunks) >= 1


@pytest.mark.asyncio
async def test_both_bm25_and_vector_fail_raises(hybrid_with_mocks, monkeypatch):
    hybrid_with_mocks["vector_store"].search.side_effect = RuntimeError("chroma down")
    from atlas_knowledge.retrieval.hybrid import bm25 as bm25_mod

    async def _bm25(*a, **kw):
        raise RuntimeError("postgres down")
    monkeypatch.setattr(bm25_mod, "search", _bm25)

    from contextlib import asynccontextmanager
    @asynccontextmanager
    async def _fake_session(*_args, **_kwargs):
        yield AsyncMock()
    monkeypatch.setattr(
        "atlas_knowledge.retrieval.hybrid.hybrid.session_scope", _fake_session
    )

    rr = FakeReranker(scores={})
    retr = HybridRetriever(
        embedder=hybrid_with_mocks["embedder"],
        vector_store=hybrid_with_mocks["vector_store"],
        graph_store=hybrid_with_mocks["graph_store"],
        reranker=rr,
        session_factory=hybrid_with_mocks["session_factory"],
    )
    with pytest.raises(RuntimeError):
        await retr.retrieve(
            RetrievalQuery(project_id=uuid4(), text="q", top_k=2)
        )


@pytest.mark.asyncio
async def test_empty_ppr_dict_marks_personalized_pagerank_degraded(hybrid_with_mocks, monkeypatch):
    """When personalized() returns {} despite a non-empty subgraph, mark PPR degraded
    so the score formula falls back to rerank_score · log1p(pagerank_global)."""
    a, b = uuid4(), uuid4()
    hybrid_with_mocks["vector_store"].search.return_value = [_scored(a), _scored(b)]
    from atlas_knowledge.retrieval.hybrid import bm25 as bm25_mod
    from atlas_knowledge.retrieval.hybrid import hydrate as hydrate_mod
    from atlas_knowledge.retrieval.hybrid import pagerank as pr_mod

    async def _bm25(*args, **kw):
        return []
    monkeypatch.setattr(bm25_mod, "search", _bm25)

    async def _hydrate(session, ids):
        from datetime import UTC, datetime
        return {
            i: ChunkText(
                id=i, user_id="matt", text="t",
                parent_id=uuid4(), parent_title=None,
                created_at=datetime.now(UTC),
            )
            for i in ids
        }
    monkeypatch.setattr(hydrate_mod, "hydrate", _hydrate)

    # Subgraph has nodes, but personalized() returns empty (simulating filtered seeds)
    hybrid_with_mocks["graph_store"].expand_chunks.return_value = ExpansionSubgraph(
        nodes={a: 0.5, b: 0.3}, edges=[]
    )
    monkeypatch.setattr(pr_mod, "personalized", lambda *a, **kw: {})

    from contextlib import asynccontextmanager
    @asynccontextmanager
    async def _fake_session(*_args, **_kwargs):
        yield AsyncMock()
    monkeypatch.setattr(
        "atlas_knowledge.retrieval.hybrid.hybrid.session_scope", _fake_session
    )

    rr = FakeReranker(scores={a: 0.9, b: 0.5})
    retr = HybridRetriever(
        embedder=hybrid_with_mocks["embedder"],
        vector_store=hybrid_with_mocks["vector_store"],
        graph_store=hybrid_with_mocks["graph_store"],
        reranker=rr,
        session_factory=hybrid_with_mocks["session_factory"],
    )
    result = await retr.retrieve(
        RetrievalQuery(project_id=uuid4(), text="q", top_k=2)
    )
    assert "personalized_pagerank" in result.degraded_stages
    # Final scores are non-zero (rerank_score · log1p path)
    assert len(result.chunks) == 2
    assert all(c.score > 0 for c in result.chunks)
