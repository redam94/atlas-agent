"""Integration tests for the RAG path in the chat WebSocket.

Uses FakeEmbedder + tmp Chroma + FakeProvider to exercise the full retrieve →
emit `rag.context` → inject context message → persist citations flow without
touching a real LLM or BGE-small.
"""

from __future__ import annotations

import contextlib
from datetime import UTC, datetime
from uuid import UUID, uuid4

import httpx
import pytest
import pytest_asyncio
from atlas_core.config import AtlasConfig
from atlas_core.db.orm import KnowledgeNodeORM, MessageORM, ProjectORM
from atlas_core.providers import FakeProvider
from atlas_core.providers.registry import ModelRegistry, ModelRouter
from atlas_knowledge.embeddings import FakeEmbedder
from atlas_knowledge.models.nodes import KnowledgeNode, KnowledgeNodeType
from atlas_knowledge.retrieval.retriever import Retriever
from atlas_knowledge.vector.chroma import ChromaVectorStore
from httpx_ws import aconnect_ws
from httpx_ws.transport import ASGIWebSocketTransport
from sqlalchemy import select

from atlas_graph.expansion import ExpansionSubgraph
from atlas_knowledge.retrieval.hybrid.hybrid import HybridRetriever
from atlas_knowledge.retrieval.hybrid.rerank import FakeReranker

from atlas_api.deps import get_model_router, get_retriever, get_session, get_settings
from atlas_api.main import app

# --- fixtures ------------------------------------------------------------


@pytest.fixture
def fake_router():
    fp = FakeProvider(model_id="fake-1", token_chunks=["alpha", " ", "beta"])
    reg = ModelRegistry()
    reg.register(fp)
    router = ModelRouter(reg)
    # Override select() to ignore project policy and always return our fake provider
    router.select = lambda project, model_override=None: fp  # type: ignore[assignment]
    return router


@pytest.fixture
def fake_settings():
    return AtlasConfig()  # default user_id="matt"


@pytest.fixture
def vector_store(tmp_path):
    return ChromaVectorStore(persist_dir=str(tmp_path / "chroma"), user_id="matt")


@pytest.fixture
def fake_embedder():
    return FakeEmbedder(dim=16)


@pytest.fixture
def retriever(vector_store, fake_embedder):
    return Retriever(embedder=fake_embedder, vector_store=vector_store)


@pytest_asyncio.fixture
async def set_overrides(db_session, fake_router, fake_settings, retriever):
    """Wire dependency overrides for these tests. db_session is the same per-test
    AsyncSession that the test will also use directly to seed/inspect rows.
    The lifespan-built retriever (real BGE + ./data/chroma) is replaced by the
    FakeEmbedder-backed one whose vector_store the test seeds via upsert."""

    async def _override_session():
        yield db_session

    app.dependency_overrides[get_session] = _override_session
    app.dependency_overrides[get_model_router] = lambda: fake_router
    app.dependency_overrides[get_settings] = lambda: fake_settings
    app.dependency_overrides[get_retriever] = lambda: retriever
    yield
    for dep in (get_session, get_model_router, get_settings, get_retriever):
        app.dependency_overrides.pop(dep, None)


async def _seed_project_and_chunks(
    db_session,
    vector_store: ChromaVectorStore,
    fake_embedder: FakeEmbedder,
    project_id: UUID,
    texts: list[str],
) -> list[KnowledgeNode]:
    project = ProjectORM(
        id=project_id,
        user_id="matt",
        name="P5",
        default_model="claude-sonnet-4-6",
    )
    db_session.add(project)
    await db_session.flush()

    parent_id = uuid4()
    # Insert parent document ORM row so FK from chunks is valid.
    parent_orm = KnowledgeNodeORM(
        id=parent_id,
        user_id="matt",
        project_id=project_id,
        type="document",
        parent_id=None,
        title="Parent Doc",
        text="",
        metadata_={},
    )
    db_session.add(parent_orm)

    chunks = [
        KnowledgeNode(
            id=uuid4(),
            user_id="matt",
            project_id=project_id,
            type=KnowledgeNodeType.CHUNK,
            parent_id=parent_id,
            title=f"Source {i}",
            text=text,
            created_at=datetime.now(UTC),
        )
        for i, text in enumerate(texts)
    ]
    # Insert chunk ORM rows so HybridRetriever.hydrate can resolve them from Postgres.
    for chunk in chunks:
        db_session.add(
            KnowledgeNodeORM(
                id=chunk.id,
                user_id=chunk.user_id,
                project_id=chunk.project_id,
                type="chunk",
                parent_id=parent_id,
                title=chunk.title,
                text=chunk.text,
                metadata_={},
            )
        )
    await db_session.flush()

    embeddings = await fake_embedder.embed_documents(texts)
    await vector_store.upsert(chunks, embeddings)
    return chunks


