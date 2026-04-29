"""Backfill tests — real Postgres + mocked GraphStore."""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from atlas_core.db.orm import KnowledgeNodeORM, ProjectORM
from sqlalchemy.ext.asyncio import AsyncSession

from atlas_graph.backfill import BackfillResult, backfill_phase1
from atlas_graph.store import GraphStore


async def _seed_project_with_chunks(db: AsyncSession, *, n_chunks: int = 3):
    proj = ProjectORM(user_id="matt", name="P", default_model="claude-sonnet-4-6")
    db.add(proj)
    await db.flush()
    doc = KnowledgeNodeORM(
        user_id="matt",
        project_id=proj.id,
        type="document",
        title="Doc",
        text="full document text",
        metadata_={"source_type": "markdown"},
    )
    db.add(doc)
    await db.flush()
    chunks = []
    for i in range(n_chunks):
        ch = KnowledgeNodeORM(
            user_id="matt",
            project_id=proj.id,
            type="chunk",
            parent_id=doc.id,
            title="Doc",
            text=f"chunk text {i} " * 30,
            metadata_={"index": i, "token_count": 64},
        )
        db.add(ch)
        chunks.append(ch)
    await db.flush()
    return proj, doc, chunks


@pytest.mark.asyncio
async def test_backfill_writes_one_call_per_document(db_session: AsyncSession):
    graph = AsyncMock(spec=GraphStore)
    proj, doc, chunks = await _seed_project_with_chunks(db_session, n_chunks=3)

    result = await backfill_phase1(db=db_session, graph=graph)

    assert isinstance(result, BackfillResult)
    assert result.documents == 1
    assert result.chunks == 3
    assert result.batches >= 1
    graph.write_document_chunks.assert_awaited_once()
    kwargs = graph.write_document_chunks.await_args.kwargs
    assert kwargs["project_id"] == proj.id
    assert kwargs["project_name"] == "P"
    assert kwargs["document_id"] == doc.id
    assert kwargs["document_title"] == "Doc"
    assert kwargs["document_source_type"] == "markdown"
    assert len(kwargs["chunks"]) == 3
    # Chunks have id, position, token_count, text_preview.
    assert {c.position for c in kwargs["chunks"]} == {0, 1, 2}
    # Drift protection: verify document_created_at was passed.
    assert "document_created_at" in kwargs


@pytest.mark.asyncio
async def test_backfill_text_preview_is_first_200_chars(db_session: AsyncSession):
    graph = AsyncMock(spec=GraphStore)
    proj, doc, chunks = await _seed_project_with_chunks(db_session, n_chunks=1)
    # The seeded chunk text is "chunk text 0 " * 30 = 390 chars.
    await backfill_phase1(db=db_session, graph=graph)
    kwargs = graph.write_document_chunks.await_args.kwargs
    preview = kwargs["chunks"][0].text_preview
    assert len(preview) == 200
    assert preview.startswith("chunk text 0")


@pytest.mark.asyncio
async def test_backfill_empty_db_returns_zero_result(db_session: AsyncSession):
    graph = AsyncMock(spec=GraphStore)
    result = await backfill_phase1(db=db_session, graph=graph)
    assert result.documents == 0
    assert result.chunks == 0
    assert result.batches == 0
    graph.write_document_chunks.assert_not_awaited()


@pytest.mark.asyncio
async def test_backfill_progress_callback_invoked_per_batch(db_session: AsyncSession):
    """With docs_per_batch=1, three docs should fire three progress calls."""
    graph = AsyncMock(spec=GraphStore)
    proj = ProjectORM(user_id="matt", name="P", default_model="claude-sonnet-4-6")
    db_session.add(proj)
    await db_session.flush()
    for _ in range(3):
        doc = KnowledgeNodeORM(
            user_id="matt", project_id=proj.id, type="document",
            title="d", text="t", metadata_={"source_type": "markdown"},
        )
        db_session.add(doc)
    await db_session.flush()

    progress: list[tuple[int, int]] = []
    await backfill_phase1(
        db=db_session, graph=graph, docs_per_batch=1,
        progress_cb=lambda b, t: progress.append((b, t)),
    )
    assert len(progress) == 3
    assert [b for b, _ in progress] == [1, 2, 3]
    assert all(t == 3 for _, t in progress)
