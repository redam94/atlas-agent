"""Cypher-shape tests for GraphStore.list_entities."""
from __future__ import annotations

from uuid import uuid4

import pytest

from atlas_graph.store import GraphStore


@pytest.mark.asyncio
async def test_list_entities_runs_read_with_pid_prefix_limit(fake_async_driver):
    store = GraphStore(fake_async_driver)  # type: ignore[arg-type]
    pid = uuid4()
    await store.list_entities(project_id=pid, prefix="Llam", limit=5)

    assert any(c.kwargs.get("pid") == str(pid) for c in fake_async_driver.calls)
    assert any(c.kwargs.get("prefix") == "Llam" for c in fake_async_driver.calls)
    assert any(c.kwargs.get("limit") == 5 for c in fake_async_driver.calls)
    assert any("Entity" in c.query for c in fake_async_driver.calls)


@pytest.mark.asyncio
async def test_list_entities_empty_prefix_passes_empty_string(fake_async_driver):
    store = GraphStore(fake_async_driver)  # type: ignore[arg-type]
    await store.list_entities(project_id=uuid4(), prefix="", limit=10)
    assert any(c.kwargs.get("prefix") == "" for c in fake_async_driver.calls)


@pytest.mark.asyncio
async def test_list_entities_returns_list_shape(fake_async_driver):
    store = GraphStore(fake_async_driver)  # type: ignore[arg-type]
    rows = await store.list_entities(project_id=uuid4(), prefix="x", limit=10)
    assert isinstance(rows, list)
