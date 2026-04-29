# ATLAS Phase 2 — Plan 3: NER + Entity Edges + PageRank — Design

**Status:** Draft · 2026-04-28
**Implements:** `docs/superpowers/specs/2026-04-27-atlas-phase-2-knowledge-graph-design.md` §5.3 and §4.8
**Predecessors:** Plan 1 (PR #7, web ingestion), Plan 2 (PR #9, Neo4j + structural graph writes) merged.

---

## 1. Purpose

Plan 2 wired Neo4j into the ingestion pipeline. Every ingest now writes `(:Document)`, `(:Chunk)`, `(:Project)` nodes and the `BELONGS_TO` / `PART_OF` structural edges. The graph is being written but is otherwise dead weight — nothing reads it, no semantic content beyond document structure.

Plan 3 fills the graph with the relationships that make graph-aware retrieval (Plan 4) actually pay off:

- **Entity nodes** extracted from chunk text via NER, with a domain-specific type vocabulary tuned for solo-consultant work.
- **`REFERENCES`** edges from each Chunk to the Entities it mentions.
- **`SEMANTICALLY_NEAR`** edges between Chunks whose embeddings exceed a cosine threshold.
- **`TEMPORAL_NEAR`** edges between Documents ingested within a rolling 7-day window.
- **Global PageRank** computed per-project after every ingestion, persisted as `pagerank_global` on every node.

After Plan 3 lands, no user-visible behavior changes — chat WS still uses the Phase 1 vector retriever. The graph is enriched and ready to be queried by Plan 4's hybrid retriever.

---

## 2. Scope

### In scope
- LM Studio-backed NER pipeline producing entities of 11 types: `CLIENT`, `METHOD`, `METRIC`, `TOOL`, `PERSON`, `ORG`, `LOCATION`, `TIME_PERIOD`, `INDUSTRY`, `CONTACT_INFO`, `DATA_SOURCE`.
- Entity write path: `MERGE (:Entity {project_id, name, type})` (project-scoped — same name in two projects = two nodes), `MERGE (chunk)-[:REFERENCES]->(entity)`.
- `SEMANTICALLY_NEAR` builder using Chroma top-K query per new chunk, threshold `cosine ≥ 0.85`, undirected MERGE with cosine score on the edge.
- `TEMPORAL_NEAR` builder using a Cypher predicate over `Document.created_at` within a rolling 7-day window, undirected MERGE.
- Global PageRank via `gds.graph.project.cypher` + `gds.pageRank.write` + `gds.graph.drop`, scoped to one project per projection.
- Schema migration `002_entities_and_edges.cypher` adding the Entity uniqueness constraint and indexes.
- New `GraphWriter` Protocol method `write_entities`; the semantic / temporal / pagerank methods are added to `GraphStore` and called via the same writer reference (richer interface, single Protocol).
- New `AtlasConfig.graph` flags: `NER_ENABLED`, `NER_MAX_ENTITIES_PER_CHUNK`, `SEMANTIC_NEAR_THRESHOLD`, `SEMANTIC_NEAR_TOP_K`, `TEMPORAL_NEAR_WINDOW_DAYS`, `PAGERANK_ENABLED`.
- New `IngestionJob` field `pagerank_status` (`ok` | `failed` | `skipped`).
- Tests covering NER mocking, entity MERGE idempotency, edge construction, PageRank lifecycle, end-to-end real-Neo4j integration.

### Out of scope (deferred)
- Cross-project entity disambiguation / merging (entities stay project-scoped).
- Coreference resolution ("the company" → "CircleK").
- Backfill of Phase 1 docs into entities/edges. Plan 2's structural backfill remains; entity backfill is a future optional CLI if it ever matters.
- Reading entities at retrieval time — that is Plan 4's hybrid expansion + reranker.
- Entity editing in any UI (Plan 5/6 surface notes and the explorer; entity correction by hand is post-Phase 2).
- Multi-model NER fallback. LM Studio is the single backend; if it is down, ingestion fails per the tiered policy.

---

## 3. Cross-cutting decisions

### 3.1 NER backend: LM Studio (gemma)

LM Studio at `${ATLAS_LLM__LMSTUDIO_BASE_URL}` (Matt's existing local server at `100.91.155.118:1234`). OpenAI-compatible chat completions endpoint with `response_format: json_schema` for structured output. The served model is whatever Matt has loaded — Plan 3 does not pin a model name; the prompt and schema are the only things the code controls.

Why LM Studio and not Anthropic Claude:
- Cost: zero per ingest. Anthropic would be ~$0.006/doc, ~$0.60 per 100 docs — negligible but unnecessary for a background extraction job.
- Local-first: matches the existing pattern (Phase 1 embeddings already use LM Studio when available).
- Quality: gemma-class models handle entity extraction with structured JSON output well enough for an 11-type schema. Edge cases (missed methodology mentions, type confusion) are tolerable for a graph that backs ranked retrieval — the cross-encoder reranker in Plan 4 has the final word.
- Reserve Anthropic for the chat itself, where reasoning quality has direct user-visible impact.

### 3.2 Entity types

The 11-type vocabulary was chosen against the consulting use case explicitly:

| Type | Examples | Notes |
|---|---|---|
| `CLIENT` | "CircleK", "Wendy's" | Companies you work with or about. Project-scoped means the same client in two projects = two nodes. |
| `METHOD` | "geo lift", "MMM", "incrementality testing" | Methodologies, frameworks, techniques. The highest-value type — vector retrieval misses these because methodology references rarely share keywords. |
| `METRIC` | "CAC", "ROAS", "LTV", "iROAS" | KPIs, financial measures. |
| `TOOL` | "GA4", "Snowflake", "dbt" | Software, platforms, vendors. |
| `PERSON` | individuals | Speakers in transcripts, paper authors, contacts. |
| `ORG` | non-client orgs | Vendors, agencies, regulators, research bodies. |
| `LOCATION` | "EMEA", "California" | Geographic context. |
| `TIME_PERIOD` | "Q3 2025", "2024 holiday season" | Named time windows. |
| `INDUSTRY` | "QSR", "DTC retail" | Sector context. |
| `CONTACT_INFO` | emails, phone numbers, addresses | Useful for the contacts/CRM surface area. |
| `DATA_SOURCE` | "Nielsen panel", "Census 2020" | Datasets, public data sources, third-party panels. |

**Known overlap:** `TOOL` vs `DATA_SOURCE` (Snowflake-as-tool vs Snowflake-as-data-source). The prompt instructs the LLM to disambiguate by surface context — if the surrounding text talks about querying or warehousing, `TOOL`; if it talks about source records or panels, `DATA_SOURCE`. Misclassifications here are not catastrophic; the entity itself still exists and is reachable via `REFERENCES`.

### 3.3 NER lives in atlas-graph; called via separate Protocol method

Plan 2 set up a clean Protocol-based decoupling: `atlas-knowledge.IngestionService` calls `graph_writer.write_document_chunks(...)` after Chroma upsert. atlas-knowledge does not import atlas-graph; the type relationship is structural.

Plan 3 follows the same pattern. NER pipeline code lives in `atlas_graph/ingestion/ner.py` (per spec §3.3). The `GraphWriter` Protocol gains a second method:

```python
async def write_entities(
    self, *,
    project_id: UUID,
    chunks: Sequence[ChunkWithText],   # structural type: id: UUID; text: str
) -> None: ...
```

The `IngestionService` calls `write_document_chunks` (structural) and `write_entities` (NER + entity write) as two sequential calls in two transactions. `build_semantic_near`, `build_temporal_near`, `run_pagerank` are added to `GraphStore` and called by `IngestionService` via the same writer reference — the writer structurally satisfies a richer interface that includes them. One Protocol, more methods.

We **do not** parallelize NER with embedding via `asyncio.gather` (which spec §5.3 hints at). The single-user latency budget is fine without it (saves ~1 s on a 5–8 s ingest), and keeping `IngestionService.ingest` linear is worth the simplicity.

### 3.4 Failure handling: tiered

Three ingestion-time operations can fail independently in Plan 3: NER call to LM Studio, entity write transaction, PageRank computation.

- **Required (any failure aborts the job, rolls back Postgres doc + chunk rows):** `write_document_chunks`, `write_entities`, `build_semantic_near`, `build_temporal_near`. NER is the actual product feature — silently skipping it would leave the user with no signal that their graph is incomplete. If LM Studio is down, the user wants to know.
- **Best-effort (failure logs but does not abort):** `run_pagerank`. PageRank is recomputable from graph state at any time (a future reconcile job can sweep `pagerank_status='failed'` rows). A missed run is harmless to retrieval quality — the existing scores from prior ingests stay; new nodes just default to a `pagerank_global` of 0 until the next successful run.

The `IngestionJob` ORM gains a `pagerank_status: str` column with values `ok` | `failed` | `skipped` (skipped when `PAGERANK_ENABLED=false`). Alembic migration `0004_add_pagerank_status.py`.

### 3.5 Edge mechanics

**SEMANTICALLY_NEAR** — for each new chunk, query Chroma with the chunk's embedding, `top_k = SEMANTIC_NEAR_TOP_K (50)`, filter to the same `project_id`, exclude the chunk itself, threshold `cosine ≥ SEMANTIC_NEAR_THRESHOLD (0.85)`. Edges are undirected: we canonicalize the endpoint pair by sorting `(chunk_id_a, chunk_id_b)` lexicographically before MERGE, so we never double-create. The cosine score is stored as an edge property for later inspection and Plan 4 tie-breaking. Cost: ~10 ms per chunk via Chroma's HNSW; trivial.

**TEMPORAL_NEAR** — one Cypher per ingestion using Neo4j's `duration.between` against `Document.created_at`:

```cypher
MATCH (d_new:Document {id: $new_doc_id}), (d:Document)
WHERE d.project_id = $project_id
  AND d.id <> d_new.id
  AND duration.between(datetime(d.created_at), datetime(d_new.created_at)).days
      <= $window_days
MERGE (d_new)-[:TEMPORAL_NEAR]-(d)
```

`Document.created_at` is not currently set by Plan 2's `write_document_chunks` (the `(:Document)` node only has `title`, `source_type`, `metadata`). Plan 3 updates that method to also set `created_at`, and the migration `002_entities_and_edges.cypher` backfills `created_at` on existing Document nodes from the corresponding Postgres `KnowledgeNode.created_at`.

**Direction:** both `SEMANTICALLY_NEAR` and `TEMPORAL_NEAR` are stored as undirected. Cypher's `MERGE (a)-[:R]-(b)` matches edges in either direction. Combined with the canonical id-ordering, we never double-create.

### 3.6 PageRank scope

`gds.pageRank.write` requires a graph projection. We use `gds.graph.project.cypher`, scoped per-project:

```python
projection_name = f"proj_{project_id_short}_{epoch_ms}"
node_query = "MATCH (n) WHERE n.project_id = $pid RETURN id(n) AS id"
rel_query  = "MATCH (a)-[r]-(b) WHERE a.project_id = $pid AND b.project_id = $pid RETURN id(a) AS source, id(b) AS target"
gds.graph.project.cypher(projection_name, node_query, rel_query, {"pid": str(project_id)})
gds.pageRank.write(projection_name, {writeProperty: "pagerank_global"})
gds.graph.drop(projection_name)
```

Projection name includes a millisecond timestamp suffix so concurrent ingests in the same project (rare but possible) don't collide on the named projection. The drop is in a `finally` so a write failure still cleans up.

For Matt's use, project sizes likely 100–5000 chunks. `gds.pageRank.write` on that subgraph completes in < 500 ms. No debounce or threshold needed; `PAGERANK_ENABLED=false` is the cost knob if it ever matters.

### 3.7 Entity-per-chunk cap

`NER_MAX_ENTITIES_PER_CHUNK = 20`. If the LLM returns more, we keep the first 20 in JSON-output order (the prompt asks for confidence-ordered output, but we don't trust ranking — we trust the cap). Above 20 the model is reliably hallucinating filler; below 20 we keep all of them.

---

## 4. Data flow

### 4.1 Updated `IngestionService.ingest()`

```
1. Persist Document row (Postgres)
2. Chunk text
3. Persist Chunk rows (Postgres) — get IDs
4. Embed + Chroma upsert
5. Stamp embedding_id on chunk rows
6. graph_writer.write_document_chunks(...)        # Plan 2 — required, also sets Document.created_at
7. graph_writer.write_entities(chunks=...)        # NEW — required (LM Studio down → abort)
8. graph_writer.build_semantic_near(...)          # NEW — required (Chroma down → abort)
9. graph_writer.build_temporal_near(...)          # NEW — required (cheap Cypher)
10. graph_writer.run_pagerank(project_id=...)     # NEW — best-effort; try/except sets pagerank_status
11. Mark job completed
```

Steps 6–9 share the existing try/except that rolls back Postgres doc + chunk rows. Step 10 has its own try/except that logs `pagerank.failed` and sets `job.pagerank_status = "failed"` without re-raising.

If `NER_ENABLED=false`: skip steps 7–10 entirely; job completes with `pagerank_status="skipped"`. Useful for ingest-heavy bulk imports where the user wants speed and will rebuild entities later.
If `PAGERANK_ENABLED=false`: skip step 10 only; `pagerank_status="skipped"`.

### 4.2 LM Studio NER call internals (step 7)

`NerExtractor.extract_batch(chunks: Sequence[ChunkWithText])`:

1. Build N HTTP requests (one per chunk) to LM Studio's `/v1/chat/completions` with:
   - System prompt: "Extract entities of these types: …" with the 11-type vocabulary and short rules per type (esp. TOOL vs DATA_SOURCE).
   - User message: chunk text.
   - `response_format: {type: "json_schema", json_schema: {…}}` enforcing `{entities: [{name: str, type: enum<11>}, ...]}`.
2. `asyncio.gather` all calls with a per-call timeout of 30 s. One retry on timeout / 5xx / malformed JSON; second failure raises.
3. JSON-parse each response. Apply the 20-cap. Drop entries with empty `name` or unknown `type`.
4. Return `dict[chunk_id, list[Entity(name, type)]]`.

Then `write_entities`:
1. Open one write transaction.
2. `UNWIND $entities AS row MERGE (e:Entity {project_id: row.project_id, name: row.name, type: row.type})`.
3. `UNWIND $references AS ref MATCH (c:Chunk {id: ref.chunk_id}), (e:Entity {project_id: ref.project_id, name: ref.name, type: ref.type}) MERGE (c)-[:REFERENCES]->(e)`.

All idempotent via `MERGE`. Re-running on the same input is a no-op.

### 4.3 Schema migration `002_entities_and_edges.cypher`

```cypher
CREATE CONSTRAINT entity_project_name_type IF NOT EXISTS
  FOR (e:Entity) REQUIRE (e.project_id, e.name, e.type) IS UNIQUE;
CREATE INDEX entity_project_id IF NOT EXISTS FOR (e:Entity) ON (e.project_id);
CREATE INDEX entity_type IF NOT EXISTS FOR (e:Entity) ON (e.type);
```

Plus a one-time backfill of `Document.created_at` for nodes missing it (existing Plan 2 docs):
```cypher
// Backfill created_at on existing Document nodes from Postgres-side data.
// This runs as a Python step in the migration runner (not Cypher), reading
// from KnowledgeNodeORM and updating Document nodes by id. Lives in
// atlas_graph/schema/migrations/002_backfill_document_created_at.py.
```

We split the migration in two: the Cypher constraints + indexes file, and a Python script for the timestamp backfill that the migration runner executes after the Cypher (the runner already supports applied-id tracking; it gains a tiny dispatch on file extension).

### 4.4 Component diagram

```
┌──────────────────────────── apps/api ─────────────────────────────┐
│  IngestionService.ingest()                                        │
│   ├── chunker, embedder, vector_store    (atlas-knowledge)        │
│   └── graph_writer: GraphWriter Protocol                          │
│         │                                                          │
│         ▼                                                          │
│  ┌──────── atlas-graph ────────────────────────────────────────┐  │
│  │  GraphStore (implements GraphWriter)                        │  │
│  │   ├── write_document_chunks()        Plan 2                 │  │
│  │   ├── write_entities()               NEW — uses NerExtractor│  │
│  │   ├── build_semantic_near()          NEW — uses Chroma      │  │
│  │   ├── build_temporal_near()          NEW                    │  │
│  │   └── run_pagerank()                 NEW                    │  │
│  │                                                             │  │
│  │  ingestion/                                                 │  │
│  │   ├── ner.py        NerExtractor (LM Studio client)         │  │
│  │   ├── entities.py   Entity dataclass + Cypher helpers       │  │
│  │   ├── semantic.py   Chroma top-K + canonical pair ordering  │  │
│  │   ├── temporal.py   Cypher TEMPORAL_NEAR predicate          │  │
│  │   └── pagerank.py   project + write + drop                  │  │
│  │                                                             │  │
│  │  schema/migrations/                                         │  │
│  │   ├── 001_initial_schema.cypher          (Plan 2)           │  │
│  │   ├── 002_entities_and_edges.cypher      (Plan 3)           │  │
│  │   └── 002_backfill_document_created_at.py (Plan 3)          │  │
│  └─────────────────────────────────────────────────────────────┘  │
└────────────────────────────────────────────────────────────────────┘
```

`atlas-knowledge` continues to import nothing from `atlas-graph`. The relationship is structural; tests in `atlas-knowledge` use a fake graph_writer.

---

## 5. Configuration

New keys under `AtlasConfig.graph`:

| Key | Type | Default | Notes |
|---|---|---|---|
| `NER_ENABLED` | bool | `True` | Master kill switch for NER + entities + edges + PageRank. |
| `NER_MAX_ENTITIES_PER_CHUNK` | int | `20` | Hard cap after JSON parse. |
| `SEMANTIC_NEAR_THRESHOLD` | float | `0.85` | Cosine cutoff. Spec §5.3. |
| `SEMANTIC_NEAR_TOP_K` | int | `50` | Chroma neighbors per chunk. |
| `TEMPORAL_NEAR_WINDOW_DAYS` | int | `7` | Rolling window. |
| `PAGERANK_ENABLED` | bool | `True` | Separate flag — most likely thing to disable for cost/latency. |

LM Studio URL reuses `ATLAS_LLM__LMSTUDIO_BASE_URL` (already in config from Phase 1).

---

## 6. Testing

| Test file | Coverage |
|---|---|
| `packages/atlas-graph/atlas_graph/tests/test_ner.py` | LM Studio client mocked: happy JSON, 20-cap enforcement, malformed-JSON retry, 5xx → raises, unknown-type filtering, empty-name filtering. |
| `packages/atlas-graph/atlas_graph/tests/test_entities.py` | `write_entities` against fake driver: MERGE idempotency, project-scoping (same `name+type` in two projects = two nodes), REFERENCES dedup on re-run. |
| `packages/atlas-graph/atlas_graph/tests/test_semantic.py` | `build_semantic_near` with mocked Chroma: threshold filter, self-exclusion, canonical ordering, cosine stored on edge. |
| `packages/atlas-graph/atlas_graph/tests/test_temporal.py` | `build_temporal_near`: Documents seeded at varying ages → 6-day = edge, 8-day = no edge, cross-project = no edge. |
| `packages/atlas-graph/atlas_graph/tests/test_pagerank.py` | `run_pagerank` mocked driver: projection created → write called → projection dropped, even on write failure (finally clause). |
| `packages/atlas-graph/atlas_graph/tests/test_store_integration.py` | Existing real-Neo4j integration tests extended: end-to-end ingest writes entities, edges, PageRank scores; gated by `ATLAS_GRAPH__INTEGRATION=1`. |
| `packages/atlas-knowledge/atlas_knowledge/tests/test_ingestion_service.py` | New cases: graph_writer mock expects 4 new method calls in order; PageRank failure → job completes with `pagerank_status="failed"`; NER failure → job aborts + Postgres rolls back. |

---

## 7. Definition of Done

1. After `docker compose up`, ingest a doc → Cypher confirms: 11-typed `Entity` nodes exist, `REFERENCES` edges from chunks, `SEMANTICALLY_NEAR` edges meeting cosine threshold, `TEMPORAL_NEAR` edges to within-7d Documents in the same project, every node has a `pagerank_global` property.
2. Density check on a typical 10-chunk doc: 10–30 `REFERENCES` edges, 5–15 `SEMANTICALLY_NEAR` edges per spec §5.3.
3. Kill switches verified: `ATLAS_GRAPH__NER_ENABLED=false` → ingestion writes structural graph only, `pagerank_status="skipped"`; `ATLAS_GRAPH__PAGERANK_ENABLED=false` → entities + edges written, no projection touched, `pagerank_status="skipped"`.
4. LM Studio down → ingestion job fails with a clear error, no partial Postgres rows committed; LM Studio recovered → next ingest succeeds.
5. PageRank failure (e.g., gds plugin glitch) → job completes with `pagerank_status="failed"`, all other state written.
6. Real-Neo4j integration test in `test_store_integration.py` covers the full pipeline (gated by `ATLAS_GRAPH__INTEGRATION=1` like Plan 2's tests).

---

## 8. Risks and Open Items

- **LM Studio model drift.** The served model is whatever Matt has loaded. If it changes (different gemma variant, different family), entity quality may shift without code changes. Acceptable for single-user; document the prompt in `ner.py` so quality regressions are traceable.
- **TOOL vs DATA_SOURCE confusion.** Mitigated by prompt rules but not eliminated. If misclassifications become annoying in the Knowledge Explorer (Plan 5), we can either prune one type or add a second-pass relabeler. Out of scope for now.
- **PageRank cost as project grows.** At 5000+ chunks, `gds.pageRank.write` may exceed a second per ingest. Not a Plan 3 concern; if it bites, Plan 4 or later can debounce / batch via a periodic job.
- **`Document.created_at` backfill** runs once during the 002 migration; if it fails midway, the migration retries idempotently because we use `SET d.created_at = $ts` only on nodes where `d.created_at IS NULL`.
- **Concurrent ingests in the same project.** Projection name namespacing handles `gds.graph.project` collisions, but two ingests racing on `MERGE (:Entity ...)` could deadlock. Neo4j 5's per-row locking avoids most of it; for safety, `write_entities` runs in its own transaction (already required by the design). Not solving multi-writer correctness in Plan 3 — single-user, serial ingests are the assumption.
