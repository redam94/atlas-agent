"""Retrieval package — vector (Phase 1) and hybrid (Plan 4) retrievers."""

from typing import Protocol

from atlas_knowledge.models.retrieval import RetrievalQuery, RetrievalResult
from atlas_knowledge.retrieval.builder import build_rag_context
from atlas_knowledge.retrieval.retriever import Retriever


class RetrieverProtocol(Protocol):
    async def retrieve(self, query: RetrievalQuery) -> RetrievalResult: ...


__all__ = ["Retriever", "RetrieverProtocol", "build_rag_context"]
