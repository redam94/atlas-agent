"""Lifespan-time integration test: real Neo4j brings migrations + GraphStore up.

Skipped unless ATLAS_RUN_NEO4J_INTEGRATION is set (and a Neo4j is reachable at
ATLAS_GRAPH__URI — defaults to bolt://localhost:7687 from conftest).
"""
from __future__ import annotations

import os

import pytest

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("ATLAS_RUN_NEO4J_INTEGRATION") != "1",
        reason="set ATLAS_RUN_NEO4J_INTEGRATION=1 to enable",
    ),
]


@pytest.mark.asyncio
async def test_lifespan_initializes_graph_store():
    from atlas_api.main import app, lifespan

    async with lifespan(app):
        assert hasattr(app.state, "graph_store")
        await app.state.graph_store.healthcheck()
