"""Pure data types shared between ingestion clients and the graph writer.

ChunkSpec is the structural shape that crosses the package boundary; clients
(atlas-knowledge.IngestionService) don't import it, they build duck-typed
adapters with the same interface. See the GraphWriter Protocol in
atlas_knowledge.ingestion.protocols.
"""
from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID


@dataclass(frozen=True)
class ChunkSpec:
    """Minimal chunk shape needed by GraphStore.write_document_chunks."""

    id: UUID
    position: int
    token_count: int
    text_preview: str

    def to_param(self) -> dict[str, object]:
        """Serialize for use as a Cypher parameter."""
        return {
            "id": str(self.id),
            "position": self.position,
            "token_count": self.token_count,
            "text_preview": self.text_preview,
        }
