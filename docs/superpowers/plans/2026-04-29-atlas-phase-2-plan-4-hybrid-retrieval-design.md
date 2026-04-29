# ATLAS Phase 2 — Plan 4: Hybrid Retrieval Design

**Status:** Draft · 2026-04-29
**Implements:** `docs/superpowers/specs/2026-04-27-atlas-phase-2-knowledge-graph-design.md` §5.4
**Predecessors:** Plans 1-3 merged. Web/URL ingestion, Neo4j stack, NER, entity edges, semantic edges, temporal edges, and global PageRank are all live. Chat WS still uses Phase 1's vector-only `Retriever`.

---

## 1. Purpose

Replace Phase 1's pure-vector retrieval with a graph-aware hybrid pipeline that combines BM25 (Postgres FTS), dense vector search (Chroma), 1-hop graph expansion over entity and semantic edges, cross-encoder reranking, and personalized PageRank. The chat WS gains the ability to surface chunks that share *concepts* but no keywords with the query — the missing capability that motivates Phase 2.

The plan ships hybrid as the default retrieval mode; the Phase 1 vector-only path remains available as `ATLAS_RETRIEVAL__MODE=vector`, providing a one-env-var rollback if hybrid misbehaves in production.

---

## 2. Scope

### In scope
- New `hybrid/` submodule under `atlas-knowledge` containing BM25, RRF, expansion, hydration, reranker, personalized PageRank, and the `HybridRetriever` orchestrator.
- Alembic migration `0005_chunks_fts` adding a generated `tsvector` column and a partial GIN index on `knowledge_nodes`.
- New `GraphStore.expand_chunks(...)` method returning the expanded subgraph (nodes with `pagerank_global`, edges with weights).
- API wiring: `app.state.retriever` becomes `Retriever | HybridRetriever` based on `ATLAS_RETRIEVAL__MODE`. Both implement the same async `retrieve()` contract; chat WS and `routers/knowledge.py` need no changes.
- Cross-encoder reranker (`ms-marco-MiniLM-L-6-v2`) lazy-loaded on first hybrid query, held as a process-lifetime singleton.
- Per-stage graceful degradation: any non-fatal stage failure logs a structured warning and falls back to the prior stage's output. `RetrievalResult` gains a backward-compatible `degraded_stages` field.
- Real-infra integration tests covering BM25, expansion, full pipeline, and the spec's "indirectly-connected concept" acceptance criterion.

### Out of scope (deferred)
- Knowledge Explorer UI integration (Plan 5 wires hybrid search into the graph view).
- Note editor (Plan 6).
- Per-user query metrics or Prometheus instrumentation. Structured logs are sufficient for single-user.
- Reranker model swap UI / A-B testing different cross-encoders.
- Query expansion via LLM rewriting. Out of scope for v1; the graph layer is the planned source of recall improvement.
- Caching of any pipeline output (rerank scores, PPR results). Single-user, ~200 ms is fine.

### Deviations from the Phase 2 spec

This design refines five decisions from the parent spec after closer examination during brainstorming. Each is called out where it appears:

| Spec says | This plan does | Why |
|---|---|---|
| `HybridRetriever` lives in `atlas-graph` (§3.3) | Lives in `atlas-knowledge/retrieval/hybrid/` | `atlas-graph` has zero ML deps; `atlas-knowledge` already pulls `sentence-transformers`. Keeps the graph package focused on Neo4j primitives. |
| Personalized PageRank via `gds.pageRank` (§4.5) | In-process via `python-igraph` on the expanded subgraph | Subgraph is ≤100 nodes; igraph PPR is sub-millisecond vs ~50-150 ms per query for a fresh GDS projection round-trip. |
| BM25 query via `to_tsquery` (§4.2) | `websearch_to_tsquery` | Handles raw chat input gracefully — quoted phrases, negation, no manual sanitization. |
| `tsvector` maintained via BEFORE INSERT/UPDATE trigger (§5.4) | `GENERATED ALWAYS AS (...) STORED` column | One DDL statement, no trigger function, supported since PG12. Postgres backfills on `ALTER TABLE`. |
| 1-hop expansion edge selection unspecified | REFERENCES + SEMANTICALLY_NEAR only | TEMPORAL_NEAR is too noisy for query expansion (every doc in a window becomes a neighbor); BELONGS_TO/PART_OF would pull whole documents and is already implicit via vector neighbors. |

---

## 3. Architecture

### 3.1 Package layout

