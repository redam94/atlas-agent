"""GraphStore.write_entities — fake-driver tests."""
from __future__ import annotations

from uuid import uuid4

import pytest

from atlas_graph.ingestion.ner import Entity
from atlas_graph.protocols import ChunkWithText
from atlas_graph.store import GraphStore


class _StubNer:
    """In-test substitute for NerExtractor; returns canned mapping per chunk."""

    def __init__(self, mapping: dict):
        self._mapping = mapping

    async def extract_batch(self, chunks):
        return {chunk_id: self._mapping.get(chunk_id, []) for chunk_id, _ in chunks}


@pytest.mark.asyncio
async def test_write_entities_unwinds_entities_and_references(fake_async_driver):
    """write_entities issues two UNWIND statements: MERGE Entity + MERGE REFERENCES."""
    pid = uuid4()
    cid_a = uuid4()
    cid_b = uuid4()
    ner = _StubNer({
        cid_a: [Entity(name="CircleK", type="CLIENT")],
        cid_b: [Entity(name="MMM", type="METHOD")],
    })
    store = GraphStore(fake_async_driver, ner_extractor=ner)

    await store.write_entities(
        project_id=pid,
        chunks=[
            ChunkWithText(id=cid_a, text="..."),
            ChunkWithText(id=cid_b, text="..."),
        ],
    )

    queries = [c.query for c in fake_async_driver.calls]
    assert any("MERGE (e:Entity" in q for q in queries)
    assert any("MERGE (c)-[:REFERENCES]->(e)" in q for q in queries)


@pytest.mark.asyncio
async def test_write_entities_dedupes_repeated_entity_within_batch(fake_async_driver):
    """If two chunks each reference 'CircleK' CLIENT, MERGE Entity row is deduped."""
    pid = uuid4()
    cid_a, cid_b = uuid4(), uuid4()
    ner = _StubNer({
        cid_a: [Entity(name="CircleK", type="CLIENT")],
        cid_b: [Entity(name="CircleK", type="CLIENT")],
    })
    store = GraphStore(fake_async_driver, ner_extractor=ner)

    await store.write_entities(
        project_id=pid,
        chunks=[ChunkWithText(id=cid_a, text="x"), ChunkWithText(id=cid_b, text="y")],
    )

    entity_calls = [c for c in fake_async_driver.calls if "MERGE (e:Entity" in c.query]
    assert len(entity_calls) == 1
    assert len(entity_calls[0].kwargs["entities"]) == 1
    ref_calls = [c for c in fake_async_driver.calls if "REFERENCES" in c.query]
    assert len(ref_calls) == 1
    assert len(ref_calls[0].kwargs["references"]) == 2


@pytest.mark.asyncio
async def test_write_entities_skips_when_chunks_empty(fake_async_driver):
    """No chunks → no Cypher calls."""
    store = GraphStore(fake_async_driver, ner_extractor=_StubNer({}))
    await store.write_entities(project_id=uuid4(), chunks=[])
    assert fake_async_driver.calls == []
