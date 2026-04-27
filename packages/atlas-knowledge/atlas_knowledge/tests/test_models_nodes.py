"""Tests for atlas_knowledge.models.nodes."""
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError

from atlas_knowledge.models.nodes import KnowledgeNode, KnowledgeNodeType


def test_node_type_values():
    assert KnowledgeNodeType.DOCUMENT == "document"
    assert KnowledgeNodeType.CHUNK == "chunk"


def test_document_node_construction():
    n = KnowledgeNode(
        id=uuid4(),
        user_id="matt",
        project_id=uuid4(),
        type=KnowledgeNodeType.DOCUMENT,
        title="Notes",
        text="hello",
        metadata={"source": "test"},
        created_at=datetime.now(UTC),
    )
    assert n.parent_id is None
    assert n.embedding_id is None


def test_chunk_node_requires_parent_in_practice():
    """Schema does not enforce parent_id, but chunks should have one in practice."""
    chunk = KnowledgeNode(
        id=uuid4(),
        user_id="matt",
        project_id=uuid4(),
        type=KnowledgeNodeType.CHUNK,
        parent_id=uuid4(),
        text="chunk text",
        metadata={"index": 0},
        embedding_id="emb-1",
        created_at=datetime.now(UTC),
    )
    assert chunk.type is KnowledgeNodeType.CHUNK
    assert chunk.parent_id is not None


def test_node_text_required():
    with pytest.raises(ValidationError):
        KnowledgeNode(
            id=uuid4(),
            user_id="matt",
            project_id=uuid4(),
            type=KnowledgeNodeType.DOCUMENT,
            created_at=datetime.now(UTC),
        )  # missing text


def test_node_metadata_defaults_empty_dict():
    n = KnowledgeNode(
        id=uuid4(),
        user_id="matt",
        project_id=uuid4(),
        type=KnowledgeNodeType.DOCUMENT,
        text="x",
        created_at=datetime.now(UTC),
    )
    assert n.metadata == {}
