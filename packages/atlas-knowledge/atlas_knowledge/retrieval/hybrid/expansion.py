"""Thin atlas-knowledge wrapper around GraphStore.expand_chunks.

Lives here (not in atlas-graph) so that the hybrid orchestration imports
its dependencies from one package.
"""
from __future__ import annotations

from uuid import UUID

from atlas_graph import ExpansionSubgraph
from atlas_graph.store import GraphStore


async def expand(
    graph_store: GraphStore,
    project_id: UUID,
    seeds: list[UUID],
    cap: int = 100,
) -> ExpansionSubgraph:
    return await graph_store.expand_chunks(
        project_id=project_id, seeds=seeds, cap=cap
    )
