# ATLAS Phase 2 — Plan 5: Knowledge Explorer UI (design)

**Date:** 2026-04-29
**Phase:** 2
**Plan:** 5 of 6
**Depends on:** Plans 1–4 (web ingestion, Neo4j schema, NER+PageRank, hybrid retrieval) — all merged.
**Blocks:** None for shipping; Plan 6 (Note editor) is independent.

## 1. Purpose

Give the user a `/projects/:id/explorer` route that renders a real, interactive Cytoscape graph of the project's knowledge graph (Document / Chunk / Entity nodes) and lets them navigate it by search and by clicking outward from any node. The explorer is **read-only** — no editing, merging, or deleting. It is the first UI surface that makes the knowledge graph visible to the user; everything before this has been backend infrastructure.

## 2. Scope

In scope:
- New backend endpoint `GET /api/v1/knowledge/graph` covering three modes (top-entities overview, search-driven, click-to-expand) under one wire format.
- New `GraphStore.expand_nodes(node_ids)` method on `atlas-graph` (generalizes the existing `expand_chunks`).
- New frontend route `/projects/:id/explorer` with Cytoscape.js + cose-fcose force layout, search bar, filter pills, and side panel.
- Sidebar tab switcher (Chat / Explorer) within an open project.
- Graceful-degradation behavior consistent with Plan 4 (Neo4j down → search returns vector hits with no edges; overview/expand return 503).

Out of scope (explicit non-goals for v1):
- Whole-graph "show me everything" view. Seed-first only. (Possible Plan 5b.)
- Graph editing — no node create / delete / merge / rename from the UI.
- `Note`-typed nodes — Plan 6 territory.
- Time-based or version-based graph diffing.
- Graph export (SVG / PNG).
- Mobile / touch optimization. Desktop only.
- Multi-project overlays.
- Semantic zoom / auto-clustering when zoomed out.

## 3. Architecture

```
                   ┌─────────────────────────────────────┐
                   │  apps/web (React 19 + Vite)         │
                   │                                     │
                   │  /projects/:id/explorer route       │
                   │    ├─ explorer-search-bar           │
                   │    ├─ explorer-filter-pills         │
                   │    ├─ explorer-canvas (Cytoscape)   │
                   │    └─ explorer-side-panel           │
                   │                                     │
                   │  Zustand store: explorer-store      │
                   │  React Query: ['graph', ...]        │
                   └──────────────┬──────────────────────┘
                                  │ HTTP
                                  ▼
                   ┌─────────────────────────────────────┐
                   │  apps/api (FastAPI)                 │
                   │                                     │
                   │  GET /api/v1/knowledge/graph        │
                   │    mode discriminator:              │
                   │      q          → search            │
                   │      seed_*     → expand            │
                   │      (none)     → top-entities      │
                   └──────┬──────────────────┬───────────┘
                          │                  │
                          ▼                  ▼
                  ┌──────────────┐    ┌──────────────────┐
                  │ HybridRetriever│  │ GraphStore       │
                  │  (Plan 4)    │    │  (atlas-graph)   │
                  │              │    │  expand_chunks   │
                  │              │    │  expand_nodes(*) │
                  └──────────────┘    └──────┬───────────┘
                                             │
                                             ▼
                                      ┌─────────────┐
                                      │   Neo4j     │
                                      └─────────────┘
```

`(*)` = new in this plan.

### Mode discrimination

Server picks one mode per request, in priority order:

1. `q` set → **search mode**: run hybrid retriever, take top-K chunk hits, expand 1-hop, return hits + neighbors. `meta.hit_node_ids` lists the matched chunk IDs.
2. `seed_node_ids` or `seed_chunk_ids` set → **expand mode**: 1-hop expansion of the given seeds, capped at 25 new neighbors per seed.
3. Neither → **top-entities mode**: top-N `Entity` nodes by PageRank for the project, plus the edges between them. No chunks, no documents.

If multiple discriminators are sent in one request, `q` wins, then `seed_node_ids`, then `seed_chunk_ids`.

## 4. Backend

### 4.1 Endpoint

`GET /api/v1/knowledge/graph`

Query parameters:

