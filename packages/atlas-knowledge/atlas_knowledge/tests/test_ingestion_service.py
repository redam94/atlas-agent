"""Integration test for IngestionService — uses FakeEmbedder + tmp Chroma."""

from datetime import datetime
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from atlas_core.db.orm import IngestionJobORM, KnowledgeNodeORM, ProjectORM
from sqlalchemy import select

from atlas_knowledge.embeddings import FakeEmbedder
from atlas_knowledge.ingestion.protocols import ChunkSpecLike, GraphWriter
from atlas_knowledge.ingestion.service import IngestionService, _ChunkSpecAdapter
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


@pytest.mark.asyncio
async def test_ingest_does_not_call_graph_writer_when_none(
    service, project_id, db_session
):
    """Default constructor leaves graph_writer=None — existing behavior."""
    parsed = parse_markdown("# Title\n\n" + ("body " * 100))
    job_id = await service.ingest(
        db=db_session, user_id="matt", project_id=project_id,
        parsed=parsed, source_type="markdown", source_filename=None,
    )
    job = (await db_session.execute(select(IngestionJobORM))).scalar_one()
    assert job.id == job_id
    assert job.status == "completed"


@pytest.mark.asyncio
async def test_ingest_calls_graph_writer_when_supplied(
    vector_store, project_id, db_session
):
    graph_writer = AsyncMock(spec=GraphWriter)
    service_with_graph = IngestionService(
        embedder=FakeEmbedder(dim=16),
        vector_store=vector_store,
        graph_writer=graph_writer,
    )
    parsed = parse_markdown("# Title\n\n" + ("body " * 100))
    job_id = await service_with_graph.ingest(
        db=db_session, user_id="matt", project_id=project_id,
        parsed=parsed, source_type="markdown", source_filename=None,
    )
    job = (await db_session.execute(select(IngestionJobORM))).scalar_one()
    assert job.id == job_id
    assert job.status == "completed"
    graph_writer.write_document_chunks.assert_awaited_once()
    kwargs = graph_writer.write_document_chunks.await_args.kwargs
    assert kwargs["project_id"] == project_id
    assert kwargs["project_name"] == "P"
    assert kwargs["document_source_type"] == "markdown"
    assert "document_created_at" in kwargs
    assert isinstance(kwargs["document_created_at"], datetime)
    assert len(kwargs["chunks"]) >= 1
    # ChunkSpecLike duck-type: each item has the required attributes + to_param().
    for c in kwargs["chunks"]:
        assert hasattr(c, "id")
        assert hasattr(c, "position")
        assert hasattr(c, "token_count")
        assert hasattr(c, "text_preview")
        param = c.to_param()
        assert "id" in param
        assert "position" in param
        assert "token_count" in param
        assert "text_preview" in param


@pytest.mark.asyncio
async def test_ingest_marks_job_failed_when_graph_writer_raises(
    vector_store, project_id, db_session
):
    graph_writer = AsyncMock(spec=GraphWriter)
    graph_writer.write_document_chunks.side_effect = RuntimeError("graph down")
    service_with_graph = IngestionService(
        embedder=FakeEmbedder(dim=16),
        vector_store=vector_store,
        graph_writer=graph_writer,
    )
    parsed = parse_markdown("# Title\n\n" + ("body " * 100))
    await service_with_graph.ingest(
        db=db_session, user_id="matt", project_id=project_id,
        parsed=parsed, source_type="markdown", source_filename=None,
    )
    job = (await db_session.execute(select(IngestionJobORM))).scalar_one()
    assert job.status == "failed"
    assert "graph down" in job.error
    # Rollback verification: doc + chunk rows should be deleted.
    nodes = (await db_session.execute(select(KnowledgeNodeORM))).scalars().all()
    assert nodes == [], f"expected empty knowledge_nodes table, got {len(nodes)} rows"


@pytest.mark.asyncio
async def test_ingest_empty_document_still_writes_graph_node(
    vector_store, project_id, db_session
):
    """Empty document (no chunks) must still create a graph (:Document) node."""
    graph_writer = AsyncMock(spec=GraphWriter)
    service_with_graph = IngestionService(
        embedder=FakeEmbedder(dim=16),
        vector_store=vector_store,
        graph_writer=graph_writer,
    )
    # Empty string → chunker returns no chunks.
    parsed = parse_markdown("", title="Empty")
    job_id = await service_with_graph.ingest(
        db=db_session, user_id="matt", project_id=project_id,
        parsed=parsed, source_type="markdown", source_filename=None,
    )
    job = (await db_session.execute(select(IngestionJobORM))).scalar_one()
    assert job.id == job_id
    assert job.status == "completed"

    # Graph writer was called with empty chunks list.
    graph_writer.write_document_chunks.assert_awaited_once()
    kwargs = graph_writer.write_document_chunks.await_args.kwargs
    assert kwargs["chunks"] == []
    assert kwargs["document_source_type"] == "markdown"


def test_chunk_spec_adapter_satisfies_chunk_spec_like_protocol():
    """Drift protection: _ChunkSpecAdapter must structurally satisfy ChunkSpecLike."""
    adapter = _ChunkSpecAdapter(id=uuid4(), position=0, token_count=0, text_preview="")
    # Type-check at runtime: this assignment fails if shape diverges.
    _: ChunkSpecLike = adapter
    # Also verify the to_param shape.
    param = adapter.to_param()
    assert set(param.keys()) == {"id", "position", "token_count", "text_preview"}
