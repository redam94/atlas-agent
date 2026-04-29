"""Cypher-shape tests for GraphStore.expand_chunks via the fake driver."""
from __future__ import annotations

from uuid import uuid4

import pytest

from atlas_graph.expansion import merge_neighbors_with_budget
from atlas_graph.store import GraphStore


@pytest.mark.asyncio
async def test_expand_chunks_runs_two_cypher_queries(fake_async_driver):
    """expand_chunks runs the SN walk and the REFERENCES walk."""
    store = GraphStore(fake_async_driver)  # type: ignore[arg-type]
    pid = uuid4()
    seeds = [uuid4(), uuid4()]
    sub = await store.expand_chunks(project_id=pid, seeds=seeds, cap=100)

    queries = [c.query for c in fake_async_driver.calls]
    # Two read queries: SEMANTICALLY_NEAR neighbors, REFERENCES neighbors.
    assert any("SEMANTICALLY_NEAR" in q for q in queries)
    assert any("REFERENCES" in q for q in queries)
    # Seeds always present with weight 0.0 by convention (pagerank_global is 0 if absent)
    assert all(seed in sub.nodes for seed in seeds)


@pytest.mark.asyncio
async def test_expand_chunks_empty_seeds_returns_empty(fake_async_driver):
    store = GraphStore(fake_async_driver)  # type: ignore[arg-type]
    sub = await store.expand_chunks(project_id=uuid4(), seeds=[], cap=100)
    assert sub.nodes == {}
    assert sub.edges == []


def test_budget_split_caps_at_total():
    seeds = [uuid4() for _ in range(2)]
    sn_rows = [(seeds[0], uuid4(), 0.9, 0.1, 0.1) for _ in range(40)]
    ref_rows = [(seeds[0], uuid4(), 5.0, 0.1, 0.1) for _ in range(40)]
    sub = merge_neighbors_with_budget(seeds, sn_rows, ref_rows, {}, cap=20)
    # 2 seeds + up to 18 neighbors total
    assert len(sub.nodes) <= 20
    assert all(s in sub.nodes for s in seeds)


def test_budget_split_rolls_over_surplus():
    seeds = [uuid4()]
    sn_rows = [(seeds[0], uuid4(), 0.9, 0.0, 0.0) for _ in range(2)]  # only 2 SN neighbors
    ref_rows = [(seeds[0], uuid4(), 5.0, 0.0, 0.0) for _ in range(20)]  # many REF neighbors
    sub = merge_neighbors_with_budget(seeds, sn_rows, ref_rows, {}, cap=11)
    # 1 seed + 10 budget. SN supplies 2; REF takes the surplus (5+5=10).
    assert len(sub.nodes) == 1 + 2 + (11 - 1 - 2)


def test_budget_split_dedupes_seeds_from_neighbors():
    seeds = [uuid4()]
    # SN edge points back to the seed itself (self-loop hypothetical) — should not duplicate.
    sn_rows = [(seeds[0], seeds[0], 0.9, 0.0, 0.0)]
    sub = merge_neighbors_with_budget(seeds, sn_rows, [], {}, cap=10)
    assert len(sub.nodes) == 1
