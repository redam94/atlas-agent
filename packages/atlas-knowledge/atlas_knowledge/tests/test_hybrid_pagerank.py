"""Unit tests for in-process personalized PageRank over ExpansionSubgraph."""
from __future__ import annotations

from uuid import uuid4

from atlas_graph.expansion import ExpansionSubgraph
from atlas_knowledge.retrieval.hybrid.pagerank import personalized


def test_empty_subgraph_returns_empty():
    assert personalized(ExpansionSubgraph(), [], damping=0.85) == {}


def test_empty_seeds_returns_empty():
    sub = ExpansionSubgraph(nodes={uuid4(): 0.0}, edges=[])
    assert personalized(sub, [], damping=0.85) == {}


def test_seed_outweighs_far_node():
    a, b, c = uuid4(), uuid4(), uuid4()
    # Chain a -- b -- c. Seed a; with undirected edges, middle node b becomes more
    # central (has flow from both sides), so ranking is a > b > c only if we look at
    # seed vs non-seed. Here we verify seed_a > far_c (b is in between and more central).
    sub = ExpansionSubgraph(
        nodes={a: 0.0, b: 0.0, c: 0.0},
        edges=[(a, b, 1.0), (b, c, 1.0)],
    )
    out = personalized(sub, seeds=[a], damping=0.85)
    # Verify seed a is ranked above far node c (middle node b may be ranked higher).
    assert out[a] > out[c]


def test_scores_normalized_to_sum_one():
    nodes = {uuid4(): 0.0 for _ in range(5)}
    ids = list(nodes.keys())
    edges = [(ids[i], ids[i + 1], 1.0) for i in range(4)]
    sub = ExpansionSubgraph(nodes=nodes, edges=edges)
    out = personalized(sub, seeds=[ids[0]], damping=0.85)
    assert abs(sum(out.values()) - 1.0) < 1e-6


def test_isolated_seed_returns_full_weight():
    a = uuid4()
    sub = ExpansionSubgraph(nodes={a: 0.0}, edges=[])
    out = personalized(sub, seeds=[a], damping=0.85)
    assert abs(out[a] - 1.0) < 1e-6
