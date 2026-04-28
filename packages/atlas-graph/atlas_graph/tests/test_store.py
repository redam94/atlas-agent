"""Unit tests for GraphStore — mocked driver, no Neo4j."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from neo4j.exceptions import ServiceUnavailable, TransientError

from atlas_graph.errors import GraphUnavailableError
from atlas_graph.store import GraphStore


def _mock_driver_session_succeeds():
    """Driver mock whose session.run returns OK on first call."""
    session = AsyncMock()
    session.__aenter__.return_value = session
    session.__aexit__.return_value = None
    session.run = AsyncMock()
    session.execute_write = AsyncMock()
    driver = MagicMock()
    driver.session = MagicMock(return_value=session)
    driver.close = AsyncMock()
    return driver, session


@pytest.mark.asyncio
async def test_healthcheck_runs_return_one():
    driver, session = _mock_driver_session_succeeds()
    store = GraphStore(driver)
    await store.healthcheck()
    session.run.assert_awaited_once()
    args, _ = session.run.call_args
    assert args[0].strip() == "RETURN 1"


@pytest.mark.asyncio
async def test_close_closes_driver():
    driver, _ = _mock_driver_session_succeeds()
    store = GraphStore(driver)
    await store.close()
    driver.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_with_retry_succeeds_first_try():
    driver, session = _mock_driver_session_succeeds()
    store = GraphStore(driver, max_retries=3)
    fn = AsyncMock()
    await store._with_retry(fn)  # type: ignore[attr-defined]
    session.execute_write.assert_awaited_once_with(fn)


@pytest.mark.asyncio
async def test_with_retry_retries_then_succeeds(monkeypatch):
    driver, session = _mock_driver_session_succeeds()
    store = GraphStore(driver, max_retries=3)
    # First two attempts raise ServiceUnavailable, third succeeds.
    session.execute_write.side_effect = [
        ServiceUnavailable("attempt 1"),
        TransientError("attempt 2"),
        None,
    ]
    # Make sleep a no-op so the test is fast.
    monkeypatch.setattr("atlas_graph.store.asyncio.sleep", AsyncMock())

    fn = AsyncMock()
    await store._with_retry(fn)  # type: ignore[attr-defined]
    assert session.execute_write.await_count == 3


@pytest.mark.asyncio
async def test_with_retry_raises_graph_unavailable_after_exhausting(monkeypatch):
    driver, session = _mock_driver_session_succeeds()
    store = GraphStore(driver, max_retries=3)
    session.execute_write.side_effect = ServiceUnavailable("nope")
    monkeypatch.setattr("atlas_graph.store.asyncio.sleep", AsyncMock())

    fn = AsyncMock()
    with pytest.raises(GraphUnavailableError) as excinfo:
        await store._with_retry(fn)  # type: ignore[attr-defined]
    assert "neo4j unavailable" in str(excinfo.value).lower()
    assert isinstance(excinfo.value.__cause__, ServiceUnavailable)
    assert session.execute_write.await_count == 3