| Name              | Type            | Default | Notes                                                                  |
|-------------------|-----------------|---------|------------------------------------------------------------------------|
| `project_id`      | UUID, required  | —       | Project scope. 404 if absent or not found.                             |
| `q`               | string          | —       | When set → search mode.                                                |
| `seed_chunk_ids`  | comma-sep UUIDs | —       | When set → expand mode (chunk seeds).                                  |
| `seed_node_ids`   | comma-sep UUIDs | —       | When set → expand mode (any node type, used by Expand button).         |
| `node_types`      | comma-sep enum  | all     | Server-side filter: subset of `Document,Chunk,Entity`.                 |
| `limit`           | int             | 30/50   | 30 for top-entities, 50 for search/expand. Hard cap 200.               |

### 4.2 Response shape (one wire format for all modes)

```json
{
  "nodes": [
    {
      "id": "uuid-string",
      "type": "Entity",
      "label": "Llama 3",
      "pagerank": 0.0123,
      "metadata": {
        "node_type_specific": "fields"
      }
    }
  ],
  "edges": [
    {
      "id": "uuid-string",
      "source": "uuid-string",
      "target": "uuid-string",
      "type": "MENTIONS"
    }
  ],
  "meta": {
    "mode": "search",
    "truncated": false,
    "hit_node_ids": ["..."],
    "degraded_stages": []
  }
}
```

Field semantics:
- `nodes[].type` ∈ `{"Document", "Chunk", "Entity"}` for v1. Plan 6 will add `"Note"`.
- `nodes[].label` is what the UI renders next to the node: document title, first ~80 chars of chunk text, or entity surface form.
- `nodes[].pagerank` populated for entities; null for chunks and documents.
- `nodes[].metadata` carries type-specific extras (chunk source document id, document mime type, entity type, etc.). Schema is loose by design — FE side panel just renders it.
- `edges[].type` ∈ `{"HAS_CHUNK", "MENTIONS", "RELATED_TO", ...}` — whatever Plan 2/3 has emitted.
- `meta.truncated` true if `limit` cut the result. FE shows a banner.
- `meta.hit_node_ids` non-empty only for search mode.
- `meta.degraded_stages` mirrors the Plan 4 retrieval-degradation list (e.g. `["graph_unavailable"]`) when search mode runs in fallback.

### 4.3 New `GraphStore.expand_nodes(node_ids)`

Existing `GraphStore.expand_chunks(chunk_ids)` is chunk-typed. Plan 5 needs to expand any node id. Add a sibling method that:
- Takes a list of node ids of any type.
- Runs a single Cypher: `MATCH (n) WHERE n.id IN $ids OPTIONAL MATCH (n)-[r]-(m) RETURN n, r, m LIMIT $cap`.
- Returns the same `(nodes, edges)` shape as `expand_chunks`.
- Caps at 25 neighbors per seed (configurable, default 25). Implemented via a per-seed `LIMIT` in a `UNWIND ... CALL { ... }` subquery, not a global LIMIT, so a single high-degree seed can't starve the others.

`expand_chunks` stays as-is — Plan 4 calls it directly and we don't want to disturb that path.

### 4.4 Wiring

- Endpoint lives in `apps/api/atlas_api/routers/knowledge.py`. Depends on existing `Retriever` and `GraphStore` providers (already wired in `lifespan`).
- New Pydantic models in `packages/atlas-knowledge/atlas_knowledge/models/graph.py`: `GraphNode`, `GraphEdge`, `GraphMeta`, `GraphResponse`.
- 404 if project doesn't exist. 422 if `node_types` contains an unknown value.

### 4.5 Degraded behavior

Same envelope as Plan 4:
- **Top-entities mode, Neo4j down:** 503 with `{"error": "graph_unavailable", "message": "..."}`.
- **Expand mode, Neo4j down:** 503 with same shape.
- **Search mode, Neo4j down:** returns the hybrid retriever's vector-only fallback (chunk hits, no edges). `meta.degraded_stages` = `["graph_unavailable"]`. UI banner explains.
- **Search mode, retriever down entirely:** 503.

## 5. Frontend

### 5.1 Routes and navigation

`apps/web/src/main.tsx` adds the new route:

```ts
{ path: "projects/:id", element: <ProjectRoute /> },
{ path: "projects/:id/explorer", element: <ExplorerRoute /> },
```

