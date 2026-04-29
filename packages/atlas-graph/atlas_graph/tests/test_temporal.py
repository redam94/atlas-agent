"""GraphStore.build_temporal_near — fake-driver tests."""
from __future__ import annotations

from uuid import uuid4

import pytest

from atlas_graph.ingestion.temporal import TEMPORAL_NEAR_CYPHER
from atlas_graph.store import GraphStore


@pytest.mark.asyncio
async def test_build_temporal_near_runs_temporal_cypher(fake_async_driver):
    pid, did = uuid4(), uuid4()
    store = GraphStore(fake_async_driver)
    await store.build_temporal_near(project_id=pid, document_id=did, window_days=7)

    call = next(c for c in fake_async_driver.calls if "TEMPORAL_NEAR" in c.query)
    assert call.query == TEMPORAL_NEAR_CYPHER
    assert call.kwargs["document_id"] == str(did)
    assert call.kwargs["project_id"] == str(pid)
    assert call.kwargs["window_days"] == 7
