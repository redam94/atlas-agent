"""ChromaDB-backed VectorStore (embedded mode)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import anyio.to_thread
import chromadb
from chromadb.config import Settings

from atlas_knowledge.models.nodes import KnowledgeNode, KnowledgeNodeType
from atlas_knowledge.models.retrieval import ScoredChunk
from atlas_knowledge.vector.store import VectorStore


class ChromaVectorStore(VectorStore):
    """One Chroma collection per user; project_id stored as item metadata."""

    def __init__(self, persist_dir: str, user_id: str) -> None:
        self._client = chromadb.PersistentClient(
            path=persist_dir,
            settings=Settings(anonymized_telemetry=False),
        )
        # Collection names: ``user_<user_id>`` — Chroma names must be 3–63 chars,
        # start/end alphanumeric. user_id="matt" → "user_matt" satisfies this.
        self._collection = self._client.get_or_create_collection(
            name=f"user_{user_id}",
            metadata={"hnsw:space": "cosine"},
        )

    async def upsert(
        self,
        chunks: list[KnowledgeNode],
        embeddings: list[list[float]],
    ) -> None:
        if len(chunks) != len(embeddings):
            raise ValueError(
                f"upsert length mismatch: {len(chunks)} chunks vs {len(embeddings)} embeddings"
            )
        if not chunks:
            return

        ids = [str(c.id) for c in chunks]
        documents = [c.text for c in chunks]
        metadatas = [
            {
                "project_id": str(c.project_id),
                "user_id": c.user_id,
                "parent_id": str(c.parent_id) if c.parent_id else "",
                "title": c.title or "",
                "created_at": c.created_at.isoformat(),
                **c.metadata,
            }
            for c in chunks
        ]

        def _do_upsert() -> None:
            self._collection.upsert(
                ids=ids,
                embeddings=embeddings,
                documents=documents,
                metadatas=metadatas,
            )

        await anyio.to_thread.run_sync(_do_upsert)

    async def search(
        self,
        query_embedding: list[float],
        top_k: int = 8,
        filter: dict[str, Any] | None = None,
    ) -> list[ScoredChunk]:
        where: dict[str, Any] | None = filter

        def _do_search() -> dict[str, Any]:
            return self._collection.query(
                query_embeddings=[query_embedding],
                n_results=top_k,
                where=where,
            )

        result = await anyio.to_thread.run_sync(_do_search)
        return self._scored_chunks_from_chroma(result)

    async def delete(self, ids: list[UUID]) -> None:
        if not ids:
            return

        str_ids = [str(i) for i in ids]

        def _do_delete() -> None:
            self._collection.delete(ids=str_ids)

        await anyio.to_thread.run_sync(_do_delete)

    def delete_by_parent(self, *, project_id: UUID, parent_id: UUID) -> None:
        """Delete all chunk vectors whose metadata.parent_id matches."""
        self._collection.delete(
            where={"$and": [
                {"project_id": str(project_id)},
                {"parent_id": str(parent_id)},
            ]}
        )

    @staticmethod
    def _scored_chunks_from_chroma(result: dict[str, Any]) -> list[ScoredChunk]:
        # Chroma returns parallel lists, each wrapped in a 1-element outer list
        # because we always pass exactly one query_embedding.
        if not result.get("ids") or not result["ids"][0]:
            return []
        ids = result["ids"][0]
        documents = result["documents"][0]
        metadatas = result["metadatas"][0]
        # Chroma returns "distances" with cosine = lower is closer.
        # Convert to similarity score: score = 1 - distance.
        distances = result["distances"][0]

        out: list[ScoredChunk] = []
        for chunk_id, doc, meta, dist in zip(ids, documents, metadatas, distances, strict=True):
            chunk = KnowledgeNode(
                id=UUID(chunk_id),
                user_id=meta.get("user_id", ""),
                project_id=UUID(meta["project_id"]),
                type=KnowledgeNodeType.CHUNK,
                parent_id=UUID(meta["parent_id"]) if meta.get("parent_id") else None,
                title=meta.get("title") or None,
                text=doc,
                metadata={
                    k: v
                    for k, v in meta.items()
                    if k not in {"project_id", "user_id", "parent_id", "title", "created_at"}
                },
                embedding_id=chunk_id,
                created_at=_parse_dt(meta.get("created_at")),
            )
            out.append(
                ScoredChunk(
                    chunk=chunk, score=1.0 - float(dist), parent_title=meta.get("title") or None
                )
            )
        return out


def _parse_dt(s: str | None) -> datetime:
    if not s:
        return datetime.now(UTC)
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        return datetime.now(UTC)
