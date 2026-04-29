"""Cross-encoder reranker — lazy-loaded singleton plus FakeReranker for tests.

The real reranker uses ``sentence_transformers.CrossEncoder``. The tokenizer
silently truncates inputs longer than the model's max sequence length (512
tokens for ms-marco-MiniLM-L-6-v2); ATLAS chunks target ≤512 tokens by design.
"""
from __future__ import annotations

import asyncio
from typing import Protocol
from uuid import UUID


class RerankerProtocol(Protocol):
    async def rerank(
        self,
        query: str,
        candidates: list[tuple[UUID, str]],
        top_k: int = 30,
    ) -> list[tuple[UUID, float]]: ...


class Reranker:
    """Lazy-loaded sentence-transformers CrossEncoder.

    Constructed once in the API lifespan; the underlying model is downloaded
    and held in memory on the first ``rerank()`` call. Predict runs inside
    ``asyncio.to_thread`` to keep the event loop responsive.
    """

    def __init__(self, model_name: str = "cross-encoder/ms-marco-MiniLM-L-6-v2") -> None:
        self._model_name = model_name
        self._model = None  # type: ignore[var-annotated]

    def _ensure_loaded(self):
        if self._model is None:
            from sentence_transformers import CrossEncoder

            self._model = CrossEncoder(self._model_name)
        return self._model

    async def rerank(
        self,
        query: str,
        candidates: list[tuple[UUID, str]],
        top_k: int = 30,
    ) -> list[tuple[UUID, float]]:
        if not candidates:
            return []
        # Cap the input — rerank cost is O(n).
        capped = candidates[:top_k]
        model = self._ensure_loaded()
        pairs = [(query, txt) for _, txt in capped]

        def _predict() -> list[float]:
            return [float(s) for s in model.predict(pairs)]

        scores = await asyncio.to_thread(_predict)
        ids = [cid for cid, _ in capped]
        out = list(zip(ids, scores, strict=True))
        out.sort(key=lambda kv: kv[1], reverse=True)
        return out


class FakeReranker:
    """Deterministic reranker for unit tests.

    Returns scores from ``scores`` (default 0.0 for unknown ids), sorted descending.
    Caps at ``top_k``.
    """

    def __init__(self, scores: dict[UUID, float]) -> None:
        self._scores = scores

    async def rerank(
        self,
        query: str,
        candidates: list[tuple[UUID, str]],
        top_k: int = 30,
    ) -> list[tuple[UUID, float]]:
        if not candidates:
            return []
        out = [(cid, self._scores.get(cid, 0.0)) for cid, _ in candidates]
        out.sort(key=lambda kv: kv[1], reverse=True)
        return out[:top_k]
