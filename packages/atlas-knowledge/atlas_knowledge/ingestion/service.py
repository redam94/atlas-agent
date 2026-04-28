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


class IngestionService:
    def __init__(
        self,
        embedder: EmbeddingService,
        vector_store: VectorStore,
        *,
        chunker: SemanticChunker | None = None,
        graph_writer: GraphWriter | None = None,
    ) -> None:
        self._embedder = embedder
        self._vector_store = vector_store
        self._chunker = chunker or SemanticChunker(target_tokens=512, overlap_tokens=128)
        self._graph_writer = graph_writer

    async def ingest(
        self,
        *,
        db: AsyncSession,
        user_id: str,
        project_id: UUID,
        parsed: ParsedDocument,
        source_type: str,  # "markdown" | "pdf" | "url"
        source_filename: str | None,
    ) -> UUID:
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
                # Edge case: empty document. Job completes with just the doc node.
                # NOTE: this path skips step 5.5, so the document exists in Postgres
                # but NOT in Neo4j. Plan 4/5 retrieval should treat doc-without-chunks
                # as a non-graph-indexed leaf; or this branch should call
                # graph_writer.write_document_chunks(chunks=[]) explicitly.
                job.status = "completed"
                job.completed_at = datetime.now(UTC)
                job.node_ids = [str(doc_row.id)]
                await db.flush()
                return job.id

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

            # 5.5 Write to graph if a writer is configured.
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
                    chunks=[
                        _ChunkSpecAdapter(
                            id=r.id,
                            position=int((r.metadata_ or {}).get("index", 0)),
                            token_count=int((r.metadata_ or {}).get("token_count", 0)),
                            text_preview=r.text[:_TEXT_PREVIEW_LEN],
                        )
                        for r in chunk_rows
                    ],
                )

            # 6. Mark job complete.
            job.status = "completed"
            job.completed_at = datetime.now(UTC)
            job.node_ids = [str(doc_row.id)] + [str(r.id) for r in chunk_rows]
            await db.flush()
            log.info("ingest.complete", job_id=str(job.id), chunks=len(chunk_rows))
            return job.id

        except Exception as e:
            log.exception("ingest.failed", job_id=str(job.id))
            # Roll back partial doc + chunk rows so the DB doesn't contain orphans
            # (chunks without embeddings) on failure. The job row stays so the
            # caller can see what happened.
            for row in chunk_rows:
                await db.delete(row)
            if doc_row is not None:
                await db.delete(doc_row)
            job.status = "failed"
            job.error = str(e)
            job.completed_at = datetime.now(UTC)
            await db.flush()
            return job.id