The Sidebar gets a per-project tab switcher (Chat / Explorer) that appears when a project is open. The switcher is a simple two-button group; selecting one navigates to the corresponding route. Default lands on Chat (existing behavior).

Sidebar icon: Lucide `Network` for Explorer, existing `MessageSquare` for Chat.

### 5.2 Component tree

```
routes/explorer.tsx                 # owns react-query keys + URL state
└── components/explorer/
    ├── explorer-canvas.tsx         # Cytoscape mount via ref, layout, click handlers
    ├── explorer-search-bar.tsx     # debounced input, fires search query on Enter
    ├── explorer-filter-pills.tsx   # 3 pills (Document/Chunk/Entity); client-side hide
    ├── explorer-side-panel.tsx     # Radix Sheet; node detail + Expand button
    └── explorer-empty-state.tsx    # spinner / error / degraded banner / "no data"
```

### 5.3 Library choice

- **Cytoscape.js** + **`cytoscape-fcose`** for force-directed layout. Both MIT.
- No React wrapper. Mount Cytoscape on a div ref in `explorer-canvas.tsx` and bridge via store actions.
- New deps: `cytoscape`, `cytoscape-fcose`, `@types/cytoscape`.
- Layout runs incrementally on merge — existing nodes keep positions; only new nodes are placed.

### 5.4 State (`stores/explorer-store.ts`, Zustand)

```ts
type NodeType = "Document" | "Chunk" | "Entity";

interface ExplorerState {
  nodes: GraphNode[];
  edges: GraphEdge[];
  hitNodeIds: Set<string>;
  selectedNodeId: string | null;
  visibleTypes: Set<NodeType>;        // pill state
  query: string;
  mode: "overview" | "search" | "expand";
  loading: boolean;
  error: string | null;
  degradedStages: string[];
  truncated: boolean;

  // actions
  replaceGraph(response: GraphResponse): void;   // overview, search
  mergeGraph(response: GraphResponse): void;     // expand
  selectNode(id: string | null): void;
  toggleType(t: NodeType): void;
  setQuery(q: string): void;
  reset(): void;
}
```

`mergeGraph` dedupes by `id`. `replaceGraph` clears positions in Cytoscape so the new layout runs from scratch.

### 5.5 Data fetching (React Query)

Three query keys, all keyed off `projectId`:

- `['graph', projectId, 'overview']` — fired on mount; falls back to overview when search clears.
- `['graph', projectId, 'search', query]` — debounced 250ms; only fires when `query.length >= 2`.
- `['graph', projectId, 'expand', seedId]` — fired by the side panel's Expand button.

Search and expand responses **merge**; overview **replaces**. `staleTime` = 30s for overview, 0 for search/expand (always re-fetch on a new query).

### 5.6 Visuals

- Node colors: Document = `#3b82f6` (blue), Chunk = `#9ca3af` (grey), Entity = `#10b981` (green).
- Hit nodes: 3px gold (`#facc15`) border.
- Selected node: 4px ring (`#a855f7` purple).
- Edges: thin grey lines, type as label on hover only (no permanent label clutter).
- Node labels: shown for Documents and Entities by default; Chunks show label only on hover or when selected (chunk text labels are too long to display permanently).
- Node size: scaled by PageRank for entities (min 20px, max 60px), fixed for chunks/documents.

### 5.7 Interactions

- **Click node** → `selectNode(id)` → side panel slides in (Radix `Sheet`, already in repo).
- **Side panel "Expand neighborhood" button** → fires expand query with the selected node id; merge result; panel stays open.
- **Search Enter** → fires search query; `replaceGraph` on response; auto-pan to first hit.
- **Pill toggle** → `toggleType(t)` updates `visibleTypes`; canvas applies Cytoscape style `display: none` to hidden types and to edges connected to hidden types. No re-fetch.
- **Truncated banner** → small toast at the top of the canvas: "Showing top N — refine your search to see more."
- **Degraded banner** → orange banner across the top when `degradedStages` is non-empty: "Graph data unavailable — showing semantic results only."
- **Clear search** (X in search bar) → `reset()` to overview.

### 5.8 Types

Hand-written TypeScript types in `apps/web/src/lib/api/knowledge-graph.ts`, mirrored from the backend Pydantic models. (No OpenAPI codegen in this repo yet.)

## 6. Testing

