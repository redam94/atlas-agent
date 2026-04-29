"""Structural types that decouple IngestionService from any specific graph backend.

GraphWriter is a Protocol satisfied by atlas_graph.store.GraphStore (Plan 2)
and any future graph backend. atlas-knowledge does NOT import atlas-graph;
the type relationship is structural, not nominal.
"""
from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Protocol
from uuid import UUID


class ChunkSpecLike(Protocol):
    """Minimal duck-type a chunk passed to write_document_chunks must satisfy."""

    id: UUID
    position: int
    token_count: int
    text_preview: str

    def to_param(self) -> dict[str, object]: ...


class ChunkWithTextLike(Protocol):
    """Minimal duck-type a chunk passed to write_entities must satisfy."""

    id: UUID
    text: str


class GraphWriter(Protocol):
    """Side-effect interface for writing document/chunk nodes to a graph store."""

    async def write_document_chunks(
        self,
        *,
        project_id: UUID,
        project_name: str,
        document_id: UUID,
        document_title: str,
        document_source_type: str,
        document_metadata: dict,
        document_created_at: datetime,
        chunks: Sequence[ChunkSpecLike],
    ) -> None: ...

    async def write_entities(
        self,
        *,
        project_id: UUID,
        chunks: Sequence[ChunkWithTextLike],
    ) -> None: ...

    async def merge_semantic_near(
        self,
        *,
        pairs: Sequence[tuple[UUID, UUID, float]],
    ) -> None: ...

    async def build_temporal_near(
        self,
        *,
        project_id: UUID,
        document_id: UUID,
        window_days: int,
    ) -> None: ...

    async def run_pagerank(self, *, project_id: UUID) -> None: ...
