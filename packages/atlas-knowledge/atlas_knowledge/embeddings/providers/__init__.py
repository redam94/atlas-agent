"""Concrete embedding providers."""

from atlas_knowledge.embeddings.providers._fake import FakeEmbedder
from atlas_knowledge.embeddings.providers.local import (
    DEFAULT_MODEL,
    SentenceTransformersEmbedder,
)

__all__ = ["DEFAULT_MODEL", "FakeEmbedder", "SentenceTransformersEmbedder"]
