"""IngestionService — orchestrates parser → chunker → embedder → vector store + DB.

The contract: caller supplies an already-parsed document. This keeps the service
agnostic about the source format (markdown text vs PDF bytes); the API layer
chooses the parser based on content type.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4

import structlog
from atlas_core.db.orm import IngestionJobORM, KnowledgeNodeORM, ProjectORM
from sqlalchemy.ext.asyncio import AsyncSession

from atlas_knowledge.chunking.semantic import SemanticChunker
from atlas_knowledge.embeddings.service import EmbeddingService
from atlas_knowledge.ingestion.protocols import GraphWriter
from atlas_knowledge.models.nodes import KnowledgeNode, KnowledgeNodeType
from atlas_knowledge.parsers.markdown import ParsedDocument
from atlas_knowledge.vector.store import VectorStore

log = structlog.get_logger("atlas.knowledge.ingest")

_TEXT_PREVIEW_LEN = 200


@dataclass(frozen=True)
class IngestionResult:
    """What `IngestionService.ingest` returns.

    `document_id` is None only on the empty-text path where no Document row is
    created (extremely rare; happens when the parser returns an empty body).
    """
    job_id: UUID
    document_id: UUID | None


@dataclass(frozen=True)
class _ChunkSpecAdapter:
    """Duck-typed match for atlas_graph.protocols.ChunkSpec.

    atlas-knowledge does NOT import atlas-graph; we satisfy the GraphWriter
    Protocol structurally.
    """

    id: UUID
    position: int
    token_count: int
    text_preview: str

    def to_param(self) -> dict[str, object]:
        return {
            "id": str(self.id),
            "position": self.position,
            "token_count": self.token_count,
            "text_preview": self.text_preview,
        }


@dataclass(frozen=True)
class _ChunkWithTextAdapter:
    """Duck-type for atlas_graph.protocols.ChunkWithText.

    Carries the full chunk text for NER (atlas_graph reads this).
    """

    id: UUID
    text: str


_PAGERANK_STATUS_OK = "ok"
_PAGERANK_STATUS_FAILED = "failed"
_PAGERANK_STATUS_SKIPPED = "skipped"


class IngestionService:
    def __init__(
        self,
        embedder: EmbeddingService,
        vector_store: VectorStore,
        *,
        chunker: SemanticChunker | None = None,
        graph_writer: GraphWriter | None = None,
        semantic_near_threshold: float = 0.85,
        semantic_near_top_k: int = 50,
        temporal_near_window_days: int = 7,
        pagerank_enabled: bool = True,
    ) -> None:
        self._embedder = embedder
        self._vector_store = vector_store
        self._chunker = chunker or SemanticChunker(target_tokens=512, overlap_tokens=128)
        self._graph_writer = graph_writer
        self._semantic_near_threshold = semantic_near_threshold
        self._semantic_near_top_k = semantic_near_top_k
        self._temporal_near_window_days = temporal_near_window_days
        self._pagerank_enabled = pagerank_enabled

    async def ingest(
        self,
        *,
        db: AsyncSession,
        user_id: str,
        project_id: UUID,
        parsed: ParsedDocument,
        source_type: str,  # "markdown" | "pdf" | "url"
        source_filename: str | None,
    ) -> IngestionResult:
        """Run the pipeline. Returns the job_id. Always commits a job row,
        even on failure (with status='failed' + error)."""
        job = IngestionJobORM(
            user_id=user_id,
            project_id=project_id,
            source_type=source_type,
            source_filename=source_filename,
            status="running",
        )
        db.add(job)
        await db.flush()
        log.info("ingest.start", job_id=str(job.id), source=source_type)

        try:
            # 1. Persist the document node.
            doc_row: KnowledgeNodeORM | None = None
            chunk_rows: list[KnowledgeNodeORM] = []
            doc_row = KnowledgeNodeORM(
                user_id=user_id,
                project_id=project_id,
                type="document",
                title=parsed.title,
                text=parsed.text,
                metadata_={"source_type": source_type, **parsed.metadata},
            )
            db.add(doc_row)
            await db.flush()

            # 2. Chunk.
            raw_chunks = self._chunker.chunk(parsed.text)
            if not raw_chunks:
                # Empty doc: write the (:Document) node only. Plan 3 ops require
                # chunks (NER), embeddings (semantic), or are pointless on an
                # isolated doc (pagerank). Status stays "skipped".
                if self._graph_writer is not None:
                    project_row = await db.get(ProjectORM, project_id)
                    project_name = project_row.name if project_row else "Unknown"
                    await self._graph_writer.write_document_chunks(
                        project_id=project_id,
                        project_name=project_name,
                        document_id=doc_row.id,
                        document_title=doc_row.title or "Untitled",
                        document_source_type=source_type,
                        document_metadata=dict(doc_row.metadata_ or {}),
                        document_created_at=doc_row.created_at or datetime.now(UTC),
                        chunks=[],
                    )
                job.status = "completed"
                job.pagerank_status = _PAGERANK_STATUS_SKIPPED
                job.completed_at = datetime.now(UTC)
                job.node_ids = [str(doc_row.id)]
                await db.flush()
                return IngestionResult(job_id=job.id, document_id=doc_row.id)

            # 3. Persist chunk rows (so they get IDs we can use for the vector store).
            for raw in raw_chunks:
                row = KnowledgeNodeORM(
                    id=uuid4(),
                    user_id=user_id,
                    project_id=project_id,
                    type="chunk",
                    parent_id=doc_row.id,
                    title=parsed.title,
                    text=raw.text,
                    metadata_={"index": raw.index, "token_count": raw.token_count},
                )
                db.add(row)
                chunk_rows.append(row)
            await db.flush()

            # 4. Embed + push to vector store.
            embeddings = await self._embedder.embed_documents([r.text for r in chunk_rows])
            chunk_models = [
                KnowledgeNode(
                    id=r.id,
                    user_id=r.user_id,
                    project_id=r.project_id,
                    type=KnowledgeNodeType.CHUNK,
                    parent_id=r.parent_id,
                    title=r.title,
                    text=r.text,
                    metadata=dict(r.metadata_ or {}),
                    created_at=r.created_at or datetime.now(UTC),
                )
                for r in chunk_rows
            ]
            await self._vector_store.upsert(chunk_models, embeddings)

            # 5. Stamp embedding_id on each chunk row.
            for row in chunk_rows:
                row.embedding_id = str(row.id)
            await db.flush()

            # 5.5 Plan 2 — structural graph writes.
            pagerank_status = _PAGERANK_STATUS_SKIPPED
            if self._graph_writer is not None:
                project_row = await db.get(ProjectORM, project_id)
                project_name = project_row.name if project_row else "Unknown"
                doc_created_at = doc_row.created_at or datetime.now(UTC)
                chunk_specs = [
                    _ChunkSpecAdapter(
                        id=r.id,
                        position=int((r.metadata_ or {}).get("index", 0)),
                        token_count=int((r.metadata_ or {}).get("token_count", 0)),
                        text_preview=r.text[:_TEXT_PREVIEW_LEN],
                    )
                    for r in chunk_rows
                ]
                await self._graph_writer.write_document_chunks(
                    project_id=project_id,
                    project_name=project_name,
                    document_id=doc_row.id,
                    document_title=doc_row.title or "Untitled",
                    document_source_type=source_type,
                    document_metadata=dict(doc_row.metadata_ or {}),
                    document_created_at=doc_created_at,
                    chunks=chunk_specs,
                )

                # 5.6 — Plan 3 NER + entity edges (required tier).
                await self._graph_writer.write_entities(
                    project_id=project_id,
                    chunks=[
                        _ChunkWithTextAdapter(id=r.id, text=r.text)
                        for r in chunk_rows
                    ],
                )

                # 5.7 — semantic-near pairs (compute against Chroma, then write).
                pairs = await self._compute_semantic_near_pairs(
                    project_id=project_id,
                    chunk_rows=chunk_rows,
                    embeddings=embeddings,
                )
                await self._graph_writer.merge_semantic_near(pairs=pairs)

                # 5.8 — temporal-near (cheap Cypher).
                await self._graph_writer.build_temporal_near(
                    project_id=project_id,
                    document_id=doc_row.id,
                    window_days=self._temporal_near_window_days,
                )

                # 5.9 — PageRank (best-effort tier).
                if self._pagerank_enabled:
                    try:
                        await self._graph_writer.run_pagerank(project_id=project_id)
                        pagerank_status = _PAGERANK_STATUS_OK
                    except Exception:
                        log.exception("ingest.pagerank_failed", job_id=str(job.id))
                        pagerank_status = _PAGERANK_STATUS_FAILED
                # else: pagerank_status stays SKIPPED

            # 6. Mark job complete.
            job.status = "completed"
            job.pagerank_status = pagerank_status
            job.completed_at = datetime.now(UTC)
            job.node_ids = [str(doc_row.id)] + [str(r.id) for r in chunk_rows]
            await db.flush()
            log.info("ingest.complete", job_id=str(job.id), chunks=len(chunk_rows))
            return IngestionResult(job_id=job.id, document_id=doc_row.id)

        except Exception as e:
            log.exception("ingest.failed", job_id=str(job.id))
            # Roll back partial doc + chunk rows so the DB doesn't contain orphans
            # (chunks without embeddings) on failure. The job row stays so the
            # caller can see what happened.
            for row in chunk_rows:
                await db.delete(row)
            if doc_row is not None:
                await db.delete(doc_row)
            # Compensating delete on the graph: write_document_chunks may have
            # already committed Document + Chunk nodes in Neo4j (separate tx
            # from Postgres). Clean them up so the graph stays consistent.
            # Wrapped in try/except so cleanup failures don't mask the original.
            if doc_row is not None and self._graph_writer is not None:
                try:
                    await self._graph_writer.cleanup_document(
                        project_id=project_id,
                        document_id=doc_row.id,
                    )
                except Exception:
                    log.exception(
                        "ingest.graph_cleanup_failed", job_id=str(job.id),
                    )
            job.status = "failed"
            job.error = str(e)
            job.completed_at = datetime.now(UTC)
            await db.flush()
            return IngestionResult(job_id=job.id, document_id=doc_row.id if doc_row is not None else None)

    async def cleanup_document(
        self,
        *,
        db: AsyncSession,
        project_id: UUID,
        document_id: UUID,
    ) -> None:
        """Cascade delete a Document across Postgres + Chroma + Neo4j.

        - Postgres: deletes the parent KnowledgeNodeORM; chunks cascade via
          parent_id FK ON DELETE CASCADE.
        - Chroma: deletes by metadata filter project_id + parent_id.
        - Neo4j: delegates to GraphStore.cleanup_document if a graph writer
          is configured; no-op otherwise.
        """
        # Chroma — delete chunk vectors before we lose the parent_id FK.
        self._vector_store.delete_by_parent(project_id=project_id, parent_id=document_id)

        # Postgres — cascade deletes chunks via parent_id FK.
        doc_row = await db.get(KnowledgeNodeORM, document_id)
        if doc_row is not None:
            await db.delete(doc_row)
            await db.flush()

        # Neo4j — delegate.
        if self._graph_writer is not None:
            await self._graph_writer.cleanup_document(
                project_id=project_id, document_id=document_id
            )

    async def _compute_semantic_near_pairs(
        self,
        *,
        project_id: UUID,
        chunk_rows: list[KnowledgeNodeORM],
        embeddings: list[list[float]],
    ) -> list[tuple[UUID, UUID, float]]:
        """Query Chroma top-K per new chunk; return canonical (a<b) pairs above threshold."""
        if not chunk_rows:
            return []
        threshold = self._semantic_near_threshold
        top_k = self._semantic_near_top_k
        seen: set[tuple[str, str]] = set()
        out: list[tuple[UUID, UUID, float]] = []
        for chunk_row, embedding in zip(chunk_rows, embeddings, strict=True):
            scored = await self._vector_store.search(
                query_embedding=embedding,
                top_k=top_k,
                filter={"project_id": str(project_id)},
            )
            for sc in scored:
                if sc.score < threshold:
                    continue
                if sc.chunk.id == chunk_row.id:
                    continue
                a, b = sorted((str(chunk_row.id), str(sc.chunk.id)))
                if (a, b) in seen:
                    continue
                seen.add((a, b))
                out.append((UUID(a), UUID(b), float(sc.score)))
        return out