```
packages/atlas-knowledge/atlas_knowledge/retrieval/
├── retriever.py          (Phase 1, unchanged — vector-only rollback path)
├── builder.py            (unchanged)
└── hybrid/
    ├── __init__.py
    ├── bm25.py           Postgres FTS via websearch_to_tsquery + ts_rank_cd
    ├── rrf.py            Reciprocal Rank Fusion (k=60)
    ├── expansion.py      1-hop walk over REFERENCES + SEMANTICALLY_NEAR
    ├── hydrate.py        Bulk-fetch chunk text + parent_title from Postgres
    ├── rerank.py         Cross-encoder, lazy-loaded, asyncio.to_thread
    ├── pagerank.py       In-process igraph personalized PageRank
    └── hybrid.py         HybridRetriever — orchestrates the pipeline
```

Dependency direction stays acyclic: `atlas-knowledge → atlas-graph → atlas-core`. `atlas-knowledge` gains a runtime dep on `atlas-graph` (new) and `python-igraph` (new); `sentence-transformers` (existing) covers the cross-encoder.

### 3.2 Retriever protocol

`HybridRetriever` and the existing Phase 1 `Retriever` both implement the same shape:

```python
class RetrieverProtocol(Protocol):
    async def retrieve(self, query: RetrievalQuery) -> RetrievalResult: ...
```

Defined in `atlas_knowledge.retrieval` so that `apps/api/atlas_api/deps.py:get_retriever` and the WS / HTTP call sites type-check against the abstract interface. The `app.state.retriever` lifespan binding picks the implementation based on `ATLAS_RETRIEVAL__MODE`.

### 3.3 New `GraphStore.expand_chunks` contract

```python
@dataclass
class ExpansionSubgraph:
    nodes: dict[UUID, float]                  # chunk_id → pagerank_global (0.0 if null)
    edges: list[tuple[UUID, UUID, float]]     # (a, b, weight); undirected

async def expand_chunks(
    self,
    *,
    project_id: UUID,
    seeds: list[UUID],
    cap: int = 100,
) -> ExpansionSubgraph
```

Single Cypher pass returns the union of:

- The seeds themselves (always retained).
- 1-hop SEMANTICALLY_NEAR neighbors with `weight = r.cosine`.
- 1-hop REFERENCES-via-Entity neighbors with `weight = COUNT(DISTINCT e)` (shared-entity count). Co-referencing two rare entities beats co-referencing one common entity, which matters when high-degree entities like "client" or "AI" would otherwise dominate the truncation cutoff.

**Truncation strategy.** REFERENCES weights are shared-entity *counts* (int ≥ 1) and SEMANTICALLY_NEAR weights are *cosines* (float ~0.7-0.95). The two scales aren't comparable, so cap separately rather than sort-and-truncate a mixed list:

1. Seeds always retained.
2. Remaining budget split evenly between the two edge types: up to `(cap - len(seeds)) // 2` REFERENCES neighbors (descending shared-entity count) and the same for SEMANTICALLY_NEAR neighbors (descending cosine).
3. If one side has fewer candidates than its allocation, the surplus rolls over to the other side until total node count = `cap` or both sides are exhausted.
4. Edges retained only between surviving nodes; weights preserved for use by the personalized PageRank stage.

---

## 4. Data flow per query

```
1. EMBED          embed_query(text)                        [reused from Phase 1]
2. BM25           websearch_to_tsquery + ts_rank_cd        → 20 (chunk_id, rank)
   VECTOR         Chroma cosine search                     → 20 (chunk_id, rank)
   (parallel via asyncio.gather)
3. RRF            merge by 1/(k + rank), k=60              → top-20 seeds
4. EXPAND         GraphStore.expand_chunks(seeds,          → ≤100 candidate chunk_ids
                    cap=100)                                  + subgraph + pagerank_global
5. HYDRATE        SELECT id, text, title FROM              → ChunkText[] (one round-trip)
                  knowledge_nodes WHERE id = ANY($1)
6. RERANK         cross-encoder.predict([(q, chunk)])      → 30 (chunk_id, rerank_score)
                  via asyncio.to_thread
7. PERSONAL PR    igraph.personalized_pagerank(            → ppr[chunk_id]
                    subgraph, reset_vertices=seeds)
8. SCORE          final = rerank_score                     → ScoredChunk[]
                          · log(1 + pagerank_global)
                          · ppr_personalized
9. TOP-K          sort by final, take query.top_k=8        → RetrievalResult
```

