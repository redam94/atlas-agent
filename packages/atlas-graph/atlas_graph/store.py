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
