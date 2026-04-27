"""EmbeddingService ABC.

Concrete implementations live in providers/. Phase 1 ships:
- ``SentenceTransformersEmbedder`` (BGE-small, in-process)
- ``FakeEmbedder`` (tests)
"""

from abc import ABC, abstractmethod


class EmbeddingService(ABC):
    """Async embedding interface."""

    model_id: str  # set by subclass __init__

    @abstractmethod
    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """Batch-embed input texts. Output index matches input index."""

    @abstractmethod
    async def embed_query(self, text: str) -> list[float]:
        """Embed a single query string. May share the model with embed_documents
        but some providers prepend a query-specific prefix (BGE: "Represent this
        sentence for searching: "). Implementations document their prefix."""
