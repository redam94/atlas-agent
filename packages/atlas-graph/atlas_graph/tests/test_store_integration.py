"""GraphStore.write_document_chunks against a real Neo4j."""
from __future__ import annotations

from uuid import uuid4

import pytest

from atlas_graph.protocols import ChunkSpec

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
            document_metadata={}, chunks=[spec],
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
