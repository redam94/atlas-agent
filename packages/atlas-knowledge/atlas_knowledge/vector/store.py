"""VectorStore ABC."""

from abc import ABC, abstractmethod
from typing import Any
from uuid import UUID

from atlas_knowledge.models.nodes import KnowledgeNode
from atlas_knowledge.models.retrieval import ScoredChunk


class VectorStore(ABC):
    """Async vector store interface — chunk-only.

    Documents are persisted to ``KnowledgeNodeORM`` (Postgres) but never
    enter the vector store. Only chunks (which carry semantic content of
    a fixed size) are embedded and indexed here.
    """

    @abstractmethod
    async def upsert(
        self,
        chunks: list[KnowledgeNode],
        embeddings: list[list[float]],
    ) -> None:
        """Insert or update chunks. ``embeddings[i]`` is the vector for ``chunks[i]``."""

    @abstractmethod
    async def search(
        self,
        query_embedding: list[float],
        top_k: int = 8,
        filter: dict[str, Any] | None = None,
    ) -> list[ScoredChunk]:
        """Return the top-K most similar chunks. ``ScoredChunk.chunk`` is
        hydrated from the vector store's metadata (no DB join required for
        Phase 1 — Plan 5 will hydrate from Postgres for richer fields)."""

    @abstractmethod
    async def delete(self, ids: list[UUID]) -> None:
        """Remove chunks by ID."""

    @abstractmethod
    def delete_by_parent(self, *, project_id: UUID, parent_id: UUID) -> None:
        """Delete all chunk vectors whose metadata.parent_id matches."""
