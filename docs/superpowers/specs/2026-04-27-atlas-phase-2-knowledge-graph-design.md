# ATLAS Phase 2 — Knowledge Graph Design

**Status:** Draft · 2026-04-27
**Implements:** `docs/atlas_design_document.md` §5 (Knowledge Graph RAG System) and §14 Phase 2 (Weeks 5-8)
**Predecessors:** Phase 1 (Plans 1-6) merged. ATLAS now has chat with vector RAG, project CRUD, ingestion (PDF + markdown), React frontend with RAG drawer + ingest modal, full docker-compose stack.

---

## 1. Purpose

Phase 2 makes ATLAS retrieval graph-aware. Standard vector RAG finds similar chunks but loses relational context — the consulting use case prizes connections ("this CAC concern shows up in three proposals; the same methodology was used in another engagement"). Phase 2 ships:

- A graph layer (Neo4j) that captures explicit and inferred relationships between Documents, Chunks, Entities, Projects, and Notes.
- An NER pipeline that extracts entities and creates `REFERENCES` edges between chunks and concepts.
- Hybrid retrieval (BM25 + vector + 1-hop graph expansion + personalized PageRank + cross-encoder reranker) that replaces Phase 1's pure-vector `Retriever`.
- A read-only Knowledge Explorer UI (Cytoscape) so users can see what ATLAS knows.
- A TipTap note editor with inline entity linking, so users can curate the graph by hand.
- Web/URL ingestion (Playwright + Trafilatura) — the smallest Phase 2 increment, ships first as Plan 1.

After Phase 2 closes, the answer to "why does this answer feel smart" is graph-aware retrieval. The phase is complete when a chat that asks about an indirectly-connected concept surfaces a chunk that pure-vector retrieval would miss.

---

## 2. Scope

### In scope
- Web/URL ingestion (Playwright + Trafilatura).
- Neo4j 5 community as a new docker-compose service; `atlas-graph` Python package.
- Graph schema per design doc §5.2 (Project / Document / Chunk / Entity / Note / WebClip / Conversation nodes; structural + semantic + entity + temporal edges).
- One-shot backfill of existing Phase 1 chunks into Neo4j on Plan 2 deploy.
- Entity NER pipeline (strategy decided at Plan 3 brainstorm).
- Hybrid retrieval engine: Postgres FTS for BM25, RRF merge with Chroma cosine, 1-hop graph expansion, personalized PageRank from seed set, cross-encoder rerank (`ms-marco-MiniLM-L-6-v2`).
- Hybrid retrieval is gated by config flag (`ATLAS_RETRIEVAL__MODE=hybrid|vector`) so Plan 1's vector path stays as a rollback.
- Knowledge Explorer UI: `/projects/:id/explorer` route, Cytoscape force-directed graph, node-type filters, click-to-detail panel, hybrid-search bar with subgraph highlighting.
- TipTap note editor: `/projects/:id/notes` route, markdown WYSIWYG, `@`-mention autocomplete on existing entities, note ingestion routes through the same parser→chunker→embed→graph pipeline.

### Out of scope (deferred to later phases)
- Browser-extension capture (design doc §5.6 web-clip flow). Single-URL paste only.
- Batch URL importer / scheduled re-fetch / RSS feeds.
- Graph editing in the Knowledge Explorer (creating / deleting / merging nodes by hand). Read-only viz.
- Multi-user collaborative editing in the note editor.
- Multi-project graph queries (every Cypher query filters `project_id`).
- pgvector migration. Chroma stays.
- Qdrant. Stays in the design doc as a future option.
- gds enterprise features. ATLAS is personal-use; community edition is fine.
- Cloud / managed Neo4j (Aura). Local docker-compose only.

---

## 3. Architecture

### 3.1 Decomposition

Six independently-shippable plans, executed in order:

