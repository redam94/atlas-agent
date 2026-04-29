"""Real-Neo4j fixtures for integration tests.

Skipped unless ATLAS_RUN_NEO4J_INTEGRATION=1 (the actual URI + password
come from the env defaults set in the root conftest.py).
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from unittest.mock import AsyncMock
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


@dataclass
class _Call:
    query: str
    kwargs: dict


class _FakeAsyncDriver:
    """Records every (cypher, kwargs) tuple passed through tx.run inside execute_write.

    The store calls ``async with self._driver.session() as s: await s.execute_write(fn)``;
    this fake's session.execute_write runs the closure against a fake transaction
    whose ``run(cypher, **kwargs)`` records into ``self.calls``.
    """

    def __init__(self) -> None:
        self.calls: list[_Call] = []
        self._tx_run_failer = None  # optional override: callable(query) -> raise

    def make_tx_raise_on(self, predicate):
        """Tell the driver to raise on tx.run when predicate(query) is truthy."""
        self._tx_run_failer = predicate

    @property
    def session(self):
        return self._make_session

    def _make_session(self):
        return _FakeSession(self)

    async def close(self):
        pass


class _FakeSession:
    def __init__(self, driver: _FakeAsyncDriver) -> None:
        self._driver = driver

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None

    async def execute_write(self, fn):
        tx = AsyncMock()

        async def _run(query, **kwargs):
            if self._driver._tx_run_failer is not None and self._driver._tx_run_failer(query):
                raise RuntimeError("forced failure")
            self._driver.calls.append(_Call(query=query, kwargs=kwargs))

        tx.run = _run
        return await fn(tx)

    async def execute_read(self, fn):
        tx = AsyncMock()

        async def _run(query, **kwargs):
            self._driver.calls.append(_Call(query=query, kwargs=kwargs))
            # Return an empty async iterator wrapped as result; tests assert on calls only.
            class _R:
                async def __aiter__(self):
                    if False:
                        yield  # pragma: no cover

                async def data(self):
                    return []

            return _R()

        tx.run = _run
        return await fn(tx)

    async def run(self, query, **kwargs):
        self._driver.calls.append(_Call(query=query, kwargs=kwargs))


@pytest.fixture
def fake_async_driver():
    return _FakeAsyncDriver()
