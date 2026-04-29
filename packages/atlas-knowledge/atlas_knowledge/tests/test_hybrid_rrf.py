"""Pure-function tests for Reciprocal Rank Fusion merge."""
from uuid import UUID, uuid4

from atlas_knowledge.retrieval.hybrid.rrf import merge


def test_merge_empty_input():
    assert merge([], k=60, top_k=20) == []


def test_merge_all_empty_lists():
    assert merge([[], []], k=60, top_k=20) == []


def test_merge_single_list_preserves_order():
    a, b, c = uuid4(), uuid4(), uuid4()
    out = merge([[(a, 1), (b, 2), (c, 3)]], k=60, top_k=10)
    ids = [t[0] for t in out]
    assert ids == [a, b, c]
    # Scores strictly decreasing
    scores = [t[1] for t in out]
    assert scores == sorted(scores, reverse=True)


def test_merge_two_lists_combines_scores():
    a, b, c = uuid4(), uuid4(), uuid4()
    # `a` ranks 1 in both lists -> highest score
    # `b` ranks 2 in both lists
    # `c` only appears in one list at rank 3
    out = merge(
        [[(a, 1), (b, 2), (c, 3)], [(a, 1), (b, 2)]], k=60, top_k=10
    )
    by_id = dict(out)
    assert by_id[a] > by_id[b] > by_id[c]
    # Score formula: a -> 2 * 1/(60+1); b -> 2 * 1/(60+2); c -> 1/(60+3)
    assert abs(by_id[a] - 2 / 61) < 1e-9
    assert abs(by_id[b] - 2 / 62) < 1e-9
    assert abs(by_id[c] - 1 / 63) < 1e-9


def test_merge_truncates_to_top_k():
    ids = [uuid4() for _ in range(50)]
    ranking = [(i, idx) for idx, i in enumerate(ids, start=1)]
    out = merge([ranking], k=60, top_k=20)
    assert len(out) == 20
    # First 20 ids preserved
    assert [t[0] for t in out] == ids[:20]


def test_merge_rank_position_starts_at_one():
    a = uuid4()
    out = merge([[(a, 1)]], k=60, top_k=1)
    assert out[0][0] == a
    assert abs(out[0][1] - 1 / 61) < 1e-9