| Plan | Title | Closes |
|---|---|---|
| 1 | Web/URL ingestion | URL paste → ingestion job → chunks in Chroma |
| 2 | Neo4j + graph schema + write path | Graph DB up, every ingest writes nodes + structural edges, backfill done |
| 3 | NER + entity edges + PageRank | Entity nodes, REFERENCES/SEMANTICALLY_NEAR/TEMPORAL_NEAR edges, on-ingest global PageRank |
| 4 | Hybrid retrieval | BM25+vector+graph+rerank engine, gated by config, ships as default |
| 5 | Knowledge Explorer UI | Cytoscape graph viewer, project-scoped, hybrid-search highlights |
| 6 | Note editor | TipTap, entity mentions, notes route through ingestion pipeline |

**Dependencies:** 1 → 2 → 3 → 4; 2 → 5 → 6. After Plan 3 lands, Plans 4 and 5 can run in parallel; after Plan 5 lands, Plan 6 has the graph data it needs to autocomplete `@` mentions.

### 3.2 Why this order

Plan 1 is pure additive — URL ingestion lands without touching Neo4j or retrieval. If Phases 2-6 stall, ATLAS still gains URL ingestion.

Plan 2 ships Neo4j wired into the stack with no behavior change visible to the user — the graph is being written, but nothing reads it yet. This is the lowest-risk way to introduce a new database service.

Plan 3 enriches the graph (entities, semantic edges, PageRank). Still no behavior change; the chat WS continues using Phase 1's vector Retriever.

Plan 4 swaps the retrieval engine. The change is gated by `ATLAS_RETRIEVAL__MODE`; flipping to `vector` is the immediate rollback path.

Plans 5 and 6 are the user-visible surface. Both depend on the graph being populated (Plan 3) but are otherwise independent.

### 3.3 Package layout

New package: **`packages/atlas-graph/`**.

```
packages/atlas-graph/
├── pyproject.toml
├── atlas_graph/
│   ├── __init__.py
│   ├── store.py             GraphStore — async neo4j driver wrapper, Cypher helpers
│   ├── schema/
│   │   ├── __init__.py
│   │   ├── constraints.py   Cypher CREATE CONSTRAINT / INDEX statements
│   │   └── migrations/
│   │       └── 001_initial_schema.cypher
│   ├── ingestion/
│   │   ├── __init__.py
│   │   ├── writer.py        write_document_chunks(...) — used by IngestionService
│   │   ├── ner.py           NER pipeline (Plan 3)
│   │   └── edges.py         build_semantic_near, build_temporal_near (Plan 3)
│   ├── retrieval/
│   │   ├── __init__.py
│   │   ├── bm25.py          Postgres FTS query (Plan 4)
│   │   ├── rrf.py           Reciprocal Rank Fusion merge (Plan 4)
│   │   ├── expansion.py     1-hop graph walk (Plan 4)
│   │   ├── pagerank.py      Personalized PageRank via gds (Plan 4)
│   │   ├── rerank.py        Cross-encoder reranker (Plan 4)
│   │   └── hybrid.py        HybridRetriever — orchestrates the 5 stages (Plan 4)
│   ├── backfill.py          One-shot Postgres → Neo4j migration (Plan 2)
│   └── tests/
└── ...
```

`atlas-knowledge` keeps its single responsibility: chunking, embedding, vector store, parsers. Plan 4's `HybridRetriever` lives in `atlas-graph` because hybrid retrieval is a graph-aware operation; it composes `atlas-knowledge`'s vector store + Postgres FTS + Neo4j queries.

### 3.4 Service topology after Phase 2

```
┌─────────────────────────────────────────────────────────────┐
│ docker-compose stack                                        │
├─────────────────────────────────────────────────────────────┤
│  postgres    (already)  — projects, chunks (FTS), sessions  │
│  redis       (already)  — caching, future job queue         │
│  api         (already)  — FastAPI, now with Cypher driver   │
│  web         (already)  — React frontend                    │
│  neo4j       (NEW)      — graph DB, gds plugin, port 7687   │
└─────────────────────────────────────────────────────────────┘
```

