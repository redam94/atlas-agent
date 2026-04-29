"""Lifespan-time integration test: real Neo4j brings migrations + GraphStore up.

Skipped unless ATLAS_RUN_NEO4J_INTEGRATION is set (and a Neo4j is reachable at
ATLAS_GRAPH__URI — defaults to bolt://localhost:7687 from conftest).
"""
from __future__ import annotations

import os

import pytest


@pytest.mark.asyncio
@pytest.mark.integration
@pytest.mark.skipif(
    os.getenv("ATLAS_RUN_NEO4J_INTEGRATION") != "1",
    reason="set ATLAS_RUN_NEO4J_INTEGRATION=1 to enable",
)
async def test_lifespan_initializes_graph_store():
    from atlas_api.main import app, lifespan

    async with lifespan(app):
        assert hasattr(app.state, "graph_store")
        await app.state.graph_store.healthcheck()


@pytest.mark.asyncio
async def test_lifespan_wires_ner_extractor_into_graph_store(monkeypatch):
    """Lifespan constructs a NerExtractor and passes it into GraphStore.

    Stubs out Neo4j-touching calls so this runs without a real database.
    """
    from unittest.mock import AsyncMock

    from atlas_api.main import app, lifespan
    from atlas_graph.ingestion.ner import NerExtractor

    fake_driver = AsyncMock()
    fake_driver.close = AsyncMock()
    monkeypatch.setattr(
        "atlas_api.main.AsyncGraphDatabase.driver",
        lambda *a, **kw: fake_driver,
    )
    monkeypatch.setattr(
        "atlas_api.main.MigrationRunner.run_pending",
        AsyncMock(return_value=[]),
    )
    # Disable backfill so it doesn't try to talk to Postgres.
    monkeypatch.setenv("ATLAS_GRAPH__BACKFILL_ON_START", "false")

    async with lifespan(app):
        assert hasattr(app.state, "graph_store")
        assert app.state.graph_store._ner_extractor is not None
        assert isinstance(app.state.graph_store._ner_extractor, NerExtractor)
        # IngestionService received the new Plan 3 thresholds.
        svc = app.state.ingestion_service
        assert svc._semantic_near_threshold == 0.85
        assert svc._semantic_near_top_k == 50
        assert svc._temporal_near_window_days == 7
