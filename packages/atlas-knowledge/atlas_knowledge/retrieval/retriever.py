"""Retriever — query → embed → vector search → ScoredChunk[]."""

from atlas_knowledge.embeddings.service import EmbeddingService
from atlas_knowledge.models.retrieval import RetrievalQuery, RetrievalResult
from atlas_knowledge.vector.store import VectorStore


class Retriever:
    """Phase 1 dense-only retriever."""

    def __init__(self, embedder: EmbeddingService, vector_store: VectorStore) -> None:
        self._embedder = embedder
        self._vector_store = vector_store

    async def retrieve(self, query: RetrievalQuery) -> RetrievalResult:
        embedding = await self._embedder.embed_query(query.text)

        filter_dict: dict[str, object] = {"project_id": str(query.project_id)}
        if query.filter:
            filter_dict.update(query.filter)

        scored = await self._vector_store.search(
            query_embedding=embedding,
            top_k=query.top_k,
            filter=filter_dict,
        )
        return RetrievalResult(query=query.text, chunks=scored)
