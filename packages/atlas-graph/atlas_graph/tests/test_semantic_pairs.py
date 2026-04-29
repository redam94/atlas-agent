"""GraphStore.merge_semantic_near — fake-driver tests for canonical pair writes."""
from __future__ import annotations

from uuid import uuid4

import pytest

from atlas_graph.store import GraphStore


@pytest.mark.asyncio
async def test_merge_semantic_near_unwinds_pairs(fake_async_driver):
    """merge_semantic_near issues a single UNWIND MERGE for all pairs."""
    a, b, c = uuid4(), uuid4(), uuid4()
    pairs = [(a, b, 0.91), (a, c, 0.88)]
    store = GraphStore(fake_async_driver)

    await store.merge_semantic_near(pairs=pairs)

    queries = [call.query for call in fake_async_driver.calls]
    assert any(
        "UNWIND $pairs AS p" in q and "MERGE (x)-[r:SEMANTICALLY_NEAR]-(y)" in q
        for q in queries
    )
    write_call = next(
        c for c in fake_async_driver.calls if "SEMANTICALLY_NEAR" in c.query
    )
    written = write_call.kwargs["pairs"]
    # IDs are stringified for Cypher.
    assert {p["a"] for p in written} | {p["b"] for p in written} == {str(a), str(b), str(c)}


@pytest.mark.asyncio
async def test_merge_semantic_near_no_op_on_empty(fake_async_driver):
    store = GraphStore(fake_async_driver)
    await store.merge_semantic_near(pairs=[])
    assert fake_async_driver.calls == []


@pytest.mark.asyncio
async def test_merge_semantic_near_stores_cosine_on_edge(fake_async_driver):
    a, b = uuid4(), uuid4()
    store = GraphStore(fake_async_driver)
    await store.merge_semantic_near(pairs=[(a, b, 0.91)])
    write_call = next(
        c for c in fake_async_driver.calls if "SEMANTICALLY_NEAR" in c.query
    )
    written = write_call.kwargs["pairs"][0]
    assert written["cosine"] == 0.91
