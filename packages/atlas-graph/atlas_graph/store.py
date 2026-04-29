"""GraphStore — async wrapper around the neo4j driver."""
from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable, Sequence
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING
from uuid import UUID

import structlog
from neo4j.exceptions import ServiceUnavailable, TransientError

from atlas_graph.errors import GraphUnavailableError
from atlas_graph.protocols import ChunkSpec

if TYPE_CHECKING:
    from neo4j import AsyncDriver
    from neo4j._async.driver import AsyncTransaction

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

    def __init__(self, driver: AsyncDriver, *, max_retries: int = 3) -> None:
        self._driver = driver
        self._max_retries = max_retries

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
                "    d.source_type = $source_type, d.metadata = $metadata",
                id=str(document_id),
                project_id=str(project_id),
                title=document_title,
                source_type=document_source_type,
                metadata=meta,
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

    @asynccontextmanager
    async def _session(self):
        async with self._driver.session() as s:
            yield s
