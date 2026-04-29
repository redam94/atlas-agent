"""One-shot backfill: walks Phase 1 Postgres rows into Neo4j.

Idempotent via Cypher MERGE inside GraphStore.write_document_chunks.
Progress is visible via the optional progress_cb callback. A future plan
may add a (:BackfillState {key:'phase1'}) node for cross-process visibility,
but Plan 2 does not yet write one — re-running from scratch is safe and cheap.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from math import ceil
from uuid import UUID

import structlog
from atlas_core.db.orm import KnowledgeNodeORM, ProjectORM
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from atlas_graph.protocols import ChunkSpec
from atlas_graph.store import GraphStore

log = structlog.get_logger("atlas.graph.backfill")

_DEFAULT_DOCS_PER_BATCH = 50
_TEXT_PREVIEW_LEN = 200


@dataclass
class BackfillResult:
    documents: int
    chunks: int
    batches: int
    started_at: datetime
    finished_at: datetime


async def backfill_phase1(
    *,
    db: AsyncSession,
    graph: GraphStore,
    docs_per_batch: int = _DEFAULT_DOCS_PER_BATCH,
    progress_cb: Callable[[int, int], None] | None = None,
) -> BackfillResult:
    """Walk all Postgres documents/chunks and write them to Neo4j.

    Re-running this is safe (MERGE is idempotent). Progress is reported via the
    optional ``progress_cb`` callback fired at batch boundaries.
    """
    started = datetime.now(UTC)

    project_rows = (await db.execute(select(ProjectORM))).scalars().all()
    project_names: dict[UUID, str] = {p.id: p.name for p in project_rows}

    docs_q = (
        select(KnowledgeNodeORM)
        .where(KnowledgeNodeORM.type == "document")
        .order_by(KnowledgeNodeORM.created_at)
    )
    doc_rows = (await db.execute(docs_q)).scalars().all()
    total_docs = len(doc_rows)
    total_batches = ceil(total_docs / docs_per_batch) if total_docs else 0

    batches_done = 0
    chunks_total = 0

    for i, doc in enumerate(doc_rows, start=1):
        chunks_q = (
            select(KnowledgeNodeORM)
            .where(KnowledgeNodeORM.parent_id == doc.id)
            .order_by(KnowledgeNodeORM.created_at)
        )
        chunk_rows = (await db.execute(chunks_q)).scalars().all()
        specs = [
            ChunkSpec(
                id=c.id,
                position=int((c.metadata_ or {}).get("index", 0)),
                token_count=int((c.metadata_ or {}).get("token_count", 0)),
                text_preview=c.text[:_TEXT_PREVIEW_LEN],
            )
            for c in chunk_rows
        ]
        chunks_total += len(specs)

        await graph.write_document_chunks(
            project_id=doc.project_id,
            project_name=project_names.get(doc.project_id, "Unknown"),
            document_id=doc.id,
            document_title=doc.title or "Untitled",
            document_source_type=str((doc.metadata_ or {}).get("source_type", "unknown")),
            document_metadata=dict(doc.metadata_ or {}),
            document_created_at=doc.created_at or datetime.now(UTC),
            chunks=specs,
        )

        if i % docs_per_batch == 0 or i == total_docs:
            batches_done += 1
            if progress_cb:
                progress_cb(batches_done, total_batches)
            log.info(
                "graph.backfill.progress",
                batch=batches_done, total=total_batches,
                docs_done=i, chunks_done=chunks_total,
            )

    finished = datetime.now(UTC)
    log.info(
        "graph.backfill.done",
        documents=total_docs, chunks=chunks_total, batches=batches_done,
    )
    return BackfillResult(
        documents=total_docs,
        chunks=chunks_total,
        batches=batches_done,
        started_at=started,
        finished_at=finished,
    )
