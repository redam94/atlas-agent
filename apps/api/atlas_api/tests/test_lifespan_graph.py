"""Lifespan-time integration test: real Neo4j brings migrations + GraphStore up.

Skipped unless ATLAS_TEST_NEO4J_URL is set (e.g.
ATLAS_TEST_NEO4J_URL=bolt://localhost:7687) AND ATLAS_GRAPH__PASSWORD is set.
"""
from __future__ import annotations

import os

import pytest

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not os.getenv("ATLAS_TEST_NEO4J_URL") or not os.getenv("ATLAS_GRAPH__PASSWORD"),
        reason="set ATLAS_TEST_NEO4J_URL and ATLAS_GRAPH__PASSWORD to enable",
    ),
]


@pytest.mark.asyncio
async def test_lifespan_initializes_graph_store(monkeypatch):
    monkeypatch.setenv("ATLAS_GRAPH__URI", os.environ["ATLAS_TEST_NEO4J_URL"])
    monkeypatch.setenv("ATLAS_GRAPH__BACKFILL_ON_START", "false")
    # Re-import main to pick up env-driven config in a fresh process state.
    from atlas_api.main import app, lifespan

    async with lifespan(app):
        assert hasattr(app.state, "graph_store")
        # Healthcheck round-trips.
        await app.state.graph_store.healthcheck()