Stage 2 is the only parallel point. Stages 3-9 are sequential because each consumes the prior. Candidate IDs from stages 2-4 are deduped into a `set[UUID]` before stage 5 so hydration and rerank each run once on the union.

**Pipeline caps (constants in `hybrid.py`):**
- `BM25_TOP_K = 20`
- `VECTOR_TOP_K = 20`
- `RRF_K = 60`
- `RRF_TOP_K = 20`
- `EXPANSION_CAP = 100`
- `RERANK_TOP_K = 30`
- Final `top_k` configurable via existing `RetrievalQuery.top_k` (default 8).

**Latency budget** (rough, single-user, warm reranker):
- embed 30 ms · BM25+vector 50 ms (parallel) · RRF <1 ms · expand 30-50 ms · hydrate 10 ms · rerank 80-150 ms · PPR <5 ms · score+sort <1 ms ≈ **~200-300 ms** total. Phase 1 vector-only is ~80 ms.

---

## 5. Component contracts

### 5.1 `bm25.py`

```python
async def search(
    session: AsyncSession,
    project_id: UUID,
    query: str,
    top_k: int = 20,
) -> list[tuple[UUID, int]]    # (chunk_id, rank-position 1..top_k)
```

SQL:
```sql
SELECT id
FROM knowledge_nodes
WHERE type = 'chunk'
  AND project_id = $1
  AND fts @@ websearch_to_tsquery('english', $2)
ORDER BY ts_rank_cd(fts, websearch_to_tsquery('english', $2)) DESC
LIMIT $3;
```

Returns rank positions, not raw scores, so RRF can merge cleanly across heterogeneous rankings. Empty query (after `websearch_to_tsquery`) returns `[]` — caller handles as "BM25 returned nothing", not as an error.

### 5.2 `rrf.py`

```python
def merge(
    rankings: list[list[tuple[UUID, int]]],
    k: int = 60,
    top_k: int = 20,
) -> list[tuple[UUID, float]]    # (chunk_id, rrf_score)
```

Pure function. `score(id) = Σ 1/(k + rank_i)` over all rankings containing `id`. Sort descending, truncate to `top_k`. Empty input → `[]`.

### 5.3 `expansion.py`

```python
async def expand(
    graph_store: GraphStore,
    project_id: UUID,
    seeds: list[UUID],
    cap: int = 100,
) -> ExpansionSubgraph
```

Thin wrapper around `GraphStore.expand_chunks`. Lives in this module rather than the graph package so the orchestration stays in atlas-knowledge.

### 5.4 `hydrate.py`

```python
@dataclass
class ChunkText:
    id: UUID
    text: str
    parent_id: UUID
    parent_title: str | None

async def hydrate(
    session: AsyncSession,
    chunk_ids: Iterable[UUID],
) -> dict[UUID, ChunkText]
```

Single SELECT with a self-JOIN on `knowledge_nodes` for the document title:

```sql
SELECT c.id, c.text, c.parent_id, d.title
FROM knowledge_nodes c
LEFT JOIN knowledge_nodes d ON d.id = c.parent_id
WHERE c.id = ANY($1) AND c.type = 'chunk';
```

### 5.5 `rerank.py`

```python
class Reranker:
    def __init__(self, model_name: str): ...

    async def rerank(
        self,
        query: str,
        candidates: list[tuple[UUID, str]],
        top_k: int = 30,
    ) -> list[tuple[UUID, float]]
```

- Lazy-loads `sentence_transformers.CrossEncoder(model_name)` on first call. Held as a process-lifetime singleton bound to `app.state.reranker` and constructed in the API lifespan.
- Runs `model.predict([(q, text), ...])` in `asyncio.to_thread` to keep the event loop responsive.
- Truncates `candidates` to `top_k` if longer (rerank cost is O(n)).
- The cross-encoder's tokenizer silently truncates inputs longer than 512 tokens; ATLAS chunks target ≤512 by design so this is a non-issue, but the test suite should include a synthetic chunk near the boundary to confirm.

### 5.6 `pagerank.py`

```python
def personalized(
    subgraph: ExpansionSubgraph,
    seeds: list[UUID],
    damping: float = 0.85,
) -> dict[UUID, float]
```

Pure function. Constructs an `igraph.Graph` from `subgraph.nodes` (vertices) and `subgraph.edges` (undirected weighted). Runs `g.personalized_pagerank(reset_vertices=seed_indices, damping=damping, weights=...)`. Returns chunk_id → score mapping; scores normalize to sum = 1.0. Empty subgraph or empty seeds → `{}`.