Chroma stays embedded in the `api` service as it is in Phase 1. Neo4j is the only new service.

---

## 4. Cross-cutting Decisions

### 4.1 Graph DB: Neo4j 5 community
Mature, Cypher is well-documented, `gds` plugin provides PageRank and graph algorithms out of the box. Cost: ~1.5-2 GB heap, separate auth surface. Acceptable on a 16 GB dev machine; the `addendum` already maps the Terraform path to GCE if cloud deployment matters later.

Driver: `neo4j` Python package, async API. Wrapped in `atlas_graph.store.GraphStore` so call sites use Pythonic methods, not raw Cypher strings, except in migrations and the backfill where Cypher is the cleanest option.

Schema lives in `atlas_graph/schema/migrations/*.cypher`, applied on api startup via a small migration runner that records applied migration ids in a Neo4j `(:Migration {id, applied_at})` node.

### 4.2 BM25 storage: Postgres FTS
`tsvector` column on `chunks`, GIN index, `to_tsquery` queries, `ts_rank_cd` for scoring. Postgres is already in the stack; no new dep, no new infra. Plan 4 adds an alembic migration for the column + index.

RRF merges BM25 rank with Chroma cosine rank — operates on ranks, not raw scores, so different scales don't matter. Standard k=60.

### 4.3 Vector store: Chroma stays
No migration to pgvector. Chroma is working, embedded, single-collection-per-user already, and the Phase 1 hooks into Chroma's persistence on disk are stable.

### 4.4 Reranker: `ms-marco-MiniLM-L-6-v2`
Sentence-transformers cross-encoder, ~23 MB, lazy in-process cache (same pattern as `BAAI/bge-small-en-v1.5` in Phase 1). Loaded on first hybrid query, cached for the process lifetime. Top-30 candidate cap on input to bound latency at ~50-200 ms per query.

### 4.5 PageRank: hybrid timing
Two PageRank flavors:

- **Global PageRank, on-ingest.** Computed via `gds.pageRank` over the project's subgraph after each ingestion job completes. Sparse update; only the affected nodes recompute. Stored on each node as `pagerank_global` property. Reflects "importance in the project's knowledge graph as a whole."
- **Personalized PageRank, on-query.** Computed at retrieval time on the small subgraph reached via 1-hop expansion, seeded from the BM25+vector merge. Reflects "importance relative to this specific query's seed set." Not persisted.

The cross-encoder reranker scores each candidate on (query, chunk_text) alone — it doesn't see PageRank. The two PageRank scores combine with the reranker score AFTER reranking:

`final_score = rerank_score · log(1 + pagerank_global) · pagerank_personalized`

Top-K by `final_score`. Plan 3 wires global PageRank; Plan 4 wires personalized and the final scoring.

### 4.6 Backfill
Plan 2 ships a one-shot Postgres → Neo4j backfill that walks every existing `KnowledgeNode` (Document or Chunk) and writes the corresponding Neo4j node + structural edges (`PART_OF Project`, `BELONGS_TO Document`). Idempotent via Cypher `MERGE`. Runs in batches of 1000 nodes, logs progress every batch, supports resume by reading the last-applied batch id from Neo4j.

Triggered by `ATLAS_GRAPH__BACKFILL_ON_START=true` env var. Default false. Flipped on once for the migration, then off. Backfill is also exposed as a CLI: `uv run atlas-graph backfill` for manual re-runs.

### 4.7 Neo4j memory
`NEO4J_dbms_memory_heap_max__size=2G`, `NEO4J_dbms_memory_pagecache_size=512M` in compose. Total Neo4j footprint ~2.5 GB at idle, more under heavy ingest. Acceptable on dev machines with 16 GB+. The compose file documents the requirement.

