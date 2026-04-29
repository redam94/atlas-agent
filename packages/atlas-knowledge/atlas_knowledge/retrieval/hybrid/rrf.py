"""Reciprocal Rank Fusion merge.

Operates on rank positions (1-indexed) so different rankers (BM25 and dense
vector) can be combined without normalizing their raw scores. Standard k=60.
"""
from __future__ import annotations

from collections import defaultdict
from uuid import UUID


def merge(
    rankings: list[list[tuple[UUID, int]]],
    k: int = 60,
    top_k: int = 20,
) -> list[tuple[UUID, float]]:
    """Reciprocal Rank Fusion of ``rankings``.

    For each item ``id``: ``score(id) = sum(1 / (k + rank_i))`` over every
    ranking that contains ``id``. Returns the top-``top_k`` items sorted by
    descending score.
    """
    scores: defaultdict[UUID, float] = defaultdict(float)
    for ranking in rankings:
        for chunk_id, rank in ranking:
            scores[chunk_id] += 1.0 / (k + rank)
    items = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    return items[:top_k]
