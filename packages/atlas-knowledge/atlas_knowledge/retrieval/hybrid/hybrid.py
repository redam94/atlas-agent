"""HybridRetriever — orchestrates BM25 + vector + graph + rerank + PPR pipeline.

Per-stage failures are caught and recorded into ``RetrievalResult.degraded_stages``.
The only hard-fail conditions are: (a) both BM25 and vector return zero candidates
or both raise, and (b) hydration fails (no text to rerank or cite).
"""
from __future__ import annotations

import asyncio
import math
import time
from uuid import UUID

import structlog
from atlas_core.db.session import session_scope
from atlas_graph.store import GraphStore
from sqlalchemy.ext.asyncio import async_sessionmaker, AsyncSession

from atlas_knowledge.embeddings.service import EmbeddingService
from atlas_knowledge.models.nodes import KnowledgeNode, KnowledgeNodeType
from atlas_knowledge.models.retrieval import (
    RetrievalQuery,
    RetrievalResult,
    ScoredChunk,
)
from atlas_knowledge.retrieval.hybrid import bm25 as bm25_mod
from atlas_knowledge.retrieval.hybrid import expansion as expansion_mod
from atlas_knowledge.retrieval.hybrid import hydrate as hydrate_mod
from atlas_knowledge.retrieval.hybrid import pagerank as pr_mod
from atlas_knowledge.retrieval.hybrid.rerank import RerankerProtocol
from atlas_knowledge.retrieval.hybrid.rrf import merge as rrf_merge
from atlas_knowledge.vector.store import VectorStore

log = structlog.get_logger("atlas.retrieval.hybrid")


# Pipeline caps — constants per design §4. Tune from one place.
BM25_TOP_K = 20
VECTOR_TOP_K = 20
RRF_K = 60
RRF_TOP_K = 20
EXPANSION_CAP = 100
RERANK_TOP_K = 30


