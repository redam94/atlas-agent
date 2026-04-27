"""Tests for Retriever using FakeEmbedder + tmp Chroma."""
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from atlas_knowledge.embeddings import FakeEmbedder
from atlas_knowledge.models.nodes import KnowledgeNode, KnowledgeNodeType
from atlas_knowledge.models.retrieval import RetrievalQuery
from atlas_knowledge.retrieval.retriever import Retriever
from atlas_knowledge.vector.chroma import ChromaVectorStore


@pytest.fixture
def store(tmp_path):
    return ChromaVectorStore(persist_dir=str(tmp_path), user_id="matt")


@pytest.fixture
def retriever(store):
    return Retriever(embedder=FakeEmbedder(dim=16), vector_store=store)


async def _seed(store, embedder, project_id, parent_id, texts):
    chunks = [
        KnowledgeNode(
            id=uuid4(),
            user_id="matt",
            project_id=project_id,
            type=KnowledgeNodeType.CHUNK,
            parent_id=parent_id,
            text=t,
            created_at=datetime.now(UTC),
        )
        for t in texts
    ]
    embeddings = await embedder.embed_documents(texts)
    await store.upsert(chunks, embeddings)
    return chunks


async def test_retrieve_returns_top_k(store, retriever):
    pid = uuid4()
    parent = uuid4()
    chunks = await _seed(store, retriever._embedder, pid, parent, ["foo", "bar", "baz", "foo"])  # noqa: SLF001
    res = await retriever.retrieve(RetrievalQuery(project_id=pid, text="foo", top_k=2))
    assert res.query == "foo"
    assert len(res.chunks) == 2
    assert all(sc.chunk.id in {c.id for c in chunks} for sc in res.chunks)


async def test_retrieve_filters_by_project(store, retriever):
    proj_a = uuid4()
    proj_b = uuid4()
    parent = uuid4()
    await _seed(store, retriever._embedder, proj_a, parent, ["alpha"])  # noqa: SLF001
    await _seed(store, retriever._embedder, proj_b, parent, ["alpha"])  # noqa: SLF001

    res = await retriever.retrieve(RetrievalQuery(project_id=proj_a, text="alpha", top_k=5))
    assert all(sc.chunk.project_id == proj_a for sc in res.chunks)


async def test_retrieve_top_k_default(store, retriever):
    pid = uuid4()
    parent = uuid4()
    await _seed(store, retriever._embedder, pid, parent, [f"text-{i}" for i in range(10)])  # noqa: SLF001
    res = await retriever.retrieve(RetrievalQuery(project_id=pid, text="text-3"))
    assert len(res.chunks) <= 8  # top_k default
