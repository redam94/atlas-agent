"""Cypher-shape tests for GraphStore.tag_note."""
from __future__ import annotations

from uuid import uuid4

import pytest

from atlas_graph.store import GraphStore


@pytest.mark.asyncio
async def test_tag_note_runs_one_write_with_note_id_and_entity_ids(fake_async_driver):
    store = GraphStore(fake_async_driver)  # type: ignore[arg-type]
    note_id = uuid4()
    entity_ids = [uuid4(), uuid4()]

    await store.tag_note(note_id=note_id, entity_ids=entity_ids)

    assert any(c.kwargs.get("note_id") == str(note_id) for c in fake_async_driver.calls)
    assert any(
        c.kwargs.get("entity_ids") == [str(e) for e in entity_ids]
        for c in fake_async_driver.calls
    )
    assert any("TAGGED_WITH" in c.query for c in fake_async_driver.calls)


@pytest.mark.asyncio
async def test_tag_note_empty_entity_ids_short_circuits(fake_async_driver):
    store = GraphStore(fake_async_driver)  # type: ignore[arg-type]
    await store.tag_note(note_id=uuid4(), entity_ids=[])
    assert fake_async_driver.calls == []
