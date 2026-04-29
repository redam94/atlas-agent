"""Unit tests for the rerank module — exercise FakeReranker only."""
from __future__ import annotations

from uuid import uuid4

import pytest

from atlas_knowledge.retrieval.hybrid.rerank import FakeReranker


@pytest.mark.asyncio
async def test_fake_reranker_preserves_order_with_explicit_scores():
    a, b, c = uuid4(), uuid4(), uuid4()
    rr = FakeReranker(scores={a: 0.9, b: 0.5, c: 0.1})
    out = await rr.rerank("q", [(a, "x"), (b, "y"), (c, "z")], top_k=10)
    ids = [t[0] for t in out]
    assert ids == [a, b, c]


@pytest.mark.asyncio
async def test_fake_reranker_default_score_is_zero():
    a = uuid4()
    rr = FakeReranker(scores={})
    out = await rr.rerank("q", [(a, "x")], top_k=10)
    assert out == [(a, 0.0)]


@pytest.mark.asyncio
async def test_fake_reranker_truncates_to_top_k():
    ids = [uuid4() for _ in range(5)]
    rr = FakeReranker(scores={i: float(idx) for idx, i in enumerate(ids)})
    out = await rr.rerank("q", [(i, "t") for i in ids], top_k=3)
    assert len(out) == 3
    # Highest scores first; FakeReranker sorts descending.
    assert [t[0] for t in out] == ids[::-1][:3]


@pytest.mark.asyncio
async def test_fake_reranker_empty_input():
    rr = FakeReranker(scores={})
    assert await rr.rerank("q", [], top_k=10) == []