### 4.8 NER strategy: deferred to Plan 3
Three viable approaches, decision postponed to Plan 3 brainstorm:
- **SpaCy** (`en_core_web_sm` or `_md`): fast, free, local; misses consulting-specific entities (methodologies, metrics, KPIs).
- **LLM-based** (Claude or LM Studio): higher quality, custom entity types possible; costs tokens per ingest.
- **Hybrid**: SpaCy for standard types, LLM for high-value docs flagged at ingest time.

The Phase 2 spec assumes "some NER pipeline writes Entity nodes and REFERENCES edges" without locking the strategy.

---

## 5. Plan-by-Plan Sketches

### 5.1 Plan 1 — Web/URL ingestion

**Goal:** From the IngestModal, paste a URL, get the same "Ingested N chunks" experience as PDF/markdown today.

**Backend:**
- New endpoint `POST /api/v1/knowledge/ingest/url` with body `{project_id, url}` returning an `IngestionJob`.
- New parser: `atlas_knowledge.parsers.url.parse_url(url) -> ParsedDocument`. Internally: Playwright launches a Chromium browser, navigates to the URL with a 30s timeout, captures the rendered HTML, hands it to Trafilatura which extracts the main article body + metadata (title, author, date).
- The existing `IngestionService` orchestrates parser → chunker → embed → store unchanged.

**Frontend:**
- New "URL" tab in `IngestModal`. Single `Input` for the URL, Ingest button. Same job-polling flow as the existing tabs.

**Risks:**
- Playwright pulls a ~200 MB Chromium binary into the api Docker image. Acceptable one-time tax; the multi-stage build keeps the runtime layer small.
- Some sites block headless browsers. Failed parses produce a `failed` ingestion job with a useful error; user falls back to copying markdown.

**Out of scope:** browser extension, batch importer, scheduled re-fetch.

### 5.2 Plan 2 — Neo4j + graph schema + write path

**Goal:** Neo4j is up, every ingest writes nodes + structural edges, backfill of existing chunks is done. No behavior change visible to the user.

**Backend:**
- Add `neo4j` service to `infra/docker-compose.yml`. `NEO4J_AUTH=neo4j/<password from .env>`, `NEO4J_PLUGINS=["graph-data-science"]`, memory env vars per §4.7.
- New `packages/atlas-graph/` package per §3.3 layout.
- `GraphStore` async wrapper around the `neo4j` driver. Connection pool from app.state, mirrors Phase 1's `get_session` dep pattern.
- Schema migration runner: applies `atlas_graph/schema/migrations/*.cypher` in id order, records applied ids in Neo4j. Runs on api startup.
- First migration creates uniqueness constraints + lookup indexes for: `(:Project {id})`, `(:Document {id})`, `(:Chunk {id})`, plus indexes on `Chunk.project_id`, `Document.project_id`.
- `IngestionService` extension: after Chroma write, calls `GraphStore.write_document_chunks(...)` which `MERGE`s the Document node, `MERGE`s every Chunk node, `MERGE`s the `PART_OF` edge to Project, `MERGE`s `BELONGS_TO` edges from Chunks to Document.
- One-shot backfill script per §4.6.

**Definition of Done:** after `docker compose up`, an ingestion job writes both Chroma vectors and Neo4j nodes; ad-hoc Cypher confirms the structure; backfill runs once on a fresh stack and brings every Phase 1 chunk into the graph.

### 5.3 Plan 3 — NER + entity edges + PageRank

**Goal:** Entity nodes exist; `REFERENCES`, `SEMANTICALLY_NEAR`, `TEMPORAL_NEAR` edges populate; every node has a `pagerank_global` property.

**Backend:**
- NER pipeline (strategy decided at Plan 3 brainstorm). Runs as part of ingestion, after chunking, in parallel with embedding.
- For each extracted entity: `MERGE (:Entity {name, type})` (entities are project-scoped — same name in two projects = two nodes), then `MERGE` a `REFERENCES` edge from the Chunk to the Entity.
- After ingestion completes, compute pairwise cosine over the new chunks and existing project chunks; create `SEMANTICALLY_NEAR` edges where cosine ≥ 0.85 (threshold tunable; gets its own constant).
- Create `TEMPORAL_NEAR` edges between Documents ingested within the same week, scoped by project.
- Run `gds.pageRank.write` on the project's subgraph after every ingestion. Updates `pagerank_global` on every node touched (or affected via edge).