### 6.1 Backend

`apps/api/atlas_api/tests/test_knowledge_graph_endpoint.py`:
- Each mode returns the right shape with a fake `GraphStore` and fake `Retriever`.
- Mode discrimination follows priority order (`q` > `seed_node_ids` > `seed_chunk_ids` > none).
- `node_types` filter excludes the right types.
- `limit` is honored and `meta.truncated` flips when the cap is hit.
- 404 when project is missing.
- 422 when `node_types` has an unknown value.
- Degraded paths: graph store raises → top-entities/expand return 503; search returns vector-only with `degraded_stages`.

`packages/atlas-graph/atlas_graph/tests/test_expansion.py` (extend):
- `expand_nodes` with entity seeds, chunk seeds, mixed seeds.
- 25-neighbor cap is enforced per seed, not globally.
- Empty seed list returns `(nodes=[], edges=[])`.

Acceptance test (`pytest -m slow`, real Neo4j, same pattern as Plans 2–4): seed a tiny project with one document, two chunks, three entities; hit `/knowledge/graph` in all three modes; assert nodes/edges round-trip and `hit_node_ids` is populated for search.

### 6.2 Frontend

Vitest + React Testing Library:
- `explorer-side-panel.test.tsx` — renders selected node's metadata; Expand button fires the right query.
- `explorer-search-bar.test.tsx` — debounce; Enter fires query; X clears.
- `explorer-filter-pills.test.tsx` — toggle updates store; click is idempotent.
- `explorer-empty-state.test.tsx` — loading / error / degraded variants render the right copy.
- `explorer-store.test.ts` — `mergeGraph` dedupes; `replaceGraph` clears; `toggleType` flips set membership.

**Cytoscape canvas is not unit-tested in v1.** It mounts in a real DOM and is awkward in jsdom. We assert the canvas component dispatches the right store actions on its props/effects, but we do not validate visual output. This is a known gap; manual smoke test before declaring done.

## 7. Acceptance criteria

1. Opening `/projects/:id/explorer` for a project that has been ingested shows ~30 entities laid out by force-directed layout, sized by PageRank.
2. Typing a query and pressing Enter swaps the canvas to a hit-centric subgraph; matching chunks show a gold border.
3. Clicking any node opens the side panel with that node's metadata and outgoing edges.
4. The panel's Expand button adds 1-hop neighbors of the selected node to the canvas without resetting layout, capped at 25 new neighbors per click.
5. Toggling a filter pill instantly hides or shows that node type and its connected edges; no network call.
6. With Neo4j down, the explorer page does not crash: search shows a degraded banner and renders vector-only hits with no edges; overview and expand show a clear error toast.
7. The Sidebar tab switcher navigates between Chat and Explorer for the open project.

## 8. Risks and open items

- **fcose layout perf at 150+ nodes.** We've capped at 50 per fetch and 25 per expand, so a heavy explorer session could reach ~150–300 nodes. fcose is fine to that size on modern desktops; if a user reports lag, we can switch to `cose` (simpler) or add a "reset layout" button. Not a v1 blocker.
- **Chunk labels are long.** We default to hide-on-non-hover for chunks. If users complain about not knowing what they're looking at, switch to a 40-char truncated label.
- **No Cytoscape integration test.** Documented gap. Manual smoke before merge.
- **Plan 6 will add `Note` nodes.** Plan 5's response shape is open to that — `nodes[].type` is a string, not an enum constraint on the FE — so adding `Note` later only needs a fourth color, fourth pill, and the FE type union extended.
- **Search latency.** Hybrid retrieval is ~300–500ms. The 250ms debounce + spinner state should make this feel responsive; if not, consider an instant local "previous results" while the new query loads.

## 9. Definition of Done

- [ ] `GET /api/v1/knowledge/graph` is implemented, tested, and documented in the router docstring.
- [ ] `GraphStore.expand_nodes` is implemented and tested.
- [ ] `/projects/:id/explorer` route renders for a real project with ingested content.
- [ ] All seven acceptance criteria pass on a manual smoke run.
- [ ] Backend unit + degraded tests pass; opt-in `slow` acceptance test passes against real Neo4j.
- [ ] Frontend unit tests pass.
- [ ] Code review approved (per workflow: Haiku implementer + Sonnet reviewers).
