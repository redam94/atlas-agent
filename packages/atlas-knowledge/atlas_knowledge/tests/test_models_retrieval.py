"""Tests for retrieval/ingestion/embedding model shapes."""
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError

from atlas_knowledge.models.embeddings import EmbeddingRequest, EmbeddingResult
from atlas_knowledge.models.ingestion import (
    IngestionJob,
    IngestionStatus,
    IngestRequest,
    SourceType,
)
from atlas_knowledge.models.nodes import KnowledgeNode, KnowledgeNodeType
from atlas_knowledge.models.retrieval import (
    RagContext,
    RetrievalQuery,
    RetrievalResult,
    ScoredChunk,
)


def test_embedding_request_basic():
    req = EmbeddingRequest(texts=["a", "b", "c"])
    assert req.texts == ["a", "b", "c"]


def test_embedding_request_rejects_empty_list():
    with pytest.raises(ValidationError):
        EmbeddingRequest(texts=[])


def test_embedding_result_dimensions_consistent():
    r = EmbeddingResult(vectors=[[0.1, 0.2], [0.3, 0.4]], model_id="bge-small")
    assert len(r.vectors) == 2
    assert r.model_id == "bge-small"


def test_retrieval_query_defaults():
    q = RetrievalQuery(project_id=uuid4(), text="what is X?")
    assert q.top_k == 8


def test_retrieval_query_top_k_bounds():
    pid = uuid4()
    with pytest.raises(ValidationError):
        RetrievalQuery(project_id=pid, text="x", top_k=0)
    with pytest.raises(ValidationError):
        RetrievalQuery(project_id=pid, text="x", top_k=33)


def test_scored_chunk_construction():
    chunk = KnowledgeNode(
        id=uuid4(),
        user_id="matt",
        project_id=uuid4(),
        type=KnowledgeNodeType.CHUNK,
        parent_id=uuid4(),
        text="some chunk",
        created_at=datetime.now(UTC),
    )
    sc = ScoredChunk(chunk=chunk, score=0.87, parent_title="Doc")
    assert sc.score == pytest.approx(0.87)


def test_retrieval_result_round_trip():
    chunk = KnowledgeNode(
        id=uuid4(),
        user_id="matt",
        project_id=uuid4(),
        type=KnowledgeNodeType.CHUNK,
        parent_id=uuid4(),
        text="x",
        created_at=datetime.now(UTC),
    )
    res = RetrievalResult(query="q", chunks=[ScoredChunk(chunk=chunk, score=0.5)])
    assert len(res.chunks) == 1


def test_rag_context_assemble():
    ctx = RagContext(rendered="...prompt context...", citations=[{"title": "Doc", "score": 0.5}])
    assert "prompt context" in ctx.rendered


def test_ingestion_status_values():
    assert IngestionStatus.PENDING == "pending"
    assert IngestionStatus.RUNNING == "running"
    assert IngestionStatus.COMPLETED == "completed"
    assert IngestionStatus.FAILED == "failed"


def test_source_type_values():
    assert SourceType.MARKDOWN == "markdown"
    assert SourceType.PDF == "pdf"


def test_ingest_request_markdown_minimal():
    r = IngestRequest.model_validate({"project_id": str(uuid4()), "source_type": "markdown", "text": "# hello"})
    assert r.source_type is SourceType.MARKDOWN


def test_ingest_request_requires_payload():
    pid = str(uuid4())
    # Markdown without text or filename → invalid
    with pytest.raises(ValidationError):
        IngestRequest.model_validate({"project_id": pid, "source_type": "markdown"})


def test_ingestion_job_construction():
    job = IngestionJob(
        id=uuid4(),
        user_id="matt",
        project_id=uuid4(),
        source_type=SourceType.MARKDOWN,
        source_filename="notes.md",
        status=IngestionStatus.PENDING,
        node_ids=[],
        created_at=datetime.now(UTC),
    )
    assert job.completed_at is None