**Definition of Done:** ingest a doc; Cypher confirms entities extracted, edges populated, PageRank scores updated. Density check: a typical doc has 10-30 entity edges and 5-15 semantic edges.

### 5.4 Plan 4 — Hybrid retrieval

**Goal:** Replace `atlas_knowledge.retrieval.Retriever` with `atlas_graph.retrieval.HybridRetriever`. Chat WS uses the new path, gated by config.

**Backend:**
- Alembic migration: add `tsvector` column on `chunks` table, GIN index on it, BEFORE INSERT/UPDATE trigger to keep it in sync with the chunk text.
- Backfill the tsvector for existing chunks.
- `bm25.search(query, project_id, top_k=20) -> list[(chunk_id, rank)]` — Postgres FTS using `to_tsquery` + `ts_rank_cd`.
- `rrf.merge(rankings, k=60) -> list[(chunk_id, score)]` — standard RRF.
- `expansion.expand(seed_chunk_ids, project_id) -> set[chunk_id]` — 1-hop walk over `REFERENCES → Entity → REFERENCES` (co-entity), `SEMANTICALLY_NEAR` (depth 1), `LINKED_TO` (depth 1), plus document-sibling walk (descend to chunks within ±2 positions of seeds).
- `pagerank.personalized(seed_scores, expanded_ids, project_id) -> list[(chunk_id, pr_score)]` — `gds.pageRank.stream` with `sourceNodes` = seeds.
- `rerank.rerank(query, candidates, top_k=30) -> list[(chunk_id, rerank_score)]` — cross-encoder, lazy-loaded; takes (query, chunk_text), returns relevance score per candidate.
- `HybridRetriever.retrieve(query) -> RetrievalResult` orchestrates: BM25(20) + vector(20) → RRF merge → top-20 seeds → graph expand (capped at 100 candidates) → cross-encoder rerank → personalized PageRank on the reranker top-30 → final score `rerank_score · log(1 + pagerank_global) · pagerank_personalized` → top-8 by final score.
- Config flag `ATLAS_RETRIEVAL__MODE` selects vector or hybrid retriever. Defaults to `hybrid` after Plan 4 lands; `vector` is the rollback.
- Chat WS DI: which retriever instance is on `app.state.retriever` is decided in `lifespan` based on config.

