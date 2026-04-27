"""Embedding service abstraction + concrete providers."""

from atlas_knowledge.embeddings.providers import (
    DEFAULT_MODEL,
    FakeEmbedder,
    SentenceTransformersEmbedder,
)
from atlas_knowledge.embeddings.service import EmbeddingService

__all__ = [
    "DEFAULT_MODEL",
    "EmbeddingService",
    "FakeEmbedder",
    "SentenceTransformersEmbedder",
]
