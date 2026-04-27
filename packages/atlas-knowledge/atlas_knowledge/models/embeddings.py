"""Embedding service request/response shapes."""

from atlas_core.models.base import AtlasModel
from pydantic import Field


class EmbeddingRequest(AtlasModel):
    """Batch of texts to embed."""

    texts: list[str] = Field(min_length=1)


class EmbeddingResult(AtlasModel):
    """Embedding vectors returned by an EmbeddingService.

    ``vectors[i]`` is the embedding for ``texts[i]`` of the originating
    request — caller-side correlation, no IDs in the result type.
    """

    vectors: list[list[float]]
    model_id: str