**Definition of Done:** a chat about an indirectly-connected concept ("how did we approach geo-lift in the CircleK proposal" when the answer chunk doesn't share keywords) returns the right chunk via hybrid where vector retrieval misses it. Reranker visibly reorders. Rollback flag works.

### 5.5 Plan 5 — Knowledge Explorer UI

**Goal:** `/projects/:id/explorer` renders a real Cytoscape graph for the project.

**Backend:**
- New endpoint `GET /api/v1/knowledge/graph?project_id=&node_types=&limit=` returns `{nodes: [...], edges: [...]}` JSON. Cypher query selects all nodes in the project (filtered by type) up to a limit; edges only between selected nodes.
- Endpoint also accepts `?seed_chunk_id=` for "show neighborhood of this chunk" mode (used by the search-bar highlight).

**Frontend:**
- New `/projects/:id/explorer` route.
- Cytoscape.js force-directed layout. Node coloring by type (Document = blue, Chunk = grey, Entity = green, Note = orange). Edge styling by type.
- Filter pills for node types (toggle Document/Chunk/Entity/Note visibility).
- Click a node → side panel shows full content + metadata + outgoing edges.
- Search bar: types a query, hits Enter, runs hybrid retrieval, highlights matching chunks + their 1-hop neighbors in the graph.

**Out of scope:** graph editing (creating/deleting/merging nodes by hand). Read-only viz.

### 5.6 Plan 6 — Note editor

**Goal:** `/projects/:id/notes` lets the user write notes; entity mentions create graph edges; notes are first-class graph nodes.

**Backend:**
- New `notes` Postgres table: `{id, project_id, user_id, title, body_markdown, created_at, updated_at}`.
- REST CRUD: `GET/POST/PATCH/DELETE /api/v1/notes` with `?project_id=` filter.
- On create/update: route the note's markdown through the existing ingestion pipeline (parser → chunker → embed → graph) so chunks land in Chroma and Neo4j as `Note`-typed nodes (or `Document` with `type='note'` — schema detail decided in Plan 6 brainstorm).
- On note delete: cascade-delete chunks from Chroma and Neo4j.

**Frontend:**
- New `/projects/:id/notes` route. Sidebar lists notes for the project; clicking opens the editor.
- TipTap WYSIWYG markdown editor.
- `@`-mention extension with autocomplete: queries `GET /api/v1/knowledge/entities?project_id=&prefix=` for matching entities; selecting one inserts a styled mention. On save, mentions become `TAGGED_WITH` edges from Note → Entity.
- Save button → POST/PATCH; auto-save debounced at 2 seconds.

**Out of scope:** collaborative editing, version history, exporting notes to other formats.

---

## 6. Risks and Open Items

- **gds enterprise license.** Community edition is fine for personal use; if ATLAS becomes a hosted product, the license question reopens. Out of scope for Phase 2.
- **Backfill on a busy graph.** The largest single risk in Plan 2. Mitigation: idempotent `MERGE`, batch size 1000, progress logging, resume support.
- **Reranker latency.** Worst case ~200 ms; Plan 4 caps candidate count at 30 to bound this.
- **Cypher injection.** All Cypher queries use parameterized statements; no string interpolation of user input. Standard hygiene, called out for plan reviewers.
- **Graph size on a real consulting practice.** A few thousand documents → ~50k chunks → ~200k nodes / ~500k edges. Comfortably within Neo4j community edition's free tier (no node count limit) and well below the memory budget. PageRank on a graph this size is ~1 second on the 2 GB heap.
- **Note editor schema collision.** Phase 1 already uses `Document` for ingested files. Phase 2 either reuses `Document` with `type='note'` or introduces a separate `Note` label. Decided in Plan 6 brainstorm; both work.
- **NER strategy.** Locked at Plan 3 brainstorm. Spec assumes "some NER pipeline writes Entity nodes" without binding to one approach.

---

## 7. Definition of Done (Phase 2)

Phase 2 closes when all of the following are true:

1. From the ingest modal, pasting a URL produces an ingestion job that completes; the chunks are queryable via the existing search endpoint (Plan 1).
2. `docker compose up` brings Neo4j up alongside the rest of the stack with no manual steps; one ingestion writes nodes + structural edges; Cypher can confirm the structure; full backfill runs once on a fresh stack and brings every existing Phase 1 chunk into the graph (Plan 2).
3. NER labels recognizable entities; `REFERENCES`, `SEMANTICALLY_NEAR`, and `TEMPORAL_NEAR` edges populate on ingest; every node has a `pagerank_global` property (Plan 3).
4. A chat about an indirectly-connected concept surfaces a chunk that pure-vector retrieval would miss; the cross-encoder reranker visibly reorders results; the rollback flag (`ATLAS_RETRIEVAL__MODE=vector`) works (Plan 4).
5. `/projects/:id/explorer` renders a meaningful graph for a real project; node-type filters work; clicking a node opens a detail panel; the search bar highlights matching subgraphs (Plan 5).
6. `/projects/:id/notes` lets me write a note, `@`-mention an existing entity, save; the note appears as a graph node in the explorer; the note's text becomes findable via chat retrieval (Plan 6).

---

*ATLAS Phase 2 — Knowledge Graph Design · 2026-04-27*