class HybridRetriever:
    def __init__(
        self,
        embedder: EmbeddingService,
        vector_store: VectorStore,
        graph_store: GraphStore,
        reranker: RerankerProtocol,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        self._embedder = embedder
        self._vector_store = vector_store
        self._graph_store = graph_store
        self._reranker = reranker
        self._session_factory = session_factory

    async def retrieve(self, query: RetrievalQuery) -> RetrievalResult:
        t0 = time.perf_counter()
        degraded: list[str] = []

        # Stage 1: embed the query (no fallback — embedder must work).
        embedding = await self._embedder.embed_query(query.text)

        # Stage 2: BM25 + vector in parallel.
        async with session_scope(self._session_factory) as session:
            bm25_task = self._run_bm25(session, query, degraded)
            vec_task = self._run_vector(query, embedding, degraded)
            bm25_ranks, vec_ranks = await asyncio.gather(bm25_task, vec_task)

            if not bm25_ranks and not vec_ranks:
                # Both stages produced nothing — pipeline cannot proceed.
                # If both *raised*, degraded already lists 'bm25' and 'vector';
                # If both returned empty (e.g., empty corpus), that's a no-result query.
                if "bm25" in degraded and "vector" in degraded:
                    raise RuntimeError(
                        "hybrid retrieval failed: both BM25 and vector stages errored"
                    )
                return RetrievalResult(query=query.text, chunks=[], degraded_stages=degraded)

            # Stage 3: RRF.
            rrf_input: list[list[tuple[UUID, int]]] = []
            if bm25_ranks:
                rrf_input.append(bm25_ranks)
            if vec_ranks:
                rrf_input.append(vec_ranks)
            seeds_scored = rrf_merge(rrf_input, k=RRF_K, top_k=RRF_TOP_K)
            seeds = [cid for cid, _ in seeds_scored]

            # Stage 4: graph expansion.
            subgraph = None
            try:
                subgraph = await expansion_mod.expand(
                    self._graph_store, query.project_id, seeds, cap=EXPANSION_CAP
                )
            except Exception as e:  # noqa: BLE001
                degraded.append("expansion")
                log.warning(
                    "atlas.retrieval.stage_degraded",
                    stage="expansion", error=str(e),
                )
            candidate_ids: set[UUID] = set(seeds)
            if subgraph is not None:
                candidate_ids.update(subgraph.nodes.keys())

            # Stage 5: hydrate (hard fail; no text means nothing to rerank or cite).
            chunk_texts = await hydrate_mod.hydrate(session, candidate_ids)

        if not chunk_texts:
            return RetrievalResult(query=query.text, chunks=[], degraded_stages=degraded)

        rerank_input = [
            (cid, chunk_texts[cid].text) for cid in candidate_ids if cid in chunk_texts
        ]

        # Stage 6: rerank (fallback: keep input order).
        try:
            reranked = await self._reranker.rerank(
                query.text, rerank_input, top_k=RERANK_TOP_K
            )
        except Exception as e:  # noqa: BLE001
            degraded.append("rerank")
            log.warning(
                "atlas.retrieval.stage_degraded", stage="rerank", error=str(e),
            )
            reranked = [(cid, 0.0) for cid, _ in rerank_input[:RERANK_TOP_K]]

        # Stage 7: personalized PageRank (fallback: drop the ppr factor).
        ppr: dict[UUID, float] = {}
        if subgraph is not None and subgraph.nodes:
            try:
                ppr = pr_mod.personalized(subgraph, seeds=seeds, damping=0.85)
            except Exception as e:  # noqa: BLE001
                degraded.append("personalized_pagerank")
                log.warning(
                    "atlas.retrieval.stage_degraded",
                    stage="personalized_pagerank", error=str(e),
                )
        else:
            degraded.append("personalized_pagerank")

        # Empty PPR (e.g., all seeds filtered out by personalized) → mark degraded
        # so we don't multiply every final score by 0.0.
        if not ppr and "personalized_pagerank" not in degraded:
            degraded.append("personalized_pagerank")

        # Stage 8: combine scores.
        global_pr: dict[UUID, float] = (
            subgraph.nodes if subgraph is not None else {}
        )
        scored: list[tuple[UUID, float]] = []
        ppr_active = "personalized_pagerank" not in degraded
        for cid, rerank_score in reranked:
            pg = global_pr.get(cid, 0.0)
            log_pg = math.log1p(pg)
            ppr_score = ppr.get(cid, 0.0) if ppr_active else 1.0
            final = rerank_score * log_pg * ppr_score
            # If log_pg is 0 (no global pagerank), fall back to rerank score so we don't zero out everything.
            if log_pg == 0.0:
                final = rerank_score * (ppr_score if ppr_active else 1.0)
            scored.append((cid, final))

        scored.sort(key=lambda kv: kv[1], reverse=True)
        top = scored[: query.top_k]

        chunks: list[ScoredChunk] = []
        for cid, final_score in top:
            txt = chunk_texts[cid]
            chunks.append(
                ScoredChunk(
                    chunk=KnowledgeNode(
                        id=cid,
                        user_id=txt.user_id,
                        project_id=query.project_id,
                        type=KnowledgeNodeType.CHUNK,
                        parent_id=txt.parent_id,
                        title=None,
                        text=txt.text,
                        metadata={},
                        created_at=txt.created_at,
                    ),
                    score=final_score,
                    parent_title=txt.parent_title,
                )
            )

        log.info(
            "atlas.retrieval.query",
            mode="hybrid",
            project_id=str(query.project_id),
            query_len=len(query.text),
            latency_ms=int((time.perf_counter() - t0) * 1000),
            degraded_stages=degraded,
            final_count=len(chunks),
        )
        return RetrievalResult(query=query.text, chunks=chunks, degraded_stages=degraded)

    async def _run_bm25(
        self, session: AsyncSession, query: RetrievalQuery, degraded: list[str]
    ) -> list[tuple[UUID, int]]:
        try:
            return await bm25_mod.search(
                session=session,
                project_id=query.project_id,
                query=query.text,
                top_k=BM25_TOP_K,
            )
        except Exception as e:  # noqa: BLE001
            degraded.append("bm25")
            log.warning("atlas.retrieval.stage_degraded", stage="bm25", error=str(e))
            return []

    async def _run_vector(
        self, query: RetrievalQuery, embedding: list[float], degraded: list[str]
    ) -> list[tuple[UUID, int]]:
        try:
            scored_chunks = await self._vector_store.search(
                query_embedding=embedding,
                top_k=VECTOR_TOP_K,
                filter={"project_id": str(query.project_id)},
            )
            return [
                (sc.chunk.id, idx) for idx, sc in enumerate(scored_chunks, start=1)
            ]
        except Exception as e:  # noqa: BLE001
            degraded.append("vector")
            log.warning(
                "atlas.retrieval.stage_degraded", stage="vector", error=str(e),
            )
            return []
