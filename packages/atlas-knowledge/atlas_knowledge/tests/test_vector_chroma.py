"""Tests for ChromaVectorStore — uses tmp_path-backed embedded Chroma."""
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from atlas_knowledge.models.nodes import KnowledgeNode, KnowledgeNodeType
from atlas_knowledge.vector.chroma import ChromaVectorStore


def _chunk(project_id, parent_id, text="x", meta=None):
    return KnowledgeNode(
        id=uuid4(),
        user_id="matt",
        project_id=project_id,
        type=KnowledgeNodeType.CHUNK,
        parent_id=parent_id,
        text=text,
        metadata=meta or {},
        created_at=datetime.now(UTC),
    )


@pytest.fixture
def store(tmp_path):
    return ChromaVectorStore(persist_dir=str(tmp_path), user_id="matt")


async def test_upsert_then_search_returns_chunk(store):
    project_id = uuid4()
    parent_id = uuid4()
    chunk = _chunk(project_id, parent_id, text="hello world")
    await store.upsert([chunk], [[0.1, 0.2, 0.3]])

    results = await store.search(query_embedding=[0.1, 0.2, 0.3], top_k=5)
    assert len(results) == 1
    assert results[0].chunk.id == chunk.id
    assert results[0].chunk.text == "hello world"


async def test_search_respects_project_filter(store):
    proj_a = uuid4()
    proj_b = uuid4()
    parent = uuid4()
    chunk_a = _chunk(proj_a, parent, text="a-text")
    chunk_b = _chunk(proj_b, parent, text="b-text")
    await store.upsert([chunk_a, chunk_b], [[0.1, 0, 0], [0.1, 0, 0]])

    results = await store.search(
        query_embedding=[0.1, 0, 0],
        top_k=5,
        filter={"project_id": str(proj_a)},
    )
    ids = {r.chunk.id for r in results}
    assert ids == {chunk_a.id}


async def test_delete_removes_chunks(store):
    pid = uuid4()
    parent = uuid4()
    chunk = _chunk(pid, parent)
    await store.upsert([chunk], [[0.1, 0.2, 0.3]])
    await store.delete([chunk.id])
    results = await store.search(query_embedding=[0.1, 0.2, 0.3], top_k=5)
    assert results == []


async def test_upsert_dimension_mismatch_raises(store):
    pid = uuid4()
    parent = uuid4()
    a = _chunk(pid, parent)
    b = _chunk(pid, parent)
    with pytest.raises(ValueError):
        await store.upsert([a, b], [[0.1, 0.2]])  # 2 chunks, 1 embedding
