"""Cypher-shape tests for GraphStore.fetch_top_entities and fetch_subgraph_by_seeds."""
from __future__ import annotations

from uuid import uuid4

import pytest

from atlas_graph.store import GraphStore


@pytest.mark.asyncio
async def test_fetch_top_entities_runs_one_read_with_pid_and_limit(fake_async_driver):
    store = GraphStore(fake_async_driver)  # type: ignore[arg-type]
    pid = uuid4()
    await store.fetch_top_entities(project_id=pid, limit=15)

    queries = [c.query for c in fake_async_driver.calls]
    assert any("Entity" in q for q in queries)
    assert any("pagerank" in q.lower() for q in queries)
    # The pid is passed in
    assert any(c.kwargs.get("pid") == str(pid) for c in fake_async_driver.calls)
    # The limit is passed in
    assert any(c.kwargs.get("limit") == 15 for c in fake_async_driver.calls)


@pytest.mark.asyncio
async def test_fetch_top_entities_returns_nodes_edges_pair(fake_async_driver):
    store = GraphStore(fake_async_driver)  # type: ignore[arg-type]
    nodes, edges = await store.fetch_top_entities(project_id=uuid4(), limit=10)
    # Fake driver returns empty result; assert the shape, not content.
    assert isinstance(nodes, list)
    assert isinstance(edges, list)


@pytest.mark.asyncio
async def test_fetch_subgraph_by_seeds_runs_one_read_with_seeds(fake_async_driver):
    store = GraphStore(fake_async_driver)  # type: ignore[arg-type]
    pid = uuid4()
    seeds = [uuid4(), uuid4()]
    await store.fetch_subgraph_by_seeds(
        project_id=pid, seed_ids=seeds, neighbors_per_seed=25
    )

    seed_strs = [str(s) for s in seeds]
    assert any(c.kwargs.get("seeds") == seed_strs for c in fake_async_driver.calls)
    assert any(c.kwargs.get("pid") == str(pid) for c in fake_async_driver.calls)
    assert any(c.kwargs.get("cap") == 25 for c in fake_async_driver.calls)


@pytest.mark.asyncio
async def test_fetch_subgraph_by_seeds_empty_seeds_short_circuits(fake_async_driver):
    store = GraphStore(fake_async_driver)  # type: ignore[arg-type]
    nodes, edges = await store.fetch_subgraph_by_seeds(
        project_id=uuid4(), seed_ids=[], neighbors_per_seed=25
    )
    assert nodes == []
    assert edges == []
    assert fake_async_driver.calls == []  # no Cypher run
