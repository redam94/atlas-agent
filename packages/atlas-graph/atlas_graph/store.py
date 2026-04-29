"""GraphStore — async wrapper around the neo4j driver."""
from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Awaitable, Callable, Sequence
from contextlib import asynccontextmanager
from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID

import structlog
from neo4j.exceptions import ServiceUnavailable, TransientError

from atlas_graph.errors import GraphUnavailableError
from atlas_graph.protocols import ChunkSpec, ChunkWithText

if TYPE_CHECKING:
    from neo4j import AsyncDriver
    from neo4j._async.driver import AsyncTransaction

    from atlas_graph.ingestion.ner import NerExtractor

log = structlog.get_logger("atlas.graph.store")

# Plan 5 — UI subgraph fetches.
TOP_ENTITIES_CYPHER = """
MATCH (e:Entity {project_id: $pid})
RETURN e.id AS id, e.name AS label, e.type AS entity_type,
       coalesce(e.pagerank_global, 0.0) AS pagerank,
       coalesce(e.mention_count, 0) AS mention_count
ORDER BY pagerank DESC
LIMIT $limit
"""

TOP_ENTITIES_EDGES_CYPHER = """
UNWIND $ids AS aid
MATCH (a:Entity {id: aid})<-[:REFERENCES]-(c:Chunk)-[:REFERENCES]->(b:Entity)
WHERE b.id IN $ids AND a.id < b.id
WITH a, b, count(DISTINCT c) AS shared
RETURN a.id + '|' + b.id AS rid,
       a.id AS source, b.id AS target,
       'CO_MENTIONED' AS type,
       shared
"""

# Plan 5 — 1-hop expansion of arbitrary node ids, capped per seed via subquery.
#
# Note: the seed MATCH is intentionally label-less — seeds can be a mix of
# Document/Chunk/Entity. UUID seeds make cross-label id collisions impossible.
# This trades label-scoped indexes for the flexibility of mixed-type seeds;
# fine at project scale (typical project < 10k nodes).
SUBGRAPH_CYPHER = """
MATCH (s) WHERE s.id IN $seeds AND s.project_id = $pid
WITH collect(DISTINCT s) AS seedNodes
UNWIND seedNodes AS s
CALL {
  WITH s
  MATCH (s)-[r]-(n)
  WHERE n.project_id = s.project_id
  RETURN r, n
  ORDER BY coalesce(n.pagerank_global, 0.0) DESC
  LIMIT $cap
}
WITH seedNodes,
     collect(DISTINCT {
       id: toString(elementId(r)),
       source: startNode(r).id,
       target: endNode(r).id,
       type: type(r)
     }) AS allRels,
     collect(DISTINCT n) AS neighborNodes
WITH seedNodes + neighborNodes AS allNodes, allRels
UNWIND allNodes AS node
WITH DISTINCT node, allRels
RETURN
  node.id AS id,
  labels(node)[0] AS type,
  coalesce(node.name, node.label, node.title, left(coalesce(node.text, ''), 80), '') AS label,
  node.pagerank_global AS pagerank,
  CASE labels(node)[0]
    WHEN 'Chunk' THEN {
      document_id: node.document_id,
      chunk_index: node.chunk_index,
      text_preview: left(coalesce(node.text, ''), 200)
    }
    WHEN 'Document' THEN {
      title: node.title,
      source_type: node.source_type,
      source_url: node.source_url
    }
    WHEN 'Entity' THEN {
      entity_type: node.type,
      mention_count: coalesce(node.mention_count, 0)
    }
    ELSE {}
  END AS metadata,
  allRels AS rels
"""

# Plan 6 — explicit @-mention edges from a note's (:Document) to (:Entity) nodes.
TAG_NOTE_CYPHER = """
UNWIND $entity_ids AS eid
MATCH (n:Document {id: $note_id}), (e:Entity {id: eid})
MERGE (n)-[:TAGGED_WITH]->(e)
"""

def _serialize_metadata(metadata: dict) -> str:
    """JSON-encode the metadata dict for storage as a Neo4j string property.

    Neo4j 5 node properties must be primitives or homogeneous lists of primitives;
    maps are not allowed. We store the full metadata dict as a single JSON string
    on the Document node. Readers (Plan 5 Knowledge Explorer) JSON-decode on
    display. ``default=str`` handles non-JSON types like UUID/datetime by
    coercing to str().

    Inputs are expected to come from Postgres JSONB (str/int/float/bool/None/dict/list);
    this function does not coerce bytes/tuple/set.
    """
    return json.dumps(metadata, default=str)


