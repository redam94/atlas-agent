"""GraphStore.expand_chunks against a real Neo4j (Plan 3 schema required)."""
from __future__ import annotations

from uuid import uuid4

import pytest

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_expand_chunks_walks_sn_and_references(
    real_graph_store, real_neo4j_driver, isolated_project_id,
):
    pid = isolated_project_id
    seed = uuid4()
    sn_neighbor = uuid4()
    ref_neighbor = uuid4()
    isolated = uuid4()  # has no edges; should not appear

    async with real_neo4j_driver.session() as s:
        # Seed + neighbors as Chunks
        for cid in (seed, sn_neighbor, ref_neighbor, isolated):
            await s.run(
                "MERGE (c:Chunk {id: $id}) SET c.project_id = $pid, c.pagerank_global = 0.1",
                id=str(cid), pid=str(pid),
            )
        # SEMANTICALLY_NEAR edge
        await s.run(
            "MATCH (a:Chunk {id: $a}), (b:Chunk {id: $b}) "
            "MERGE (a)-[r:SEMANTICALLY_NEAR]-(b) SET r.cosine = 0.91",
            a=str(seed), b=str(sn_neighbor),
        )
        # Shared Entity for REFERENCES
        eid = uuid4()
        await s.run(
            "MERGE (e:Entity {project_id: $pid, name: 'circlek', type: 'CLIENT'}) "
            "SET e.id = $eid",
            pid=str(pid), eid=str(eid),
        )
        await s.run(
            "MATCH (c:Chunk {id: $c}), (e:Entity {id: $eid}) "
            "MERGE (c)-[:REFERENCES]->(e)",
            c=str(seed), eid=str(eid),
        )
        await s.run(
            "MATCH (c:Chunk {id: $c}), (e:Entity {id: $eid}) "
            "MERGE (c)-[:REFERENCES]->(e)",
            c=str(ref_neighbor), eid=str(eid),
        )

    sub = await real_graph_store.expand_chunks(
        project_id=pid, seeds=[seed], cap=100
    )

    assert seed in sub.nodes
    assert sn_neighbor in sub.nodes
    assert ref_neighbor in sub.nodes
    assert isolated not in sub.nodes

    # SN edge weight = cosine
    sn_edges = [(a, b, w) for (a, b, w) in sub.edges if w == 0.91]
    assert len(sn_edges) >= 1
    # REF edge weight = shared-entity count = 1 (one shared entity)
    ref_edges = [(a, b, w) for (a, b, w) in sub.edges if w == 1.0]
    assert any(seed in (a, b) and ref_neighbor in (a, b) for a, b, _ in ref_edges)


@pytest.mark.asyncio
async def test_expand_chunks_respects_cap_with_split(
    real_graph_store, real_neo4j_driver, isolated_project_id,
):
    pid = isolated_project_id
    seed = uuid4()
    async with real_neo4j_driver.session() as s:
        await s.run(
            "MERGE (c:Chunk {id: $id}) SET c.project_id = $pid",
            id=str(seed), pid=str(pid),
        )
        eid = uuid4()
        await s.run(
            "MERGE (e:Entity {project_id: $pid, name: 'hub', type: 'CLIENT'}) "
            "SET e.id = $eid",
            pid=str(pid), eid=str(eid),
        )
        await s.run(
            "MATCH (c:Chunk {id: $c}), (e:Entity {id: $eid}) "
            "MERGE (c)-[:REFERENCES]->(e)",
            c=str(seed), eid=str(eid),
        )
        # 30 ref-neighbors all sharing the hub entity, 0 SN neighbors
        for _ in range(30):
            n = uuid4()
            await s.run(
                "MERGE (c:Chunk {id: $id}) SET c.project_id = $pid",
                id=str(n), pid=str(pid),
            )
            await s.run(
                "MATCH (c:Chunk {id: $c}), (e:Entity {id: $eid}) "
                "MERGE (c)-[:REFERENCES]->(e)",
                c=str(n), eid=str(eid),
            )

    sub = await real_graph_store.expand_chunks(
        project_id=pid, seeds=[seed], cap=10
    )
    # Seed + up to 9 neighbors. Since SN supplies 0, REF takes the full surplus.
    assert len(sub.nodes) == 10
