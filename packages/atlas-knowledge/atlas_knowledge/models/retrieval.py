"""Retrieval query/result shapes."""

from typing import Any
from uuid import UUID

from atlas_core.models.base import AtlasModel
from pydantic import Field

from atlas_knowledge.models.nodes import KnowledgeNode


class RetrievalQuery(AtlasModel):
    """One RAG query — embed → vector search → ScoredChunk[]."""

    project_id: UUID
    text: str = Field(min_length=1)
    top_k: int = Field(default=8, ge=1, le=32)
    filter: dict[str, Any] | None = None  # extra metadata filter passed to the store


class ScoredChunk(AtlasModel):
    """A single chunk + similarity score + denormalized parent title."""

    chunk: KnowledgeNode
    score: float
    parent_title: str | None = None


class RetrievalResult(AtlasModel):
    """Bundle returned by Retriever.retrieve()."""

    query: str
    chunks: list[ScoredChunk]
    degraded_stages: list[str] = Field(default_factory=list)


class RagContext(AtlasModel):
    """Renderable bundle injected into the system prompt (Plan 5 wires this in)."""

    rendered: str  # the prompt-ready text block
    citations: list[dict[str, Any]]  # parallel list of metadata for the UI