### 5.7 `hybrid.py`

```python
class HybridRetriever:
    def __init__(
        self,
        embedder: EmbeddingService,
        vector_store: VectorStore,
        graph_store: GraphStore,
        reranker: Reranker,
        session_factory: async_sessionmaker[AsyncSession],
    ): ...

    async def retrieve(self, query: RetrievalQuery) -> RetrievalResult
```

Implements §4 flow. Each stage wrapped in try/except per §6 degradation policy. Returns `RetrievalResult` with `chunks: list[ScoredChunk]` (sorted by final score) plus a `degraded_stages: list[str]` field.

---

## 6. Error handling and degradation

Per-stage policy:

| Stage | Failure mode | Fallback |
|---|---|---|
| BM25 | Postgres error / unparseable query | Skip stage; vector-only candidates |
| Vector | Chroma timeout / unavailable | Skip stage; BM25-only candidates |
| Both BM25 + vector | Hard failure | Raise; chat WS surfaces "retrieval failed" (same behavior as Phase 1 on Chroma outage) |
| RRF | Pure function — cannot fail | n/a |
| Expansion | Neo4j unavailable / Cypher error / timeout | Skip; rerank on RRF candidates only |
| Hydrate | Postgres error | Hard fail (no text → nothing to rerank or cite) |
| Rerank | Model load fails / predict error | Use RRF/expansion order; log so we see it |
| Personalized PR | igraph error / empty subgraph | Drop the `· ppr` factor; final = `rerank_score · log(1 + pagerank_global)` |

All fallbacks log a structured warning at `atlas.retrieval.stage_degraded` with `stage`, `error`, `query_id`. Failures are also accumulated into `RetrievalResult.degraded_stages: list[str]` (default `[]`).

The chat WS forwards `degraded_stages` in the existing `rag.context` event; the field is additive (defaults to `[]`) so the Phase 1 `Retriever` returns a valid `RetrievalResult` without modification.

**Logging contract:**
- `atlas.retrieval.query` — INFO, once per query: `mode=hybrid|vector`, `project_id`, `query_len`, `latency_ms`, `degraded_stages`, `final_count`.
- `atlas.retrieval.stage` — DEBUG, per stage: `stage`, `latency_ms`, `result_count`.
- `atlas.retrieval.stage_degraded` — WARNING, only on fallback.

**Rollback:** set `ATLAS_RETRIEVAL__MODE=vector` in `.env` and restart the API. The lifespan binding picks `Retriever` (Phase 1) instead of `HybridRetriever`. No code change required.

---

## 7. Database changes

### 7.1 Alembic migration `0005_chunks_fts`

```sql
ALTER TABLE knowledge_nodes
  ADD COLUMN fts tsvector
  GENERATED ALWAYS AS (to_tsvector('english', coalesce(text, ''))) STORED;

-- Partial GIN index — every BM25 query filters type='chunk', so the partial
-- covers them and avoids the write cost of a redundant full index.
CREATE INDEX knowledge_nodes_fts_chunk_idx
  ON knowledge_nodes USING GIN (fts)
  WHERE type = 'chunk';
```

`GENERATED ... STORED` populates the column for all existing rows during the `ALTER TABLE`. No separate backfill step.

**Downgrade:**
```sql
DROP INDEX knowledge_nodes_fts_chunk_idx;
ALTER TABLE knowledge_nodes DROP COLUMN fts;
```

### 7.2 No Neo4j migrations

Plan 3 already populates REFERENCES, SEMANTICALLY_NEAR, and `pagerank_global` properties.

### 7.3 No Chroma changes

### 7.4 Config additions (`packages/atlas-core/atlas_core/config.py`)

```python
class RetrievalSettings(BaseModel):
    mode: Literal["vector", "hybrid"] = "hybrid"
    reranker_model: str = "cross-encoder/ms-marco-MiniLM-L-6-v2"
```

`ATLAS_RETRIEVAL__MODE` defaults to `hybrid` after Plan 4 lands. `ATLAS_RETRIEVAL__RERANKER_MODEL` overridable for tests / air-gapped environments.

### 7.5 Package dep changes

- `packages/atlas-knowledge/pyproject.toml`: add `python-igraph>=0.11`, add `atlas-graph` (workspace dep).
- No changes to `atlas-graph`, `atlas-core`, or the API service.

---

## 8. Testing strategy

### 8.1 Unit tests (fast, no infra)