class GraphStore:
    """Async wrapper around the neo4j AsyncDriver.

    Constructor does NOT open a connection — the first method call (or
    healthcheck()) is when the driver actually probes the server. On transient
    failures we retry with exponential backoff; persistent failures raise
    GraphUnavailableError.
    """

    def __init__(
        self,
        driver: AsyncDriver,
        *,
        max_retries: int = 3,
        ner_extractor: NerExtractor | None = None,
    ) -> None:
        self._driver = driver
        self._max_retries = max_retries
        self._ner_extractor = ner_extractor

    async def close(self) -> None:
        await self._driver.close()

    async def healthcheck(self) -> None:
        """Run `RETURN 1` against the driver. Raises GraphUnavailableError on persistent failure."""
        async with self._session() as s:
            await s.run("RETURN 1")

    async def _with_retry(
        self,
        fn: Callable[[AsyncTransaction], Awaitable[None]],
    ) -> None:
        """Execute fn inside a write transaction, retrying transient failures.

        Retries up to ``max_retries`` times with exponential backoff
        (0.5 s -> 1 s -> 2 s). Wraps the final failure in GraphUnavailableError.
        """
        delay = 0.5
        for attempt in range(1, self._max_retries + 1):
            try:
                async with self._session() as s:
                    await s.execute_write(fn)
                return
            except (ServiceUnavailable, TransientError) as e:
                if attempt == self._max_retries:
                    raise GraphUnavailableError(f"neo4j unavailable: {e}") from e
                log.warning("graph.retry", attempt=attempt, error=str(e))
                await asyncio.sleep(delay)
                delay *= 2

    async def write_document_chunks(
        self,
        *,
        project_id: UUID,
        project_name: str,
        document_id: UUID,
        document_title: str,
        document_source_type: str,
        document_metadata: dict,
        document_created_at: datetime,
        chunks: Sequence[ChunkSpec],
    ) -> None:
        """Write Document + Chunk nodes + structural edges in one tx.

        All MERGE — idempotent. Property values for nested dicts in
        ``document_metadata`` are JSON-encoded as strings (Neo4j 5
        property-type constraint).
        """
        meta = _serialize_metadata(document_metadata)
        chunk_params = [c.to_param() for c in chunks]
        chunk_ids = [str(c.id) for c in chunks]

        async def _do(tx: AsyncTransaction) -> None:
            await tx.run(
                "MERGE (p:Project {id: $project_id}) "
                "ON CREATE SET p.name = $name "
                "ON MATCH SET p.name = coalesce(p.name, $name)",
                project_id=str(project_id),
                name=project_name,
            )
            await tx.run(
                "MERGE (d:Document {id: $id}) "
                "SET d.project_id = $project_id, d.title = $title, "
                "    d.source_type = $source_type, d.metadata = $metadata, "
                "    d.created_at = $created_at",
                id=str(document_id),
                project_id=str(project_id),
                title=document_title,
                source_type=document_source_type,
                metadata=meta,
                created_at=document_created_at.isoformat(),
            )
            await tx.run(
                "MATCH (d:Document {id: $document_id}), (p:Project {id: $project_id}) "
                "MERGE (d)-[:PART_OF]->(p)",
                document_id=str(document_id),
                project_id=str(project_id),
            )
            await tx.run(
                "UNWIND $chunks AS c "
                "MERGE (ch:Chunk {id: c.id}) "
                "SET ch.project_id = $project_id, ch.parent_id = $document_id, "
                "    ch.position = c.position, ch.token_count = c.token_count, "
                "    ch.text_preview = c.text_preview",
                chunks=chunk_params,
                project_id=str(project_id),
                document_id=str(document_id),
            )
            await tx.run(
                "MATCH (d:Document {id: $document_id}) "
                "UNWIND $chunk_ids AS cid "
                "MATCH (c:Chunk {id: cid}) "
                "MERGE (c)-[:BELONGS_TO]->(d)",
                document_id=str(document_id),
                chunk_ids=chunk_ids,
            )

        await self._with_retry(_do)

    async def cleanup_document(
        self,
        *,
        project_id: UUID,
        document_id: UUID,
    ) -> None:
        """Compensating delete: remove a Document and its Chunks (and edges).

        Called when ingestion fails after structural writes have committed.
        Neo4j doesn't participate in the Postgres transaction, so a Postgres
        rollback alone leaves orphan graph nodes. This method removes them.

        Entities are NOT deleted — they're project-scoped and may be referenced
        by other documents; the lost REFERENCES edges are detached automatically
        when the chunks are removed.
        """
        async def _do(tx: AsyncTransaction) -> None:
            await tx.run(
                "MATCH (d:Document {id: $document_id}) "
                "WHERE d.project_id = $project_id "
                "OPTIONAL MATCH (c:Chunk {parent_id: $document_id}) "
                "DETACH DELETE d, c",
                document_id=str(document_id),
                project_id=str(project_id),
            )

        await self._with_retry(_do)

    async def expand_chunks(
        self,
        *,
        project_id: UUID,
        seeds: list[UUID],
        cap: int = 100,
    ) -> "ExpansionSubgraph":
        """Return the seeds + 1-hop neighbors via REFERENCES and SEMANTICALLY_NEAR.

        Uses a separate-cap-per-edge-type budget because the two weight scales
        (cosine vs shared-entity count) are not comparable.
        """
        from atlas_graph.expansion import (
            EXPAND_REF_CYPHER,
            EXPAND_SN_CYPHER,
            SEEDS_PR_CYPHER,
            ExpansionSubgraph,
            merge_neighbors_with_budget,
        )

        if not seeds:
            return ExpansionSubgraph()

        seed_strs = [str(s) for s in seeds]

        async def _read(tx):
            sn_result = await tx.run(EXPAND_SN_CYPHER, seeds=seed_strs, pid=str(project_id))
            sn_rows_raw = await sn_result.data()
            ref_result = await tx.run(
                EXPAND_REF_CYPHER, seeds=seed_strs, pid=str(project_id)
            )
            ref_rows_raw = await ref_result.data()
            seed_pr_result = await tx.run(
                SEEDS_PR_CYPHER, seeds=seed_strs, pid=str(project_id)
            )
            seed_pr_raw = await seed_pr_result.data()
            return sn_rows_raw, ref_rows_raw, seed_pr_raw

        async with self._session() as s:
            sn_raw, ref_raw, seed_pr_raw = await s.execute_read(_read)

        sn_rows = [
            (UUID(r["a"]), UUID(r["b"]), float(r["w"]), float(r["pa"]), float(r["pb"]))
            for r in sn_raw
        ]
        ref_rows = [
            (UUID(r["a"]), UUID(r["b"]), float(r["w"]), float(r["pa"]), float(r["pb"]))
            for r in ref_raw
        ]
        seed_prs = {UUID(r["id"]): float(r["pr"]) for r in seed_pr_raw}

        return merge_neighbors_with_budget(seeds, sn_rows, ref_rows, seed_prs, cap)

    async def fetch_top_entities(
        self,
        *,
        project_id: UUID,
        limit: int = 30,
    ) -> tuple[list[dict], list[dict]]:
        """Top-N entities by PageRank for the project, plus edges between them.

        Returns ``(nodes, edges)``. Each node is a dict with keys
        ``id, type, label, pagerank, metadata``. Each edge has
        ``id, source, target, type``. UUIDs are returned as strings — the
        router converts them to Pydantic models.
        """
        async def _read(tx):
            node_result = await tx.run(
                TOP_ENTITIES_CYPHER, pid=str(project_id), limit=int(limit)
            )
            nodes_raw = await node_result.data()
            if not nodes_raw:
                return [], []
            ids = [r["id"] for r in nodes_raw]
            edge_result = await tx.run(TOP_ENTITIES_EDGES_CYPHER, ids=ids)
            edges_raw = await edge_result.data()
            return nodes_raw, edges_raw

        async with self._session() as s:
            nodes_raw, edges_raw = await s.execute_read(_read)

        nodes = [
            {
                "id": r["id"],
                "type": "Entity",
                "label": r["label"],
                "pagerank": float(r["pagerank"]),
                "metadata": {
                    "entity_type": r.get("entity_type"),
                    "mention_count": int(r.get("mention_count") or 0),
                },
            }
            for r in nodes_raw
        ]
        edges = [
            {
                "id": str(r["rid"]),
                "source": r["source"],
                "target": r["target"],
                "type": r["type"],
            }
            for r in edges_raw
        ]
        return nodes, edges

    async def fetch_subgraph_by_seeds(
        self,
        *,
        project_id: UUID,
        seed_ids: list[UUID],
        neighbors_per_seed: int = 25,
    ) -> tuple[list[dict], list[dict]]:
        """1-hop expansion of arbitrary nodes by id.

        Returns nodes (full dicts with id/type/label/metadata) and
        deduped edges. Per-seed neighbor cap (with PageRank-DESC ordering for
        determinism) prevents one high-degree seed from starving the others.
        """
        if not seed_ids:
            return [], []

        seed_strs = [str(s) for s in seed_ids]

        async def _read(tx):
            result = await tx.run(
                SUBGRAPH_CYPHER, seeds=seed_strs, pid=str(project_id), cap=int(neighbors_per_seed)
            )
            return await result.data()

        async with self._session() as s:
            rows = await s.execute_read(_read)

        nodes: dict[str, dict] = {}
        edges: dict[str, dict] = {}
        for row in rows:
            node_id = row["id"]
            if node_id not in nodes:
                nodes[node_id] = {
                    "id": node_id,
                    "type": row["type"],
                    "label": row["label"],
                    "pagerank": float(row["pagerank"]) if row["pagerank"] is not None else None,
                    "metadata": row["metadata"] or {},
                }
            for rel in row["rels"]:
                if rel is None or rel.get("source") is None or rel.get("target") is None:
                    continue
                rel_id = rel["id"]
                if rel_id not in edges:
                    edges[rel_id] = {
                        "id": rel_id,
                        "source": rel["source"],
                        "target": rel["target"],
                        "type": rel["type"],
                    }

        return list(nodes.values()), list(edges.values())

    async def tag_note(
        self,
        *,
        note_id: UUID,
        entity_ids: list[UUID],
    ) -> None:
        """Create (:Document {id:note_id})-[:TAGGED_WITH]->(:Entity {id:eid}) edges.

        Idempotent via MERGE; safe to re-call on every Save & Index.
        """
        if not entity_ids:
            return

        async def _do(tx: AsyncTransaction) -> None:
            await tx.run(
                TAG_NOTE_CYPHER,
                note_id=str(note_id),
                entity_ids=[str(e) for e in entity_ids],
            )

        await self._with_retry(_do)

    async def write_entities(
        self,
        *,
        project_id: UUID,
        chunks: Sequence[ChunkWithText],
    ) -> None:
        """Run NER over chunk text and MERGE Entity nodes + REFERENCES edges.

        No-op on empty chunks. Raises NerFailure if LM Studio is unreachable.
        """
        if not chunks or self._ner_extractor is None:
            return
        from atlas_graph.ingestion.entities import (
            MERGE_ENTITIES_CYPHER,
            MERGE_REFERENCES_CYPHER,
            flatten,
        )

        chunk_entities = await self._ner_extractor.extract_batch(
            [(c.id, c.text) for c in chunks]
        )
        entities, references = flatten(project_id, chunk_entities)
        if not entities:
            return

        async def _do(tx: AsyncTransaction) -> None:
            await tx.run(MERGE_ENTITIES_CYPHER, entities=entities)
            if references:
                await tx.run(MERGE_REFERENCES_CYPHER, references=references)

        await self._with_retry(_do)

    async def merge_semantic_near(
        self,
        *,
        pairs: Sequence[tuple[UUID, UUID, float]],
    ) -> None:
        """MERGE undirected SEMANTICALLY_NEAR edges with cosine on the relation.

        Caller is expected to canonicalize ``(a, b)`` so the same pair is not
        passed twice; we don't dedupe inside this method to keep it cheap.
        """
        if not pairs:
            return
        params = [
            {"a": str(a), "b": str(b), "cosine": float(score)}
            for (a, b, score) in pairs
        ]

        async def _do(tx: AsyncTransaction) -> None:
            await tx.run(
                "UNWIND $pairs AS p "
                "MATCH (x:Chunk {id: p.a}), (y:Chunk {id: p.b}) "
                "MERGE (x)-[r:SEMANTICALLY_NEAR]-(y) "
                "SET r.cosine = p.cosine",
                pairs=params,
            )

        await self._with_retry(_do)

    async def build_temporal_near(
        self,
        *,
        project_id: UUID,
        document_id: UUID,
        window_days: int,
    ) -> None:
        """MERGE undirected TEMPORAL_NEAR edges between same-project Documents within N days.

        Both endpoints must have a non-null created_at. The signed delta check
        handles both directions: new doc ingested before or after existing docs.
        """
        from atlas_graph.ingestion.temporal import TEMPORAL_NEAR_CYPHER

        async def _do(tx: AsyncTransaction) -> None:
            await tx.run(
                TEMPORAL_NEAR_CYPHER,
                project_id=str(project_id),
                document_id=str(document_id),
                window_days=int(window_days),
            )

        await self._with_retry(_do)

    async def run_pagerank(self, *, project_id: UUID) -> None:
        """Compute global PageRank on the project's subgraph and persist it.

        Naming the projection uniquely-per-call avoids collisions if two
        ingests in the same project race. The drop runs unconditionally;
        a failed write must not leak the projection.
        """
        from atlas_graph.ingestion.pagerank import (
            DROP_CYPHER,
            PROJECT_CYPHER,
            WRITE_CYPHER,
        )

        proj_name = f"proj_{str(project_id).replace('-', '')[:12]}_{int(time.time() * 1000)}"

        async def _project_and_write(tx: AsyncTransaction) -> None:
            await tx.run(PROJECT_CYPHER, name=proj_name, pid=str(project_id))
            await tx.run(WRITE_CYPHER, name=proj_name)

        async def _drop(tx: AsyncTransaction) -> None:
            await tx.run(DROP_CYPHER, name=proj_name)

        try:
            await self._with_retry(_project_and_write)
        finally:
            try:
                await self._with_retry(_drop)
            except Exception as e:  # noqa: BLE001
                log.warning("graph.pagerank.drop_failed", name=proj_name, error=str(e))

    @asynccontextmanager
    async def _session(self):
        async with self._driver.session() as s:
            yield s