@contextlib.asynccontextmanager
async def _ws_client():
    """Open a WS to the running ASGI app via httpx-ws + ASGI transport."""
    transport = ASGIWebSocketTransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as http:
        yield http


async def _drain_events_until_done(ws):
    """Receive events until chat.done. Returns the list of events."""
    events: list[dict] = []
    while True:
        evt = await ws.receive_json()
        events.append(evt)
        if evt["type"] == "chat.done":
            return events


# --- tests ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_rag_happy_path_emits_event_and_persists_citations(
    set_overrides, db_session, vector_store, fake_embedder
):
    project_id = uuid4()
    chunks = await _seed_project_and_chunks(
        db_session,
        vector_store,
        fake_embedder,
        project_id,
        texts=["alpha context body", "beta context body"],
    )
    chunk_ids = {str(c.id) for c in chunks}

    session_id = uuid4()
    async with _ws_client() as http, aconnect_ws(f"/api/v1/ws/{session_id}", http) as ws:
        await ws.send_json(
            {
                "type": "chat.message",
                "payload": {
                    "text": "alpha",
                    "project_id": str(project_id),
                    "rag_enabled": True,
                    "top_k_context": 5,
                },
            }
        )
        events = await _drain_events_until_done(ws)

    types = [e["type"] for e in events]
    # The rag.context event must arrive before the first chat.token.
    assert "rag.context" in types
    rag_idx = types.index("rag.context")
    first_token_idx = types.index("chat.token")
    assert rag_idx < first_token_idx

    rag_evt = events[rag_idx]
    citations = rag_evt["payload"]["citations"]
    assert len(citations) == 2
    for cite in citations:
        assert set(cite.keys()) >= {"id", "title", "score", "chunk_id"}
        assert cite["chunk_id"] in chunk_ids
    assert sorted(c["id"] for c in citations) == [1, 2]

    rows = (
        (await db_session.execute(select(MessageORM).where(MessageORM.session_id == session_id)))
        .scalars()
        .all()
    )
    assistant = next(r for r in rows if r.role == "assistant")
    assert assistant.rag_context is not None
    assert len(assistant.rag_context) == 2
    persisted_ids = sorted(c["chunk_id"] for c in assistant.rag_context)
    emitted_ids = sorted(c["chunk_id"] for c in citations)
    assert persisted_ids == emitted_ids


@pytest.mark.asyncio
async def test_rag_disabled_skips_event_and_persists_null(
    set_overrides, db_session, vector_store, fake_embedder
):
    project_id = uuid4()
    await _seed_project_and_chunks(
        db_session,
        vector_store,
        fake_embedder,
        project_id,
        texts=["should be ignored"],
    )

    session_id = uuid4()
    async with _ws_client() as http, aconnect_ws(f"/api/v1/ws/{session_id}", http) as ws:
        await ws.send_json(
            {
                "type": "chat.message",
                "payload": {
                    "text": "hello",
                    "project_id": str(project_id),
                    "rag_enabled": False,
                },
            }
        )
        events = await _drain_events_until_done(ws)

    types = [e["type"] for e in events]
    assert "rag.context" not in types

    rows = (
        (await db_session.execute(select(MessageORM).where(MessageORM.session_id == session_id)))
        .scalars()
        .all()
    )
    assistant = next(r for r in rows if r.role == "assistant")
    assert assistant.rag_context is None


