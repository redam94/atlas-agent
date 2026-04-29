"""Unit tests for GraphStore — mocked driver, no Neo4j."""
from __future__ import annotations

import json
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from neo4j.exceptions import ServiceUnavailable, TransientError

from atlas_graph.errors import GraphUnavailableError
from atlas_graph.protocols import ChunkSpec
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


@pytest.mark.asyncio
async def test_write_document_chunks_runs_5_cypher_statements_in_one_tx():
    """Captures the (cypher, params) sequence executed inside the write tx."""
    driver = MagicMock()
    session = AsyncMock()
    session.__aenter__.return_value = session
    session.__aexit__.return_value = None
    driver.session = MagicMock(return_value=session)

    captured: list[tuple[str, dict]] = []

    async def fake_execute_write(fn):
        # The fn the store passes to execute_write expects an AsyncTransaction.
        tx = AsyncMock()
        async def fake_run(cypher, **params):
            captured.append((cypher, params))
        tx.run = fake_run
        await fn(tx)

    session.execute_write = fake_execute_write

    store = GraphStore(driver)
    pid = uuid4()
    did = uuid4()
    chunks = [
        ChunkSpec(id=uuid4(), position=0, token_count=128, text_preview="alpha"),
        ChunkSpec(id=uuid4(), position=1, token_count=64, text_preview="beta"),
    ]
    await store.write_document_chunks(
        project_id=pid,
        project_name="P",
        document_id=did,
        document_title="Doc One",
        document_source_type="markdown",
        document_metadata={"author": "matt"},
        document_created_at=datetime.now(UTC),
        chunks=chunks,
    )

    assert len(captured) == 5
    cyphers = [c for c, _ in captured]
    assert "MERGE (p:Project" in cyphers[0]
    assert "MERGE (d:Document" in cyphers[1]
    assert "(d)-[:PART_OF]->(p)" in cyphers[2]
    assert "MERGE (ch:Chunk" in cyphers[3]
    assert "(c)-[:BELONGS_TO]->(d)" in cyphers[4]


@pytest.mark.asyncio
async def test_write_document_chunks_passes_str_uuids_for_ids():
    driver = MagicMock()
    session = AsyncMock()
    session.__aenter__.return_value = session
    session.__aexit__.return_value = None
    driver.session = MagicMock(return_value=session)

    captured: list[tuple[str, dict]] = []

    async def fake_execute_write(fn):
        tx = AsyncMock()
        async def fake_run(cypher, **params):
            captured.append((cypher, params))
        tx.run = fake_run
        await fn(tx)

    session.execute_write = fake_execute_write

    store = GraphStore(driver)
    pid = uuid4()
    did = uuid4()
    cid = uuid4()
    await store.write_document_chunks(
        project_id=pid,
        project_name="P",
        document_id=did,
        document_title="t",
        document_source_type="markdown",
        document_metadata={},
        document_created_at=datetime.now(UTC),
        chunks=[ChunkSpec(id=cid, position=0, token_count=1, text_preview="x")],
    )
    # All id parameters are stringified UUIDs (Neo4j stores them as strings).
    project_call = captured[0][1]
    document_call = captured[1][1]
    chunk_unwind_call = captured[3][1]
    assert project_call["project_id"] == str(pid)
    assert document_call["id"] == str(did)
    assert chunk_unwind_call["chunks"][0]["id"] == str(cid)
    assert chunk_unwind_call["project_id"] == str(pid)
    assert chunk_unwind_call["document_id"] == str(did)


@pytest.mark.asyncio
async def test_write_document_chunks_serializes_metadata_as_json_string():
    """Neo4j 5 properties don't accept maps. Metadata becomes a single JSON string."""
    driver = MagicMock()
    session = AsyncMock()
    session.__aenter__.return_value = session
    session.__aexit__.return_value = None
    driver.session = MagicMock(return_value=session)

    captured: list[tuple[str, dict]] = []

    async def fake_execute_write(fn):
        tx = AsyncMock()
        async def fake_run(cypher, **params):
            captured.append((cypher, params))
        tx.run = fake_run
        await fn(tx)

    session.execute_write = fake_execute_write

    store = GraphStore(driver)
    metadata = {
        "scalar": "ok",
        "nested": {"a": 1, "b": [1, 2]},
        "list_of_dicts": [{"k": "v"}, {"k": "v2"}],
    }
    await store.write_document_chunks(
        project_id=uuid4(),
        project_name="P",
        document_id=uuid4(),
        document_title="t",
        document_source_type="markdown",
        document_metadata=metadata,
        document_created_at=datetime.now(UTC),
        chunks=[],
    )
    document_call_meta = captured[1][1]["metadata"]
    # The whole metadata dict round-trips as a single JSON string.
    assert isinstance(document_call_meta, str)
    decoded = json.loads(document_call_meta)
    assert decoded == metadata


@pytest.mark.asyncio
async def test_write_document_chunks_sets_created_at_on_document():
    """Document.created_at is set from the document_created_at parameter (ISO 8601 string)."""
    driver = MagicMock()
    session = AsyncMock()
    session.__aenter__.return_value = session
    session.__aexit__.return_value = None
    driver.session = MagicMock(return_value=session)

    captured: list[tuple[str, dict]] = []

    async def fake_execute_write(fn):
        tx = AsyncMock()
        async def fake_run(cypher, **params):
            captured.append((cypher, params))
        tx.run = fake_run
        await fn(tx)

    session.execute_write = fake_execute_write

    store = GraphStore(driver)
    ts = datetime(2026, 4, 28, 12, 0, 0, tzinfo=UTC)

    await store.write_document_chunks(
        project_id=uuid4(),
        project_name="P",
        document_id=uuid4(),
        document_title="t",
        document_source_type="markdown",
        document_metadata={},
        document_created_at=ts,
        chunks=[],
    )

    # Find the (single) Document MERGE call and verify created_at parameter.
    doc_calls = [(c, p) for (c, p) in captured if "MERGE (d:Document" in c]
    assert len(doc_calls) == 1
    cypher, params = doc_calls[0]
    assert "d.created_at" in cypher
    assert params["created_at"] == ts.isoformat()
