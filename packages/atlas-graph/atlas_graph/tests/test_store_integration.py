"""GraphStore.write_document_chunks against a real Neo4j."""
from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest

from atlas_graph.protocols import ChunkSpec, ChunkWithText

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_write_document_chunks_creates_nodes_and_edges(
    real_graph_store, real_neo4j_driver, isolated_project_id,
):
    pid = isolated_project_id
    did = uuid4()
    chunks = [
        ChunkSpec(id=uuid4(), position=0, token_count=100, text_preview="alpha"),
        ChunkSpec(id=uuid4(), position=1, token_count=120, text_preview="beta"),
        ChunkSpec(id=uuid4(), position=2, token_count=80, text_preview="gamma"),
    ]
    await real_graph_store.write_document_chunks(
        project_id=pid,
        project_name="IntegrationTest",
        document_id=did,
        document_title="Doc",
        document_source_type="markdown",
        document_metadata={"author": "matt"},
        document_created_at=datetime.now(UTC),
        chunks=chunks,
    )

    async with real_neo4j_driver.session() as s:
        result = await s.run(
            "MATCH (p:Project {id: $pid}) "
            "OPTIONAL MATCH (d:Document {id: $did})-[:PART_OF]->(p) "
            "OPTIONAL MATCH (c:Chunk)-[:BELONGS_TO]->(d) "
            "RETURN count(DISTINCT p) AS projects, count(DISTINCT d) AS docs, "
            "       count(DISTINCT c) AS chunks",
            pid=str(pid), did=str(did),
        )
        rec = await result.single()
    assert rec["projects"] == 1
    assert rec["docs"] == 1
    assert rec["chunks"] == 3


@pytest.mark.asyncio
async def test_write_document_chunks_idempotent(
    real_graph_store, real_neo4j_driver, isolated_project_id,
):
    pid = isolated_project_id
    did = uuid4()
    cid = uuid4()
    spec = ChunkSpec(id=cid, position=0, token_count=10, text_preview="x")

    for _ in range(2):
        await real_graph_store.write_document_chunks(
            project_id=pid, project_name="P", document_id=did,
            document_title="t", document_source_type="markdown",
            document_metadata={}, document_created_at=datetime.now(UTC),
            chunks=[spec],
        )

    async with real_neo4j_driver.session() as s:
        result = await s.run(
            "MATCH (c:Chunk {id: $cid})-[r:BELONGS_TO]->(d:Document {id: $did}) "
            "RETURN count(c) AS chunks, count(r) AS edges",
            cid=str(cid), did=str(did),
        )
        rec = await result.single()
    # Two calls but MERGE means one node and one edge.
    assert rec["chunks"] == 1
    assert rec["edges"] == 1


@pytest.mark.asyncio
async def test_healthcheck_against_real_neo4j(real_graph_store):
    await real_graph_store.healthcheck()  # no exception = pass


@pytest.mark.asyncio
async def test_full_plan3_pipeline_against_real_neo4j(
    real_graph_store, real_neo4j_driver, isolated_project_id,
):
    """End-to-end: write doc/chunks → entities → semantic → temporal → pagerank."""
    from atlas_graph.ingestion.ner import Entity

    pid = isolated_project_id
    did = uuid4()
    chunks = [
        ChunkSpec(id=uuid4(), position=i, token_count=128, text_preview=f"c{i}")
        for i in range(3)
    ]

    # 1. Structural write.
    await real_graph_store.write_document_chunks(
        project_id=pid,
        project_name="Plan3 Test",
        document_id=did,
        document_title="Doc",
        document_source_type="markdown",
        document_metadata={},
        document_created_at=datetime.now(UTC),
        chunks=chunks,
    )

    # 2. Inject deterministic entities (bypass LM Studio for the integration test).
    class _StubNer:
        async def extract_batch(self, items):
            return {cid: [Entity(name="CircleK", type="CLIENT")] for cid, _ in items}

    real_graph_store._ner_extractor = _StubNer()
    await real_graph_store.write_entities(
        project_id=pid,
        chunks=[ChunkWithText(id=c.id, text="we worked with CircleK") for c in chunks],
    )

    # 3. Semantic-near (synthesize one canonicalized pair).
    a, b = sorted((str(chunks[0].id), str(chunks[1].id)))
    await real_graph_store.merge_semantic_near(
        pairs=[(UUID(a), UUID(b), 0.92)],
    )

    # 4. Temporal-near. (Single-doc case → 0 edges; this just confirms no error.)
    await real_graph_store.build_temporal_near(
        project_id=pid, document_id=did, window_days=7,
    )

    # 5. PageRank.
    await real_graph_store.run_pagerank(project_id=pid)

    # Assertions via raw Cypher.
    async with real_neo4j_driver.session() as s:
        # Entities created (deduplicated via flatten() — 3 chunks reference 1 entity).
        rec = await (await s.run(
            "MATCH (e:Entity {project_id: $pid, type: 'CLIENT'}) RETURN count(e) AS n",
            pid=str(pid),
        )).single()
        assert rec["n"] == 1, f"expected 1 CLIENT entity, got {rec['n']}"

        # 3 REFERENCES edges (one per chunk).
        rec = await (await s.run(
            "MATCH (:Chunk {project_id: $pid})-[:REFERENCES]->(:Entity {project_id: $pid}) "
            "RETURN count(*) AS n",
            pid=str(pid),
        )).single()
        assert rec["n"] == 3, f"expected 3 REFERENCES edges, got {rec['n']}"

        # SEMANTICALLY_NEAR — undirected MATCH returns each pair both ways.
        rec = await (await s.run(
            "MATCH (:Chunk {project_id: $pid})-[:SEMANTICALLY_NEAR]-(:Chunk {project_id: $pid}) "
            "RETURN count(*) AS n",
            pid=str(pid),
        )).single()
        assert rec["n"] == 2, f"expected 2 SEMANTICALLY_NEAR rows (one undirected edge), got {rec['n']}"

        # PageRank scores: every node in the project subgraph should have pagerank_global.
        rec = await (await s.run(
            "MATCH (n) WHERE n.project_id = $pid RETURN count(n.pagerank_global) AS n",
            pid=str(pid),
        )).single()
        # Project (no project_id self-prop, but PR projection includes it via id() match) +
        # Document + 1 Entity + 3 Chunks = 5 nodes minimum with pagerank_global.
        assert rec["n"] >= 4, f"expected >=4 nodes with pagerank_global, got {rec['n']}"
