"""Plan 4 graph expansion contract.

Returned to atlas_knowledge.retrieval.hybrid for per-query graph walks. The
weights have heterogeneous scales (REFERENCES = shared-entity *count*,
SEMANTICALLY_NEAR = *cosine*); see store.expand_chunks for the budget split
that handles this.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from uuid import UUID


@dataclass
class ExpansionSubgraph:
    """Subgraph rooted at a list of seed chunks plus their 1-hop neighbors.

    ``nodes`` maps chunk_id -> pagerank_global score (0.0 if absent).
    ``edges`` is undirected; each tuple is (a, b, weight).
    """

    nodes: dict[UUID, float] = field(default_factory=dict)
    edges: list[tuple[UUID, UUID, float]] = field(default_factory=list)


# 1-hop SEMANTICALLY_NEAR neighbors with cosine on the relation.
EXPAND_SN_CYPHER = """
MATCH (c:Chunk) WHERE c.id IN $seeds AND c.project_id = $pid
MATCH (c)-[r:SEMANTICALLY_NEAR]-(n:Chunk)
WHERE n.project_id = $pid
RETURN c.id AS a, n.id AS b, r.cosine AS w,
       coalesce(c.pagerank_global, 0.0) AS pa,
       coalesce(n.pagerank_global, 0.0) AS pb
"""

# 1-hop REFERENCES-via-Entity neighbors with weight = COUNT(DISTINCT shared_entity).
EXPAND_REF_CYPHER = """
MATCH (c:Chunk) WHERE c.id IN $seeds AND c.project_id = $pid
MATCH (c)-[:REFERENCES]->(e:Entity)<-[:REFERENCES]-(n:Chunk)
WHERE n.project_id = $pid AND n.id <> c.id
WITH c, n, count(DISTINCT e) AS w
RETURN c.id AS a, n.id AS b, toFloat(w) AS w,
       coalesce(c.pagerank_global, 0.0) AS pa,
       coalesce(n.pagerank_global, 0.0) AS pb
"""

# Pagerank for the seeds themselves (so ExpansionSubgraph.nodes carries a value
# for every seed even when the seed has no neighbors).
SEEDS_PR_CYPHER = """
MATCH (c:Chunk) WHERE c.id IN $seeds AND c.project_id = $pid
RETURN c.id AS id, coalesce(c.pagerank_global, 0.0) AS pr
"""


def merge_neighbors_with_budget(
    seeds: list[UUID],
    sn_rows: list[tuple[UUID, UUID, float, float, float]],
    ref_rows: list[tuple[UUID, UUID, float, float, float]],
    seed_prs: dict[UUID, float],
    cap: int,
) -> ExpansionSubgraph:
    """Apply the per-edge-type cap split.

    Seeds are always retained. Of the remaining ``cap - len(seeds)`` budget,
    each edge type gets up to half (sorted by descending weight); surplus
    rolls over to the other side.
    """
    sub = ExpansionSubgraph()
    for s in seeds:
        sub.nodes[s] = seed_prs.get(s, 0.0)

    sn_neighbors: dict[UUID, tuple[float, float]] = {}  # node -> (best_weight, pr)
    sn_edges: list[tuple[UUID, UUID, float]] = []
    for a, b, w, _pa, pb in sn_rows:
        sn_edges.append((a, b, float(w)))
        prev = sn_neighbors.get(b)
        if prev is None or float(w) > prev[0]:
            sn_neighbors[b] = (float(w), float(pb))

    ref_neighbors: dict[UUID, tuple[float, float]] = {}
    ref_edges: list[tuple[UUID, UUID, float]] = []
    for a, b, w, _pa, pb in ref_rows:
        ref_edges.append((a, b, float(w)))
        prev = ref_neighbors.get(b)
        if prev is None or float(w) > prev[0]:
            ref_neighbors[b] = (float(w), float(pb))

    # Drop neighbors that are already seeds (they're already in sub.nodes).
    seed_set = set(seeds)
    sn_sorted = sorted(
        ((nid, w_pr) for nid, w_pr in sn_neighbors.items() if nid not in seed_set),
        key=lambda kv: kv[1][0],
        reverse=True,
    )
    ref_sorted = sorted(
        ((nid, w_pr) for nid, w_pr in ref_neighbors.items() if nid not in seed_set),
        key=lambda kv: kv[1][0],
        reverse=True,
    )

    remaining = max(0, cap - len(sub.nodes))
    sn_quota = remaining // 2
    ref_quota = remaining - sn_quota

    # Allocate, then roll surplus.
    sn_take = min(sn_quota, len(sn_sorted))
    ref_take = min(ref_quota, len(ref_sorted))
    sn_surplus = sn_quota - sn_take
    ref_surplus = ref_quota - ref_take
    if sn_surplus > 0 and ref_take < len(ref_sorted):
        extra = min(sn_surplus, len(ref_sorted) - ref_take)
        ref_take += extra
    if ref_surplus > 0 and sn_take < len(sn_sorted):
        extra = min(ref_surplus, len(sn_sorted) - sn_take)
        sn_take += extra

    for nid, (_w, pr) in sn_sorted[:sn_take]:
        sub.nodes[nid] = pr
    for nid, (_w, pr) in ref_sorted[:ref_take]:
        # Ref-side may already be present from SN; keep the existing pr (same node, same value).
        sub.nodes.setdefault(nid, pr)

    # Edges: keep only those whose endpoints both survived.
    surviving = set(sub.nodes.keys())
    for a, b, w in sn_edges + ref_edges:
        if a in surviving and b in surviving:
            sub.edges.append((a, b, w))

    return sub
