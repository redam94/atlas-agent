"""Opt-in real-Neo4j acceptance test for Plan 5 subgraph fetches.

Run with: ATLAS_RUN_NEO4J_INTEGRATION=1 uv run pytest -m slow ...
"""
from __future__ import annotations

import os
from uuid import uuid4

import pytest
import pytest_asyncio

pytestmark = pytest.mark.skipif(
    os.getenv("ATLAS_RUN_NEO4J_INTEGRATION") != "1",
    reason="set ATLAS_RUN_NEO4J_INTEGRATION=1 to enable",
)


@pytest_asyncio.fixture
async def seeded_project(real_graph_store, isolated_project_id):
    """Tiny project: 1 doc, 2 chunks, 3 entities. Mentions edges between them."""
    pid = isolated_project_id
    doc_id = uuid4()
    chunk_a, chunk_b = uuid4(), uuid4()
    ent1, ent2, ent3 = uuid4(), uuid4(), uuid4()

    async with real_graph_store._driver.session() as s:
        await s.run(
            """
            CREATE (d:Document {id: $doc, project_id: $pid, title: "Doc", source_type: "markdown"})
            CREATE (c1:Chunk {id: $c1, project_id: $pid, document_id: $doc, chunk_index: 0, text: "alpha"})
            CREATE (c2:Chunk {id: $c2, project_id: $pid, document_id: $doc, chunk_index: 1, text: "beta"})
            CREATE (e1:Entity {id: $e1, project_id: $pid, name: "E1", type: "PERSON",
                              pagerank_global: 0.5, mention_count: 2})
            CREATE (e2:Entity {id: $e2, project_id: $pid, name: "E2", type: "ORG",
                              pagerank_global: 0.3, mention_count: 1})
            CREATE (e3:Entity {id: $e3, project_id: $pid, name: "E3", type: "CONCEPT",
                              pagerank_global: 0.1, mention_count: 1})
            CREATE (d)-[:HAS_CHUNK]->(c1)
            CREATE (d)-[:HAS_CHUNK]->(c2)
            CREATE (c1)-[:MENTIONS]->(e1)
            CREATE (c1)-[:MENTIONS]->(e2)
            CREATE (c2)-[:MENTIONS]->(e2)
            CREATE (c2)-[:MENTIONS]->(e3)
            CREATE (e1)-[:RELATED_TO]->(e2)
            """,
            doc=str(doc_id), pid=str(pid),
            c1=str(chunk_a), c2=str(chunk_b),
            e1=str(ent1), e2=str(ent2), e3=str(ent3),
        )
    return {
        "pid": pid,
        "doc_id": doc_id,
        "chunks": [chunk_a, chunk_b],
        "entities": [ent1, ent2, ent3],
    }


@pytest.mark.asyncio
@pytest.mark.slow
async def test_fetch_top_entities_returns_entities_sorted_by_pagerank(
    real_graph_store, seeded_project
):
    nodes, edges = await real_graph_store.fetch_top_entities(
        project_id=seeded_project["pid"], limit=10
    )
    assert len(nodes) == 3
    assert all(n["type"] == "Entity" for n in nodes)
    pageranks = [n["pagerank"] for n in nodes]
    assert pageranks == sorted(pageranks, reverse=True)
    # E1-E2 RELATED_TO edge is between two top entities
    assert any(e["type"] == "RELATED_TO" for e in edges)


@pytest.mark.asyncio
@pytest.mark.slow
async def test_fetch_subgraph_by_seeds_expands_one_hop(
    real_graph_store, seeded_project
):
    chunk_a = seeded_project["chunks"][0]
    nodes, edges = await real_graph_store.fetch_subgraph_by_seeds(
        project_id=seeded_project["pid"],
        seed_ids=[chunk_a],
        neighbors_per_seed=25,
    )
    node_ids = {n["id"] for n in nodes}
    # The seed is included.
    assert str(chunk_a) in node_ids
    # 1-hop: doc + 2 entities mentioned by chunk_a.
    assert len(nodes) >= 4
    # MENTIONS and HAS_CHUNK edges present.
    edge_types = {e["type"] for e in edges}
    assert "MENTIONS" in edge_types
    assert "HAS_CHUNK" in edge_types


@pytest.mark.asyncio
@pytest.mark.slow
async def test_tag_note_creates_tagged_with_edges(
    real_graph_store, isolated_project_id
):
    pid = isolated_project_id
    note_doc_id = uuid4()
    ent1, ent2 = uuid4(), uuid4()

    async with real_graph_store._driver.session() as s:
        await s.run(
            """
            CREATE (n:Document {id: $note, project_id: $pid, title: "My note", type: "note"})
            CREATE (e1:Entity {id: $e1, project_id: $pid, name: "X", type: "PERSON"})
            CREATE (e2:Entity {id: $e2, project_id: $pid, name: "Y", type: "ORG"})
            """,
            note=str(note_doc_id), pid=str(pid),
            e1=str(ent1), e2=str(ent2),
        )

    await real_graph_store.tag_note(note_id=note_doc_id, entity_ids=[ent1, ent2])

    # Idempotent — calling twice doesn't duplicate edges.
    await real_graph_store.tag_note(note_id=note_doc_id, entity_ids=[ent1, ent2])

    async with real_graph_store._driver.session() as s:
        result = await s.run(
            """
            MATCH (n:Document {id: $note})-[r:TAGGED_WITH]->(e:Entity)
            RETURN count(r) AS count
            """,
            note=str(note_doc_id),
        )
        records = await result.data()

    assert records[0]["count"] == 2


@pytest.mark.asyncio
@pytest.mark.slow
async def test_list_entities_prefix_match(real_graph_store, isolated_project_id):
    pid = isolated_project_id
    async with real_graph_store._driver.session() as s:
        await s.run(
            """
            CREATE (e1:Entity {id: $e1, project_id: $pid, name: "Llama 3", type: "PRODUCT", pagerank_global: 0.9})
            CREATE (e2:Entity {id: $e2, project_id: $pid, name: "Llama 2", type: "PRODUCT", pagerank_global: 0.5})
            CREATE (e3:Entity {id: $e3, project_id: $pid, name: "Mistral", type: "PRODUCT", pagerank_global: 0.7})
            """,
            pid=str(pid),
            e1=str(uuid4()), e2=str(uuid4()), e3=str(uuid4()),
        )
    rows = await real_graph_store.list_entities(project_id=pid, prefix="Lla", limit=10)
    names = [r["name"] for r in rows]
    assert names == ["Llama 3", "Llama 2"]  # ordered by pagerank DESC

    # Empty prefix returns all 3, ordered by pagerank.
    rows = await real_graph_store.list_entities(project_id=pid, prefix="", limit=10)
    assert [r["name"] for r in rows] == ["Llama 3", "Mistral", "Llama 2"]