- `test_rrf.py` — pure function. Single-list, two-list, empty-list, identical-rankings, ties.
- `test_pagerank.py` — `personalized()` on a fixed 5-node toy subgraph; assert seeds outweigh non-seeds, scores ≈ sum to 1.0, empty subgraph returns `{}`.
- `test_rerank.py` — `FakeReranker` that returns deterministic scores. Real model is not loaded in unit tests (CI speed).
- `test_hydrate.py` — fixture-loaded chunks in test Postgres; assert `dict[UUID, ChunkText]` round-trip and missing-id handling.

### 8.2 Integration tests (real Postgres + Neo4j, FakeReranker)

- `test_bm25_real_postgres.py` — runs migration `0005`, inserts chunks, asserts `websearch_to_tsquery` returns expected ranks for keyword + quoted-phrase + negation queries; asserts the partial GIN index is used (`EXPLAIN`).
- `test_expansion_real_neo4j.py` — uses Plan 3's existing real-Neo4j fixture; seeds chunks linked via REFERENCES + SEMANTICALLY_NEAR; asserts `cap=100` honored, per-edge-type budget split honored (oversupply on one side rolls over), `weight = COUNT(DISTINCT e)` correct for REFERENCES, `weight = cosine` correct for SN, isolated chunks return only themselves.
- `test_hybrid_pipeline.py` — full pipeline against Postgres + Neo4j with `FakeReranker`. Happy-path asserts `degraded_stages == []`. Failure-injection variants per stage assert each fallback (e.g., point GraphStore at an invalid URL → `expansion` ∈ `degraded_stages` and result still returned).

### 8.3 Acceptance test — spec Definition of Done

- `test_indirect_concept.py` — fixture loads two documents that share Entity nodes via Plan 3's NER but no surface keywords. Query against vector-only `Retriever` returns one. Query against `HybridRetriever` returns both. Proves the graph layer added retrievable recall.

### 8.4 Reranker smoke (opt-in, slow)

- `test_real_reranker.py` — pytest-marked `slow`. Loads the actual `ms-marco-MiniLM-L-6-v2`, reranks 10 candidates including one near the 512-token tokenizer limit. Verifies model name resolves on HF Hub and the prediction shape matches `rerank.py`'s expectations. Skipped by default.

### 8.5 Existing tests touched

- `apps/api/atlas_api/tests/test_ws_chat_rag.py` — extend to also exercise `mode=hybrid` via parametrize.
- `apps/api/atlas_api/tests/test_knowledge_router.py` — same.
- `apps/api/atlas_api/tests/test_ws_chat.py` — unchanged.

---

## 9. Definition of Done

1. Migration `0005_chunks_fts` runs cleanly on a Plan-3 Postgres database; downgrade works.
2. `HybridRetriever.retrieve()` returns within ~300 ms on a small test dataset with the warm reranker.
3. The acceptance test (`test_indirect_concept.py`) passes: hybrid surfaces a chunk that vector-only misses.
4. The cross-encoder reranker visibly reorders results when fed candidates whose RRF and rerank orderings disagree (asserted in `test_hybrid_pipeline.py`).
5. `ATLAS_RETRIEVAL__MODE=vector` rollback works: lifespan binds Phase 1 `Retriever`, chat WS works unchanged, no `degraded_stages` appears (since the field defaults to `[]` for the vector path).
6. Each per-stage failure mode is covered by an integration test that asserts the right stage name in `degraded_stages` and the result quality remains acceptable.
7. All Phase 1 and Phase 2 Plan 1-3 tests still pass.

---

## 10. Open risks

- **Reranker download on first query.** ~23 MB from HF Hub adds ~300 ms latency to the first hybrid query in a fresh container. Mitigation: pre-warm in the API lifespan if it bites; keep lazy for now per the existing Phase 1 pattern.
- **High-degree entities skewing expansion.** The shared-entity-count weighting reduces this, but a query whose seeds all reference one ubiquitous Entity ("client", "consulting") could still cap=100-truncate to a near-arbitrary slice. If observed, follow-up work could move to Jaccard or IDF-weighted expansion.
- **igraph build time.** Building a Graph from a 100-node Python `dict` happens per query. Measured to be sub-millisecond at this scale; flagged so we don't accidentally regress.
- **Plan 5 dependency.** Knowledge Explorer's hybrid-search bar (Plan 5 §5.5 of parent spec) reuses `HybridRetriever`. Plan 5 will need a thin HTTP wrapper around `retrieve()`; we should confirm the contract is reusable as Plan 5 starts.
