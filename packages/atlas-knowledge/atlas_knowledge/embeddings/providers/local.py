"""SentenceTransformersEmbedder — wraps BAAI/bge-small-en-v1.5.

Loaded lazily into a process-wide cache on first call. Sync model
inference is wrapped in ``anyio.to_thread.run_sync`` to keep the
event loop responsive.

BGE convention: queries get the prefix
``"Represent this sentence for searching relevant passages: "`` so
similarity scores cluster correctly. Documents are embedded as-is.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import anyio.to_thread

from atlas_knowledge.embeddings.service import EmbeddingService

if TYPE_CHECKING:
    from sentence_transformers import SentenceTransformer

DEFAULT_MODEL = "BAAI/bge-small-en-v1.5"
QUERY_PREFIX = "Represent this sentence for searching relevant passages: "

_MODEL_CACHE: dict[str, SentenceTransformer] = {}


def _get_model(model_name: str) -> SentenceTransformer:
    if model_name not in _MODEL_CACHE:
        # Imported lazily — only when actually needed, so test runs that
        # never instantiate this class don't pay the import cost (~2s).
        from sentence_transformers import SentenceTransformer

        _MODEL_CACHE[model_name] = SentenceTransformer(model_name)
    return _MODEL_CACHE[model_name]


class SentenceTransformersEmbedder(EmbeddingService):
    """In-process embedder using sentence-transformers."""

    def __init__(
        self,
        model_name: str = DEFAULT_MODEL,
        *,
        batch_size: int = 32,
    ) -> None:
        self.model_id = model_name
        self.batch_size = batch_size

    @property
    def dim(self) -> int:
        return _get_model(self.model_id).get_sentence_embedding_dimension()

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        def _encode() -> list[list[float]]:
            model = _get_model(self.model_id)
            arr = model.encode(
                texts,
                batch_size=self.batch_size,
                normalize_embeddings=True,
                show_progress_bar=False,
            )
            return arr.tolist()

        return await anyio.to_thread.run_sync(_encode)

    async def embed_query(self, text: str) -> list[float]:
        prefixed = QUERY_PREFIX + text

        def _encode() -> list[float]:
            model = _get_model(self.model_id)
            arr = model.encode(
                [prefixed],
                normalize_embeddings=True,
                show_progress_bar=False,
            )
            return arr[0].tolist()

        return await anyio.to_thread.run_sync(_encode)
