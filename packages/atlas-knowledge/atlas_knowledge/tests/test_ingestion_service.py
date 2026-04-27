"""Integration test for IngestionService — uses FakeEmbedder + tmp Chroma."""
from uuid import uuid4

import pytest
from sqlalchemy import select

from atlas_core.db.orm import IngestionJobORM, KnowledgeNodeORM, ProjectORM
from atlas_knowledge.embeddings import FakeEmbedder
from atlas_knowledge.ingestion.service import IngestionService
from atlas_knowledge.parsers.markdown import parse_markdown
from atlas_knowledge.vector.chroma import ChromaVectorStore


@pytest.fixture
async def project_id(db_session):
    p = ProjectORM(user_id="matt", name="P", default_model="claude-sonnet-4-6")
    db_session.add(p)
    await db_session.flush()
    return p.id


@pytest.fixture
def vector_store(tmp_path):
    return ChromaVectorStore(persist_dir=str(tmp_path), user_id="matt")


@pytest.fixture
def service(vector_store):
    return IngestionService(
        embedder=FakeEmbedder(dim=16),
        vector_store=vector_store,
    )


@pytest.mark.asyncio
async def test_ingest_markdown_creates_document_chunks_and_completes_job(
    service, project_id, db_session
):
    parsed = parse_markdown("# Hello\n\n" + ("body word " * 600), title="Hello")
    job_id = await service.ingest(
        db=db_session,
        user_id="matt",
        project_id=project_id,
        parsed=parsed,
        source_type="markdown",
        source_filename="hello.md",
    )

    job = (await db_session.execute(select(IngestionJobORM))).scalar_one()
    assert job.id == job_id
    assert job.status == "completed"
    assert job.completed_at is not None
    assert len(job.node_ids) >= 2  # at least one document + one chunk

    nodes = (await db_session.execute(select(KnowledgeNodeORM))).scalars().all()
    docs = [n for n in nodes if n.type == "document"]
    chunks = [n for n in nodes if n.type == "chunk"]
    assert len(docs) == 1
    assert len(chunks) >= 1
    assert all(c.parent_id == docs[0].id for c in chunks)
    assert docs[0].title == "Hello"


@pytest.mark.asyncio
async def test_ingest_failure_marks_job_failed(service, project_id, db_session):
    """If embedding raises, the job row should be marked failed with error text."""

    class _BoomEmbedder(FakeEmbedder):
        async def embed_documents(self, texts):
            raise RuntimeError("boom")

    bad_service = IngestionService(
        embedder=_BoomEmbedder(),
        vector_store=service._vector_store,  # noqa: SLF001
    )
    parsed = parse_markdown("# X\n\nbody.")
    job_id = await bad_service.ingest(
        db=db_session,
        user_id="matt",
        project_id=project_id,
        parsed=parsed,
        source_type="markdown",
        source_filename=None,
    )
    job = (await db_session.execute(select(IngestionJobORM))).scalar_one()
    assert job.id == job_id
    assert job.status == "failed"
    assert "boom" in (job.error or "")
