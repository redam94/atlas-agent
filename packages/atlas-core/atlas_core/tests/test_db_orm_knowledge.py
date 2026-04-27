"""Smoke tests for KnowledgeNodeORM + IngestionJobORM round-trips."""
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import select

from atlas_core.db.orm import (
    IngestionJobORM,
    KnowledgeNodeORM,
    ProjectORM,
)


@pytest.mark.asyncio
async def test_knowledge_node_round_trip(db_session):
    project = ProjectORM(
        user_id="matt",
        name="P",
        default_model="claude-sonnet-4-6",
    )
    db_session.add(project)
    await db_session.flush()

    doc = KnowledgeNodeORM(
        user_id="matt",
        project_id=project.id,
        type="document",
        title="Doc One",
        text="full document text",
        metadata_={"source": "test"},
    )
    db_session.add(doc)
    await db_session.flush()

    chunk = KnowledgeNodeORM(
        user_id="matt",
        project_id=project.id,
        type="chunk",
        parent_id=doc.id,
        text="a chunk of the document",
        metadata_={"index": 0},
        embedding_id=str(doc.id),
    )
    db_session.add(chunk)
    await db_session.flush()

    rows = (await db_session.execute(select(KnowledgeNodeORM))).scalars().all()
    assert len(rows) == 2
    chunks = [r for r in rows if r.type == "chunk"]
    assert chunks[0].parent_id == doc.id
    assert chunks[0].metadata_ == {"index": 0}


@pytest.mark.asyncio
async def test_ingestion_job_round_trip(db_session):
    project = ProjectORM(user_id="matt", name="P", default_model="claude-sonnet-4-6")
    db_session.add(project)
    await db_session.flush()

    job = IngestionJobORM(
        user_id="matt",
        project_id=project.id,
        source_type="markdown",
        source_filename="notes.md",
        status="pending",
    )
    db_session.add(job)
    await db_session.flush()

    job.status = "completed"
    job.completed_at = datetime.now(UTC)
    job.node_ids = [str(uuid4()), str(uuid4())]
    await db_session.flush()

    fetched = (await db_session.execute(select(IngestionJobORM))).scalar_one()
    assert fetched.status == "completed"
    assert len(fetched.node_ids) == 2
