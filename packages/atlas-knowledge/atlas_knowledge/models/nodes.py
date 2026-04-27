"""Pydantic models for knowledge nodes (documents and chunks)."""

from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from atlas_core.models.base import AtlasModel
from pydantic import Field


class KnowledgeNodeType(StrEnum):
    DOCUMENT = "document"
    CHUNK = "chunk"


class KnowledgeNode(AtlasModel):
    """A node in the knowledge graph — either a parsed document or one of its chunks."""

    id: UUID
    user_id: str
    project_id: UUID
    type: KnowledgeNodeType
    parent_id: UUID | None = None  # set on chunks; references the document
    title: str | None = None  # populated on documents (filename / heading)
    text: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    embedding_id: str | None = None  # vector store ID for chunks; None for documents
    created_at: datetime
