"""Embedding service abstraction + concrete providers."""

from atlas_knowledge.embeddings.providers import FakeEmbedder
from atlas_knowledge.embeddings.service import EmbeddingService

__all__ = ["EmbeddingService", "FakeEmbedder"]
