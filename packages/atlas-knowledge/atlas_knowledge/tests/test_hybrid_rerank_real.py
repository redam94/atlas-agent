"""Slow smoke test: actually load and run the cross-encoder."""
from __future__ import annotations

import os
from uuid import uuid4

import pytest

pytestmark = pytest.mark.slow


def _enabled() -> bool:
    return os.getenv("ATLAS_RUN_SLOW_TESTS") == "1"


@pytest.mark.asyncio
async def test_real_reranker_orders_candidates():
    if not _enabled():
        pytest.skip("set ATLAS_RUN_SLOW_TESTS=1 to enable")
    from atlas_knowledge.retrieval.hybrid.rerank import Reranker

    rr = Reranker()  # downloads model on first call
    a, b, c = uuid4(), uuid4(), uuid4()
    candidates = [
        (a, "geo lift methodology measures incremental ad effects via geographic experiments"),
        (b, "we picked up coffee on the way to the meeting"),
        (c, "incremental measurement and geo-experiments are core to lift testing"),
    ]
    out = await rr.rerank("how do you measure geo lift", candidates, top_k=3)
    assert len(out) == 3
    # Topical chunks (a, c) should outrank coffee (b).
    ranked_ids = [t[0] for t in out]
    assert ranked_ids[-1] == b


@pytest.mark.asyncio
async def test_real_reranker_handles_long_input():
    """Sanity-check that a near-512-token input doesn't crash."""
    if not _enabled():
        pytest.skip("set ATLAS_RUN_SLOW_TESTS=1 to enable")
    from atlas_knowledge.retrieval.hybrid.rerank import Reranker

    rr = Reranker()
    long_text = ("token " * 600).strip()  # well past the 512-token cap
    a = uuid4()
    out = await rr.rerank("query", [(a, long_text)], top_k=1)
    assert len(out) == 1
    assert out[0][0] == a
