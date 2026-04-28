"""Real-Neo4j fixtures for integration tests.

Skipped unless ATLAS_RUN_NEO4J_INTEGRATION=1 (the actual URI + password
come from the env defaults set in the root conftest.py).
"""
from __future__ import annotations

import os
from uuid import uuid4

import pytest
import pytest_asyncio
from neo4j import AsyncGraphDatabase

from atlas_graph.store import GraphStore


def _enabled() -> bool:
    return os.getenv("ATLAS_RUN_NEO4J_INTEGRATION") == "1"


@pytest_asyncio.fixture
async def real_neo4j_driver():
    if not _enabled():
        pytest.skip("set ATLAS_RUN_NEO4J_INTEGRATION=1 to enable")
    driver = AsyncGraphDatabase.driver(
        os.environ["ATLAS_GRAPH__URI"],
        auth=("neo4j", os.environ["ATLAS_GRAPH__PASSWORD"]),
    )
    try:
        yield driver
    finally:
        await driver.close()


@pytest_asyncio.fixture
async def real_graph_store(real_neo4j_driver):
    yield GraphStore(real_neo4j_driver)


@pytest_asyncio.fixture
async def isolated_project_id(real_neo4j_driver):
    """Yield a fresh UUID; teardown deletes every node tagged with it."""
    pid = uuid4()
    yield pid
    async with real_neo4j_driver.session() as s:
        await s.run(
            "MATCH (n) WHERE n.project_id = $pid DETACH DELETE n",
            pid=str(pid),
        )
        await s.run(
            "MATCH (p:Project {id: $pid}) DETACH DELETE p",
            pid=str(pid),
        )