@pytest.mark.asyncio
async def test_empty_knowledge_base_skips_event(set_overrides, db_session):
    project_id = uuid4()
    project = ProjectORM(
        id=project_id, user_id="matt", name="P5-empty", default_model="claude-sonnet-4-6"
    )
    db_session.add(project)
    await db_session.flush()

    session_id = uuid4()
    async with _ws_client() as http, aconnect_ws(f"/api/v1/ws/{session_id}", http) as ws:
        await ws.send_json(
            {
                "type": "chat.message",
                "payload": {
                    "text": "anything",
                    "project_id": str(project_id),
                    "rag_enabled": True,
                },
            }
        )
        events = await _drain_events_until_done(ws)

    types = [e["type"] for e in events]
    assert "rag.context" not in types
    assert "chat.token" in types  # tokens still stream

    rows = (
        (await db_session.execute(select(MessageORM).where(MessageORM.session_id == session_id)))
        .scalars()
        .all()
    )
    assistant = next(r for r in rows if r.role == "assistant")
    assert assistant.rag_context is None


# --- hybrid-mode tests ---------------------------------------------------


@pytest_asyncio.fixture
async def hybrid_retriever(vector_store, fake_embedder, db_session):
    """HybridRetriever that reuses the test's FakeEmbedder + tmp Chroma + db_session.

    Uses a stub GraphStore returning an empty ExpansionSubgraph so the pipeline
    runs without Neo4j. Expansion and PPR will degrade gracefully — the WS-level
    behavior (rag.context event + citations) should match the vector path.
    """
    from contextlib import asynccontextmanager
    from unittest.mock import AsyncMock

    graph_store = AsyncMock()

    async def _expand(*, project_id, seeds, cap):
        return ExpansionSubgraph(nodes={s: 0.0 for s in seeds}, edges=[])

    graph_store.expand_chunks.side_effect = _expand

    @asynccontextmanager
    async def _session_cm():
        yield db_session

    class _SessionFactory:
        def __call__(self):
            return _session_cm()

    return HybridRetriever(
        embedder=fake_embedder,
        vector_store=vector_store,
        graph_store=graph_store,
        reranker=FakeReranker(scores={}),
        session_factory=_SessionFactory(),  # type: ignore[arg-type]
    )


@pytest_asyncio.fixture
async def set_overrides_hybrid(db_session, fake_router, fake_settings, hybrid_retriever):
    """Same as set_overrides but injects HybridRetriever instead of Retriever."""

    async def _override_session():
        yield db_session

    app.dependency_overrides[get_session] = _override_session
    app.dependency_overrides[get_model_router] = lambda: fake_router
    app.dependency_overrides[get_settings] = lambda: fake_settings
    app.dependency_overrides[get_retriever] = lambda: hybrid_retriever
    yield
    for dep in (get_session, get_model_router, get_settings, get_retriever):
        app.dependency_overrides.pop(dep, None)


@pytest.mark.asyncio
async def test_rag_happy_path_works_under_hybrid_mode(
    set_overrides_hybrid, db_session, vector_store, fake_embedder
):
    """Same scenario as test_rag_happy_path_emits_event_and_persists_citations,
    but with HybridRetriever. Asserts the rag.context event still arrives with
    citations sourced from the hybrid pipeline (BM25 + vector + degraded graph)."""
    project_id = uuid4()
    chunks = await _seed_project_and_chunks(
        db_session,
        vector_store,
        fake_embedder,
        project_id,
        texts=["alpha context body", "beta context body"],
    )
    chunk_ids = {str(c.id) for c in chunks}

    session_id = uuid4()
    async with _ws_client() as http, aconnect_ws(f"/api/v1/ws/{session_id}", http) as ws:
        await ws.send_json(
            {
                "type": "chat.message",
                "payload": {
                    "text": "alpha",
                    "project_id": str(project_id),
                    "rag_enabled": True,
                    "top_k_context": 5,
                },
            }
        )
        events = await _drain_events_until_done(ws)

    types = [e["type"] for e in events]
    assert "rag.context" in types
    rag_evt = events[types.index("rag.context")]
    citations = rag_evt["payload"]["citations"]
    assert len(citations) >= 1
    for cite in citations:
        assert cite["chunk_id"] in chunk_ids
