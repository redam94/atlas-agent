"""In-process personalized PageRank over an ExpansionSubgraph using igraph.

Subgraphs at retrieval time are small (≤100 nodes); building a fresh igraph.Graph
per query is sub-millisecond and avoids the cost of a Cypher-side gds projection.
"""
from __future__ import annotations

from uuid import UUID

import igraph as ig

from atlas_graph.expansion import ExpansionSubgraph


def personalized(
    subgraph: ExpansionSubgraph,
    seeds: list[UUID],
    damping: float = 0.85,
) -> dict[UUID, float]:
    """Return chunk_id -> personalized PageRank score (sums to 1.0).

    ``seeds`` are the reset vertices. Seed ids that are not present in
    ``subgraph.nodes`` are silently dropped. Empty subgraph or empty
    surviving-seed list returns ``{}``.
    """
    if not subgraph.nodes or not seeds:
        return {}
    surviving_seeds = [s for s in seeds if s in subgraph.nodes]
    if not surviving_seeds:
        return {}

    node_ids = list(subgraph.nodes.keys())
    index = {nid: i for i, nid in enumerate(node_ids)}

    g = ig.Graph(n=len(node_ids), directed=False)
    if subgraph.edges:
        edge_list = [(index[a], index[b]) for a, b, _ in subgraph.edges if a in index and b in index]
        weights = [w for a, b, w in subgraph.edges if a in index and b in index]
        g.add_edges(edge_list)
        g.es["weight"] = weights

    reset = [0.0] * len(node_ids)
    seed_weight = 1.0 / len(surviving_seeds)
    for s in surviving_seeds:
        reset[index[s]] = seed_weight

    weights_arg = g.es["weight"] if g.ecount() > 0 else None
    scores = g.personalized_pagerank(
        damping=damping, reset=reset, weights=weights_arg
    )

    return {nid: float(scores[index[nid]]) for nid in node_ids}
