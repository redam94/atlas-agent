"""GraphStore.run_pagerank — projection + write + drop, drop runs even on failure."""
from __future__ import annotations

from uuid import uuid4

import pytest

from atlas_graph.errors import GraphUnavailableError
from atlas_graph.store import GraphStore


@pytest.mark.asyncio
async def test_run_pagerank_invokes_project_write_drop_in_order(fake_async_driver):
    pid = uuid4()
    store = GraphStore(fake_async_driver)
    await store.run_pagerank(project_id=pid)

    queries = [c.query for c in fake_async_driver.calls]
    project_idx = next(i for i, q in enumerate(queries) if "gds.graph.project.cypher" in q)
    write_idx = next(i for i, q in enumerate(queries) if "gds.pageRank.write" in q)
    drop_idx = next(i for i, q in enumerate(queries) if "gds.graph.drop" in q)
    assert project_idx < write_idx < drop_idx


@pytest.mark.asyncio
async def test_run_pagerank_drops_projection_even_when_write_fails(fake_async_driver):
    """Failure mode: gds.pageRank.write raises → projection still dropped."""
    fake_async_driver.make_tx_raise_on(lambda q: "gds.pageRank.write" in q)

    store = GraphStore(fake_async_driver)
    with pytest.raises((RuntimeError, GraphUnavailableError)):
        await store.run_pagerank(project_id=uuid4())

    queries = [c.query for c in fake_async_driver.calls]
    assert any("gds.graph.drop" in q for q in queries)
