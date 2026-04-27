"""FakeEmbedder — deterministic hash-based vectors for tests.

NOT semantic; it only guarantees that identical inputs produce identical
outputs and the dimension is consistent. Useful for unit-testing the
ingestion pipeline + vector store without downloading BGE-small.
"""
import hashlib

from atlas_knowledge.embeddings.service import EmbeddingService


class FakeEmbedder(EmbeddingService):
    """Hash a string → bytes → ``dim`` floats in [-1, 1]."""

    def __init__(self, dim: int = 16) -> None:
        self.dim = dim
        self.model_id = "fake-embedder"

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._hash_to_vector(t) for t in texts]

    async def embed_query(self, text: str) -> list[float]:
        return self._hash_to_vector(text)

    def _hash_to_vector(self, text: str) -> list[float]:
        # SHA-256 → 32 bytes → repeat/truncate to ``dim`` bytes → scale to [-1, 1].
        digest = hashlib.sha256(text.encode("utf-8")).digest()
        vec_bytes = (digest * ((self.dim // len(digest)) + 1))[: self.dim]
        return [(b - 128) / 128.0 for b in vec_bytes]
