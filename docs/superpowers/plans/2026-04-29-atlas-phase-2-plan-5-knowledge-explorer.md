# ATLAS Phase 2 — Plan 5: Knowledge Explorer UI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship `/projects/:id/explorer` — a seed-first Cytoscape graph view of a project's knowledge graph, plus the unified `GET /api/v1/knowledge/graph` endpoint backing it.

**Architecture:** Single backend endpoint with three modes (`top_entities`, `search`, `expand`) discriminated by which query params are present, returning one wire format `{nodes, edges, meta}`. Frontend is a Zustand-backed React route mounting Cytoscape.js + cose-fcose layout. Search mode reuses the Plan 4 `HybridRetriever`; expand is capped at 25 neighbors per seed to keep the canvas legible.

**Tech Stack:** FastAPI (existing), Pydantic v2 (existing), Neo4j 5 + atlas-graph (existing), HybridRetriever (Plan 4), React 19 + Vite + Tailwind + Radix (existing), Zustand 5 (existing), React Query 5 (existing), Cytoscape.js + cytoscape-fcose (new).

**Spec:** `docs/superpowers/specs/2026-04-29-atlas-phase-2-plan-5-knowledge-explorer-design.md`

---

## File Map

**Backend (create):**
- `packages/atlas-knowledge/atlas_knowledge/models/graph.py` — Pydantic models for the graph response.
- `apps/api/atlas_api/tests/test_knowledge_graph_endpoint.py` — endpoint integration tests.
- `packages/atlas-graph/atlas_graph/tests/test_subgraph.py` — fetch_top_entities + fetch_subgraph_by_seeds tests (fake driver).
- `packages/atlas-graph/atlas_graph/tests/test_subgraph_integration.py` — opt-in real-Neo4j acceptance test.

**Backend (modify):**
- `packages/atlas-graph/atlas_graph/store.py` — add `fetch_top_entities`, `fetch_subgraph_by_seeds`.
- `packages/atlas-graph/atlas_graph/__init__.py` — re-export new types if needed.
- `apps/api/atlas_api/routers/knowledge.py` — add `GET /api/v1/knowledge/graph` handler + docstring.

**Frontend (create):**
- `apps/web/src/lib/api/knowledge-graph.ts` — TS types + `fetchKnowledgeGraph` function.
- `apps/web/src/stores/explorer-store.ts` — Zustand store.
- `apps/web/src/stores/explorer-store.test.ts` — store unit tests.
- `apps/web/src/components/explorer/explorer-empty-state.tsx` + `.test.tsx`
- `apps/web/src/components/explorer/explorer-filter-pills.tsx` + `.test.tsx`
- `apps/web/src/components/explorer/explorer-search-bar.tsx` + `.test.tsx`
- `apps/web/src/components/explorer/explorer-side-panel.tsx` + `.test.tsx`
- `apps/web/src/components/explorer/explorer-canvas.tsx` (no unit test — see spec §6.2)
- `apps/web/src/components/sidebar/project-tabs.tsx` — Chat / Explorer tab switcher.
- `apps/web/src/routes/explorer.tsx` — explorer route with react-query.

**Frontend (modify):**
- `apps/web/package.json` — add `cytoscape`, `cytoscape-fcose`, `@types/cytoscape`.
- `apps/web/src/main.tsx` — add the new route.
- `apps/web/src/routes/project.tsx` — wrap ChatPanel in ProjectShell with tabs.

---

## Phase A — Backend

### Task 1: Pydantic models for the graph response

**Files:**
- Create: `packages/atlas-knowledge/atlas_knowledge/models/graph.py`

- [ ] **Step 1: Write the file**

```python
"""Knowledge-graph response models for the Plan 5 explorer endpoint."""

from __future__ import annotations

from typing import Any, Literal
from uuid import UUID

from atlas_core.models.base import AtlasModel
from pydantic import Field

NodeType = Literal["Document", "Chunk", "Entity"]
GraphMode = Literal["top_entities", "search", "expand"]


class GraphNode(AtlasModel):
    id: UUID
    type: NodeType
    label: str
    pagerank: float | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class GraphEdge(AtlasModel):
    id: str  # not a UUID — Neo4j relationships have integer ids; we stringify them
    source: UUID
    target: UUID
    type: str  # e.g. "HAS_CHUNK", "MENTIONS", "REFERENCES"


class GraphMeta(AtlasModel):
    mode: GraphMode
    truncated: bool = False
    hit_node_ids: list[UUID] = Field(default_factory=list)
    degraded_stages: list[str] = Field(default_factory=list)


class GraphResponse(AtlasModel):
    nodes: list[GraphNode]
    edges: list[GraphEdge]
    meta: GraphMeta
```

- [ ] **Step 2: Confirm import works**

```bash
uv run python -c "from atlas_knowledge.models.graph import GraphNode, GraphResponse; print('ok')"
```

Expected output: `ok`

- [ ] **Step 3: Commit**

```bash
git add packages/atlas-knowledge/atlas_knowledge/models/graph.py
git commit -m "feat(knowledge/models): add Plan 5 graph response models"
```

---

### Task 2: GraphStore.fetch_top_entities

**Files:**
- Modify: `packages/atlas-graph/atlas_graph/store.py`
- Create: `packages/atlas-graph/atlas_graph/tests/test_subgraph.py`

- [ ] **Step 1: Write the failing test**

Create `packages/atlas-graph/atlas_graph/tests/test_subgraph.py`:

```python
"""Cypher-shape tests for GraphStore.fetch_top_entities and fetch_subgraph_by_seeds."""
from __future__ import annotations

from uuid import uuid4

import pytest

from atlas_graph.store import GraphStore


@pytest.mark.asyncio
async def test_fetch_top_entities_runs_one_read_with_pid_and_limit(fake_async_driver):
    store = GraphStore(fake_async_driver)  # type: ignore[arg-type]
    pid = uuid4()
    await store.fetch_top_entities(project_id=pid, limit=15)

    queries = [c.query for c in fake_async_driver.calls]
    assert any("Entity" in q for q in queries)
    assert any("pagerank" in q.lower() for q in queries)
    # The pid is passed in
    assert any(c.kwargs.get("pid") == str(pid) for c in fake_async_driver.calls)
    # The limit is passed in
    assert any(c.kwargs.get("limit") == 15 for c in fake_async_driver.calls)


@pytest.mark.asyncio
async def test_fetch_top_entities_returns_nodes_edges_pair(fake_async_driver):
    store = GraphStore(fake_async_driver)  # type: ignore[arg-type]
    nodes, edges = await store.fetch_top_entities(project_id=uuid4(), limit=10)
    # Fake driver returns empty result; assert the shape, not content.
    assert isinstance(nodes, list)
    assert isinstance(edges, list)
```

- [ ] **Step 2: Run the test (fail)**

```bash
uv run pytest packages/atlas-graph/atlas_graph/tests/test_subgraph.py -v
```

Expected: both tests FAIL with `AttributeError: 'GraphStore' object has no attribute 'fetch_top_entities'`.

- [ ] **Step 3: Add the Cypher constants**

Add to the top of `packages/atlas-graph/atlas_graph/store.py` (after existing imports, before `class GraphStore`):

```python
# Plan 5 — UI subgraph fetches.
TOP_ENTITIES_CYPHER = """
MATCH (e:Entity {project_id: $pid})
RETURN e.id AS id, e.label AS label, e.entity_type AS entity_type,
       coalesce(e.pagerank_global, 0.0) AS pagerank,
       coalesce(e.mention_count, 0) AS mention_count
ORDER BY pagerank DESC
LIMIT $limit
"""

TOP_ENTITIES_EDGES_CYPHER = """
UNWIND $ids AS aid
MATCH (a:Entity {id: aid})-[r]-(b:Entity)
WHERE b.id IN $ids AND id(a) < id(b)
RETURN id(r) AS rid, a.id AS source, b.id AS target, type(r) AS type
"""
```

- [ ] **Step 4: Implement `fetch_top_entities`**

Add this method to the `GraphStore` class in `store.py` (place it just after `expand_chunks`):

```python
async def fetch_top_entities(
    self,
    *,
    project_id: UUID,
    limit: int = 30,
) -> tuple[list[dict], list[dict]]:
    """Top-N entities by PageRank for the project, plus edges between them.

    Returns ``(nodes, edges)``. Each node is a dict with keys
    ``id, type, label, pagerank, metadata``. Each edge has
    ``id, source, target, type``. UUIDs are returned as strings — the
    router converts them to Pydantic models.
    """
    async def _read(tx):
        node_result = await tx.run(
            TOP_ENTITIES_CYPHER, pid=str(project_id), limit=int(limit)
        )
        nodes_raw = await node_result.data()
        if not nodes_raw:
            return [], []
        ids = [r["id"] for r in nodes_raw]
        edge_result = await tx.run(TOP_ENTITIES_EDGES_CYPHER, ids=ids)
        edges_raw = await edge_result.data()
        return nodes_raw, edges_raw

    async with self._session() as s:
        nodes_raw, edges_raw = await s.execute_read(_read)

    nodes = [
        {
            "id": r["id"],
            "type": "Entity",
            "label": r["label"],
            "pagerank": float(r["pagerank"]),
            "metadata": {
                "entity_type": r.get("entity_type"),
                "mention_count": int(r.get("mention_count") or 0),
            },
        }
        for r in nodes_raw
    ]
    edges = [
        {
            "id": str(r["rid"]),
            "source": r["source"],
            "target": r["target"],
            "type": r["type"],
        }
        for r in edges_raw
    ]
    return nodes, edges
```

- [ ] **Step 5: Run the tests (pass)**

```bash
uv run pytest packages/atlas-graph/atlas_graph/tests/test_subgraph.py -v
```

Expected: both tests PASS.

- [ ] **Step 6: Commit**

```bash
git add packages/atlas-graph/atlas_graph/store.py packages/atlas-graph/atlas_graph/tests/test_subgraph.py
git commit -m "feat(graph): GraphStore.fetch_top_entities for Plan 5 overview mode"
```

---

### Task 3: GraphStore.fetch_subgraph_by_seeds

**Files:**
- Modify: `packages/atlas-graph/atlas_graph/store.py`
- Modify: `packages/atlas-graph/atlas_graph/tests/test_subgraph.py`

- [ ] **Step 1: Write the failing tests**

Append to `packages/atlas-graph/atlas_graph/tests/test_subgraph.py`:

```python
@pytest.mark.asyncio
async def test_fetch_subgraph_by_seeds_runs_one_read_with_seeds(fake_async_driver):
    store = GraphStore(fake_async_driver)  # type: ignore[arg-type]
    pid = uuid4()
    seeds = [uuid4(), uuid4()]
    await store.fetch_subgraph_by_seeds(
        project_id=pid, seed_ids=seeds, neighbors_per_seed=25
    )

    seed_strs = [str(s) for s in seeds]
    assert any(c.kwargs.get("seeds") == seed_strs for c in fake_async_driver.calls)
    assert any(c.kwargs.get("pid") == str(pid) for c in fake_async_driver.calls)
    assert any(c.kwargs.get("cap") == 25 for c in fake_async_driver.calls)


@pytest.mark.asyncio
async def test_fetch_subgraph_by_seeds_empty_seeds_short_circuits(fake_async_driver):
    store = GraphStore(fake_async_driver)  # type: ignore[arg-type]
    nodes, edges = await store.fetch_subgraph_by_seeds(
        project_id=uuid4(), seed_ids=[], neighbors_per_seed=25
    )
    assert nodes == []
    assert edges == []
    assert fake_async_driver.calls == []  # no Cypher run
```

- [ ] **Step 2: Run the tests (fail)**

```bash
uv run pytest packages/atlas-graph/atlas_graph/tests/test_subgraph.py -v
```

Expected: the two new tests FAIL.

- [ ] **Step 3: Add the Cypher constant**

Add to `packages/atlas-graph/atlas_graph/store.py` near the other Plan 5 constants:

```python
# Plan 5 — 1-hop expansion of arbitrary node ids, capped per seed via subquery.
SUBGRAPH_CYPHER = """
MATCH (s) WHERE s.id IN $seeds AND s.project_id = $pid
WITH collect(DISTINCT s) AS seedNodes, $cap AS cap
UNWIND seedNodes AS s
CALL {
  WITH s, cap
  MATCH (s)-[r]-(n)
  WHERE n.project_id = s.project_id
  RETURN r, n
  LIMIT cap
}
WITH seedNodes, collect(DISTINCT {r: r, n: n}) AS hits
WITH seedNodes, hits,
     [x IN hits | x.n] + seedNodes AS allNodes,
     [x IN hits | x.r] AS allRels
UNWIND allNodes AS node
WITH DISTINCT node, allRels
RETURN
  node.id AS id,
  labels(node)[0] AS type,
  coalesce(node.label, node.title, node.text, "") AS label,
  node.pagerank_global AS pagerank,
  properties(node) AS props,
  allRels AS rels
"""
```

Note: this query returns one row per node plus `rels` repeated. We dedupe relationships in Python.

- [ ] **Step 4: Implement `fetch_subgraph_by_seeds`**

Add to `GraphStore` (just after `fetch_top_entities`):

```python
async def fetch_subgraph_by_seeds(
    self,
    *,
    project_id: UUID,
    seed_ids: list[UUID],
    neighbors_per_seed: int = 25,
) -> tuple[list[dict], list[dict]]:
    """1-hop expansion of arbitrary nodes by id.

    Returns nodes (full dicts with id/type/label/metadata) and
    deduped edges. Per-seed neighbor cap prevents one high-degree
    seed from starving the others.
    """
    if not seed_ids:
        return [], []

    seed_strs = [str(s) for s in seed_ids]

    async def _read(tx):
        result = await tx.run(
            SUBGRAPH_CYPHER, seeds=seed_strs, pid=str(project_id), cap=int(neighbors_per_seed)
        )
        return await result.data()

    async with self._session() as s:
        rows = await s.execute_read(_read)

    nodes: dict[str, dict] = {}
    edges: dict[str, dict] = {}
    for row in rows:
        node_id = row["id"]
        if node_id not in nodes:
            nodes[node_id] = {
                "id": node_id,
                "type": row["type"],
                "label": row["label"],
                "pagerank": float(row["pagerank"]) if row["pagerank"] is not None else None,
                "metadata": _project_node_metadata(row["type"], row["props"]),
            }
        for rel in row["rels"]:
            if rel is None:
                continue
            rel_id = str(rel.element_id) if hasattr(rel, "element_id") else str(rel.id)
            if rel_id not in edges:
                edges[rel_id] = {
                    "id": rel_id,
                    "source": rel.start_node["id"],
                    "target": rel.end_node["id"],
                    "type": rel.type,
                }

    return list(nodes.values()), list(edges.values())
```

And add this helper at module scope (near `_serialize_metadata`):

```python
def _project_node_metadata(node_type: str, props: dict) -> dict:
    """Strip large/internal fields and project per-type metadata for the UI."""
    if node_type == "Document":
        return {
            "title": props.get("title") or props.get("label"),
            "source_type": props.get("source_type"),
            "source_url": props.get("source_url"),
        }
    if node_type == "Chunk":
        text = props.get("text") or ""
        return {
            "document_id": props.get("document_id"),
            "chunk_index": props.get("chunk_index"),
            "text_preview": text[:200],
        }
    if node_type == "Entity":
        return {
            "entity_type": props.get("entity_type"),
            "mention_count": int(props.get("mention_count") or 0),
        }
    return {}
```

- [ ] **Step 5: Run the tests (pass)**

```bash
uv run pytest packages/atlas-graph/atlas_graph/tests/test_subgraph.py -v
```

Expected: all 4 tests in the file PASS.

- [ ] **Step 6: Commit**

```bash
git add packages/atlas-graph/atlas_graph/store.py packages/atlas-graph/atlas_graph/tests/test_subgraph.py
git commit -m "feat(graph): GraphStore.fetch_subgraph_by_seeds with per-seed neighbor cap"
```

---

### Task 4: Knowledge graph endpoint — top-entities mode

**Files:**
- Create: `apps/api/atlas_api/tests/test_knowledge_graph_endpoint.py`
- Modify: `apps/api/atlas_api/routers/knowledge.py`

- [ ] **Step 1: Write the failing test**

Create `apps/api/atlas_api/tests/test_knowledge_graph_endpoint.py`:

```python
"""Integration tests for GET /api/v1/knowledge/graph (Plan 5)."""

from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
from atlas_core.db.orm import ProjectORM

from atlas_api.deps import get_graph_store, get_retriever
from atlas_api.main import app


@pytest.fixture
def fake_graph_store():
    store = AsyncMock()
    store.fetch_top_entities.return_value = (
        [
            {
                "id": "11111111-1111-1111-1111-111111111111",
                "type": "Entity",
                "label": "Llama 3",
                "pagerank": 0.5,
                "metadata": {"entity_type": "PRODUCT", "mention_count": 3},
            },
        ],
        [],
    )
    store.fetch_subgraph_by_seeds.return_value = ([], [])
    return store


@pytest.fixture
def app_with_graph_overrides(app_client, fake_graph_store):
    app.dependency_overrides[get_graph_store] = lambda: fake_graph_store
    yield app_client
    app.dependency_overrides.pop(get_graph_store, None)


@pytest.mark.asyncio
async def test_top_entities_mode_returns_entities_when_no_query_or_seeds(
    app_with_graph_overrides, db_session, fake_graph_store
):
    project = ProjectORM(user_id="matt", name="P", default_model="claude-sonnet-4-6")
    db_session.add(project)
    await db_session.flush()

    resp = await app_with_graph_overrides.get(
        "/api/v1/knowledge/graph",
        params={"project_id": str(project.id)},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["meta"]["mode"] == "top_entities"
    assert body["meta"]["truncated"] is False
    assert len(body["nodes"]) == 1
    assert body["nodes"][0]["label"] == "Llama 3"
    fake_graph_store.fetch_top_entities.assert_called_once()
    fake_graph_store.fetch_subgraph_by_seeds.assert_not_called()


@pytest.mark.asyncio
async def test_unknown_project_returns_404(app_with_graph_overrides):
    resp = await app_with_graph_overrides.get(
        "/api/v1/knowledge/graph",
        params={"project_id": str(uuid4())},
    )
    assert resp.status_code == 404
```

- [ ] **Step 2: Run the tests (fail)**

```bash
uv run pytest apps/api/atlas_api/tests/test_knowledge_graph_endpoint.py -v
```

Expected: tests FAIL (404 because endpoint doesn't exist).

- [ ] **Step 3: Add the endpoint**

In `apps/api/atlas_api/routers/knowledge.py`:

Add this import at the top of the file (with the other `atlas_knowledge.models.*` imports):

```python
from atlas_knowledge.models.graph import (
    GraphEdge,
    GraphMeta,
    GraphNode,
    GraphResponse,
)
```

Add this import (with the other deps imports):

```python
from atlas_api.deps import get_graph_store
from atlas_graph import GraphStore
```

Update the module docstring to mention the new route. Append at the very end of the file:

```python
# --- Graph (explorer) ----------------------------------------------------


def _to_graph_node(raw: dict) -> GraphNode:
    return GraphNode(
        id=UUID(raw["id"]),
        type=raw["type"],
        label=raw["label"] or "",
        pagerank=raw.get("pagerank"),
        metadata=raw.get("metadata") or {},
    )


def _to_graph_edge(raw: dict) -> GraphEdge:
    return GraphEdge(
        id=raw["id"],
        source=UUID(raw["source"]),
        target=UUID(raw["target"]),
        type=raw["type"],
    )


@router.get("/knowledge/graph", response_model=GraphResponse)
async def get_knowledge_graph(
    project_id: UUID,
    q: str | None = None,
    seed_chunk_ids: str | None = None,
    seed_node_ids: str | None = None,
    node_types: str | None = None,
    limit: int | None = None,
    db: AsyncSession = Depends(get_session),
    graph_store: GraphStore = Depends(get_graph_store),
    retriever: Retriever = Depends(get_retriever),
) -> GraphResponse:
    """Return a subgraph of the project's knowledge graph for visualization.

    Modes (priority: q > seed_node_ids > seed_chunk_ids > none):
      - search: q is set → run hybrid retriever, expand chunk hits 1-hop.
      - expand: seed_*_ids set → 1-hop expansion of those seeds.
      - top_entities: none of the above → top-N entities by PageRank.
    """
    project = await db.get(ProjectORM, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="project not found")

    valid_types = {"Document", "Chunk", "Entity"}
    types_filter: set[str] | None = None
    if node_types:
        types_filter = {t.strip() for t in node_types.split(",") if t.strip()}
        unknown = types_filter - valid_types
        if unknown:
            raise HTTPException(
                status_code=422,
                detail=f"unknown node_types: {sorted(unknown)}",
            )

    # Mode discrimination — top_entities only for Task 4.
    if q is None and not seed_node_ids and not seed_chunk_ids:
        cap = limit if limit is not None else 30
        cap = min(cap, 200)
        nodes_raw, edges_raw = await graph_store.fetch_top_entities(
            project_id=project_id, limit=cap
        )
        truncated = len(nodes_raw) >= cap
        nodes = [_to_graph_node(n) for n in nodes_raw]
        if types_filter:
            nodes = [n for n in nodes if n.type in types_filter]
            kept = {n.id for n in nodes}
            edges = [_to_graph_edge(e) for e in edges_raw if UUID(e["source"]) in kept and UUID(e["target"]) in kept]
        else:
            edges = [_to_graph_edge(e) for e in edges_raw]
        return GraphResponse(
            nodes=nodes,
            edges=edges,
            meta=GraphMeta(mode="top_entities", truncated=truncated),
        )

    # search and expand modes implemented in subsequent tasks.
    raise HTTPException(status_code=501, detail="not implemented yet")
```

- [ ] **Step 4: Run the tests (pass)**

```bash
uv run pytest apps/api/atlas_api/tests/test_knowledge_graph_endpoint.py -v
```

Expected: both tests PASS.

- [ ] **Step 5: Commit**

```bash
git add apps/api/atlas_api/routers/knowledge.py apps/api/atlas_api/tests/test_knowledge_graph_endpoint.py
git commit -m "feat(api/knowledge): GET /knowledge/graph — top_entities mode"
```

---

### Task 5: Endpoint — expand mode

**Files:**
- Modify: `apps/api/atlas_api/routers/knowledge.py`
- Modify: `apps/api/atlas_api/tests/test_knowledge_graph_endpoint.py`

- [ ] **Step 1: Write the failing tests**

Append to `apps/api/atlas_api/tests/test_knowledge_graph_endpoint.py`:

```python
@pytest.mark.asyncio
async def test_expand_mode_via_seed_node_ids(
    app_with_graph_overrides, db_session, fake_graph_store
):
    project = ProjectORM(user_id="matt", name="P", default_model="claude-sonnet-4-6")
    db_session.add(project)
    await db_session.flush()
    seed = uuid4()
    fake_graph_store.fetch_subgraph_by_seeds.return_value = (
        [
            {
                "id": str(seed),
                "type": "Entity",
                "label": "Seed",
                "pagerank": 0.0,
                "metadata": {},
            },
            {
                "id": "22222222-2222-2222-2222-222222222222",
                "type": "Chunk",
                "label": "neighbor chunk",
                "pagerank": None,
                "metadata": {"document_id": str(uuid4()), "chunk_index": 0, "text_preview": "..."},
            },
        ],
        [
            {
                "id": "rel-1",
                "source": str(seed),
                "target": "22222222-2222-2222-2222-222222222222",
                "type": "MENTIONS",
            },
        ],
    )

    resp = await app_with_graph_overrides.get(
        "/api/v1/knowledge/graph",
        params={"project_id": str(project.id), "seed_node_ids": str(seed)},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["meta"]["mode"] == "expand"
    assert len(body["nodes"]) == 2
    assert len(body["edges"]) == 1
    fake_graph_store.fetch_subgraph_by_seeds.assert_called_once()
    args, kwargs = fake_graph_store.fetch_subgraph_by_seeds.call_args
    assert kwargs["seed_ids"] == [seed]


@pytest.mark.asyncio
async def test_expand_mode_priority_node_ids_over_chunk_ids(
    app_with_graph_overrides, db_session, fake_graph_store
):
    project = ProjectORM(user_id="matt", name="P", default_model="claude-sonnet-4-6")
    db_session.add(project)
    await db_session.flush()
    fake_graph_store.fetch_subgraph_by_seeds.return_value = ([], [])

    node_seed = uuid4()
    chunk_seed = uuid4()
    resp = await app_with_graph_overrides.get(
        "/api/v1/knowledge/graph",
        params={
            "project_id": str(project.id),
            "seed_node_ids": str(node_seed),
            "seed_chunk_ids": str(chunk_seed),
        },
    )
    assert resp.status_code == 200
    args, kwargs = fake_graph_store.fetch_subgraph_by_seeds.call_args
    # node_seed wins over chunk_seed.
    assert kwargs["seed_ids"] == [node_seed]


@pytest.mark.asyncio
async def test_node_types_filter_excludes_unwanted_types(
    app_with_graph_overrides, db_session, fake_graph_store
):
    project = ProjectORM(user_id="matt", name="P", default_model="claude-sonnet-4-6")
    db_session.add(project)
    await db_session.flush()

    fake_graph_store.fetch_subgraph_by_seeds.return_value = (
        [
            {"id": str(uuid4()), "type": "Entity", "label": "E", "pagerank": 0.0, "metadata": {}},
            {"id": str(uuid4()), "type": "Chunk", "label": "C", "pagerank": None, "metadata": {}},
        ],
        [],
    )

    resp = await app_with_graph_overrides.get(
        "/api/v1/knowledge/graph",
        params={
            "project_id": str(project.id),
            "seed_node_ids": str(uuid4()),
            "node_types": "Entity",
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert all(n["type"] == "Entity" for n in body["nodes"])


@pytest.mark.asyncio
async def test_unknown_node_types_returns_422(app_with_graph_overrides, db_session):
    project = ProjectORM(user_id="matt", name="P", default_model="claude-sonnet-4-6")
    db_session.add(project)
    await db_session.flush()

    resp = await app_with_graph_overrides.get(
        "/api/v1/knowledge/graph",
        params={"project_id": str(project.id), "node_types": "Bogus"},
    )
    assert resp.status_code == 422
```

- [ ] **Step 2: Run the tests (fail)**

```bash
uv run pytest apps/api/atlas_api/tests/test_knowledge_graph_endpoint.py -v
```

Expected: the four new tests FAIL with `501 Not Implemented` for expand-mode tests; 422 test should pass already (it tests an existing branch).

- [ ] **Step 3: Implement expand mode**

Replace the `# search and expand modes implemented in subsequent tasks.` block in `get_knowledge_graph` with:

```python
    # Expand mode — seed_node_ids beats seed_chunk_ids.
    if seed_node_ids or seed_chunk_ids:
        raw_seeds = seed_node_ids or seed_chunk_ids or ""
        try:
            seed_uuids = [UUID(s.strip()) for s in raw_seeds.split(",") if s.strip()]
        except ValueError:
            raise HTTPException(status_code=422, detail="invalid seed UUID")
        cap = limit if limit is not None else 50
        cap = min(cap, 200)
        nodes_raw, edges_raw = await graph_store.fetch_subgraph_by_seeds(
            project_id=project_id,
            seed_ids=seed_uuids,
            neighbors_per_seed=25,
        )
        return _build_graph_response(
            mode="expand",
            nodes_raw=nodes_raw,
            edges_raw=edges_raw,
            cap=cap,
            types_filter=types_filter,
        )

    # search mode implemented in next task.
    raise HTTPException(status_code=501, detail="search mode not implemented yet")
```

And add this helper just above `get_knowledge_graph`:

```python
def _build_graph_response(
    *,
    mode: str,
    nodes_raw: list[dict],
    edges_raw: list[dict],
    cap: int,
    types_filter: set[str] | None,
    hit_node_ids: list[UUID] | None = None,
    degraded_stages: list[str] | None = None,
) -> GraphResponse:
    truncated = len(nodes_raw) > cap
    nodes_raw = nodes_raw[:cap]
    nodes = [_to_graph_node(n) for n in nodes_raw]
    if types_filter:
        nodes = [n for n in nodes if n.type in types_filter]
    kept = {n.id for n in nodes}
    edges = [
        _to_graph_edge(e)
        for e in edges_raw
        if UUID(e["source"]) in kept and UUID(e["target"]) in kept
    ]
    return GraphResponse(
        nodes=nodes,
        edges=edges,
        meta=GraphMeta(
            mode=mode,
            truncated=truncated,
            hit_node_ids=hit_node_ids or [],
            degraded_stages=degraded_stages or [],
        ),
    )
```

Also, refactor the top-entities branch to use this helper:

```python
    # Top-entities mode (default).
    if q is None and not seed_node_ids and not seed_chunk_ids:
        cap = limit if limit is not None else 30
        cap = min(cap, 200)
        nodes_raw, edges_raw = await graph_store.fetch_top_entities(
            project_id=project_id, limit=cap
        )
        return _build_graph_response(
            mode="top_entities",
            nodes_raw=nodes_raw,
            edges_raw=edges_raw,
            cap=cap,
            types_filter=types_filter,
        )
```

- [ ] **Step 4: Run the tests (pass)**

```bash
uv run pytest apps/api/atlas_api/tests/test_knowledge_graph_endpoint.py -v
```

Expected: all expand-mode tests PASS; existing top_entities + 422 + 404 tests still PASS.

- [ ] **Step 5: Commit**

```bash
git add apps/api/atlas_api/routers/knowledge.py apps/api/atlas_api/tests/test_knowledge_graph_endpoint.py
git commit -m "feat(api/knowledge): /knowledge/graph — expand mode + node_types filter"
```

---

### Task 6: Endpoint — search mode

**Files:**
- Modify: `apps/api/atlas_api/routers/knowledge.py`
- Modify: `apps/api/atlas_api/tests/test_knowledge_graph_endpoint.py`

- [ ] **Step 1: Write the failing tests**

Append to `apps/api/atlas_api/tests/test_knowledge_graph_endpoint.py`:

```python
@pytest.mark.asyncio
async def test_search_mode_calls_retriever_then_expands_chunk_hits(
    app_with_graph_overrides, db_session, fake_graph_store
):
    from atlas_knowledge.models.nodes import KnowledgeNode
    from atlas_knowledge.models.retrieval import RetrievalResult, ScoredChunk

    project = ProjectORM(user_id="matt", name="P", default_model="claude-sonnet-4-6")
    db_session.add(project)
    await db_session.flush()

    chunk_id = uuid4()
    fake_retriever = AsyncMock()
    fake_retriever.retrieve.return_value = RetrievalResult(
        query="hello",
        chunks=[
            ScoredChunk(
                chunk=KnowledgeNode(
                    id=chunk_id,
                    project_id=project.id,
                    type="chunk",
                    title="c",
                    content="hello world",
                    metadata={},
                ),
                score=0.9,
            )
        ],
    )

    fake_graph_store.fetch_subgraph_by_seeds.return_value = (
        [
            {"id": str(chunk_id), "type": "Chunk", "label": "hello world", "pagerank": None, "metadata": {}},
        ],
        [],
    )

    app.dependency_overrides[get_retriever] = lambda: fake_retriever
    try:
        resp = await app_with_graph_overrides.get(
            "/api/v1/knowledge/graph",
            params={"project_id": str(project.id), "q": "hello"},
        )
    finally:
        app.dependency_overrides.pop(get_retriever, None)

    assert resp.status_code == 200
    body = resp.json()
    assert body["meta"]["mode"] == "search"
    assert body["meta"]["hit_node_ids"] == [str(chunk_id)]
    assert len(body["nodes"]) == 1
    fake_retriever.retrieve.assert_awaited_once()
    fake_graph_store.fetch_subgraph_by_seeds.assert_called_once()


@pytest.mark.asyncio
async def test_search_mode_priority_q_over_seeds(
    app_with_graph_overrides, db_session, fake_graph_store
):
    """When q AND seeds are both set, q wins."""
    from atlas_knowledge.models.retrieval import RetrievalResult

    project = ProjectORM(user_id="matt", name="P", default_model="claude-sonnet-4-6")
    db_session.add(project)
    await db_session.flush()

    fake_retriever = AsyncMock()
    fake_retriever.retrieve.return_value = RetrievalResult(query="x", chunks=[])
    fake_graph_store.fetch_subgraph_by_seeds.return_value = ([], [])

    app.dependency_overrides[get_retriever] = lambda: fake_retriever
    try:
        resp = await app_with_graph_overrides.get(
            "/api/v1/knowledge/graph",
            params={
                "project_id": str(project.id),
                "q": "x",
                "seed_node_ids": str(uuid4()),
            },
        )
    finally:
        app.dependency_overrides.pop(get_retriever, None)

    assert resp.status_code == 200
    assert resp.json()["meta"]["mode"] == "search"
    fake_retriever.retrieve.assert_awaited_once()
```

- [ ] **Step 2: Run the tests (fail)**

```bash
uv run pytest apps/api/atlas_api/tests/test_knowledge_graph_endpoint.py -v
```

Expected: the two new tests FAIL with 501.

- [ ] **Step 3: Implement search mode**

Replace `raise HTTPException(status_code=501, detail="search mode not implemented yet")` with:

```python
    # Search mode — q wins, runs hybrid retrieval, expand chunk hits 1-hop.
    cap = limit if limit is not None else 50
    cap = min(cap, 200)
    retrieval = await retriever.retrieve(
        RetrievalQuery(project_id=project_id, text=q, top_k=10)
    )
    chunk_hits = [c.chunk.id for c in retrieval.chunks]
    if not chunk_hits:
        return GraphResponse(
            nodes=[],
            edges=[],
            meta=GraphMeta(mode="search", truncated=False, hit_node_ids=[]),
        )
    nodes_raw, edges_raw = await graph_store.fetch_subgraph_by_seeds(
        project_id=project_id,
        seed_ids=chunk_hits,
        neighbors_per_seed=25,
    )
    return _build_graph_response(
        mode="search",
        nodes_raw=nodes_raw,
        edges_raw=edges_raw,
        cap=cap,
        types_filter=types_filter,
        hit_node_ids=chunk_hits,
    )
```

Also reorder the mode discrimination so the `if q:` check comes BEFORE the expand and top-entities branches. Final order:

```python
    # 1. search mode (q wins)
    if q:
        ... # search code above
    # 2. expand mode (seed_*_ids)
    if seed_node_ids or seed_chunk_ids:
        ...
    # 3. top-entities (default)
    ...
```

Also confirm `RetrievalQuery` is imported at the top of the file (it already is — used by the existing `/knowledge/search` route).

- [ ] **Step 4: Run the tests (pass)**

```bash
uv run pytest apps/api/atlas_api/tests/test_knowledge_graph_endpoint.py -v
```

Expected: all tests PASS.

- [ ] **Step 5: Commit**

```bash
git add apps/api/atlas_api/routers/knowledge.py apps/api/atlas_api/tests/test_knowledge_graph_endpoint.py
git commit -m "feat(api/knowledge): /knowledge/graph — search mode via HybridRetriever"
```

---

### Task 7: Endpoint — degraded handling

**Files:**
- Modify: `apps/api/atlas_api/routers/knowledge.py`
- Modify: `apps/api/atlas_api/tests/test_knowledge_graph_endpoint.py`

- [ ] **Step 1: Write the failing tests**

Append to `apps/api/atlas_api/tests/test_knowledge_graph_endpoint.py`:

```python
@pytest.mark.asyncio
async def test_top_entities_returns_503_when_graph_unavailable(
    app_with_graph_overrides, db_session, fake_graph_store
):
    from atlas_graph.errors import GraphUnavailable

    project = ProjectORM(user_id="matt", name="P", default_model="claude-sonnet-4-6")
    db_session.add(project)
    await db_session.flush()

    fake_graph_store.fetch_top_entities.side_effect = GraphUnavailable("neo4j down")

    resp = await app_with_graph_overrides.get(
        "/api/v1/knowledge/graph",
        params={"project_id": str(project.id)},
    )
    assert resp.status_code == 503
    assert resp.json()["detail"] == "graph_unavailable"


@pytest.mark.asyncio
async def test_expand_returns_503_when_graph_unavailable(
    app_with_graph_overrides, db_session, fake_graph_store
):
    from atlas_graph.errors import GraphUnavailable

    project = ProjectORM(user_id="matt", name="P", default_model="claude-sonnet-4-6")
    db_session.add(project)
    await db_session.flush()

    fake_graph_store.fetch_subgraph_by_seeds.side_effect = GraphUnavailable("neo4j down")

    resp = await app_with_graph_overrides.get(
        "/api/v1/knowledge/graph",
        params={"project_id": str(project.id), "seed_node_ids": str(uuid4())},
    )
    assert resp.status_code == 503


@pytest.mark.asyncio
async def test_search_falls_back_to_chunks_only_when_graph_unavailable(
    app_with_graph_overrides, db_session, fake_graph_store
):
    from atlas_graph.errors import GraphUnavailable
    from atlas_knowledge.models.nodes import KnowledgeNode
    from atlas_knowledge.models.retrieval import RetrievalResult, ScoredChunk

    project = ProjectORM(user_id="matt", name="P", default_model="claude-sonnet-4-6")
    db_session.add(project)
    await db_session.flush()

    chunk_id = uuid4()
    fake_retriever = AsyncMock()
    fake_retriever.retrieve.return_value = RetrievalResult(
        query="x",
        chunks=[
            ScoredChunk(
                chunk=KnowledgeNode(
                    id=chunk_id, project_id=project.id, type="chunk",
                    title="c", content="x", metadata={},
                ),
                score=0.5,
            )
        ],
    )
    fake_graph_store.fetch_subgraph_by_seeds.side_effect = GraphUnavailable("neo4j down")

    app.dependency_overrides[get_retriever] = lambda: fake_retriever
    try:
        resp = await app_with_graph_overrides.get(
            "/api/v1/knowledge/graph",
            params={"project_id": str(project.id), "q": "x"},
        )
    finally:
        app.dependency_overrides.pop(get_retriever, None)

    assert resp.status_code == 200
    body = resp.json()
    assert body["meta"]["mode"] == "search"
    assert body["meta"]["degraded_stages"] == ["graph_unavailable"]
    assert body["edges"] == []
    # Hit chunk synthesized as a node so the UI has something to render.
    assert len(body["nodes"]) == 1
    assert body["nodes"][0]["id"] == str(chunk_id)
```

- [ ] **Step 2: Run the tests (fail)**

```bash
uv run pytest apps/api/atlas_api/tests/test_knowledge_graph_endpoint.py -v
```

Expected: the three new tests FAIL.

- [ ] **Step 3: Add degraded handling**

In `apps/api/atlas_api/routers/knowledge.py`:

Add the import:

```python
from atlas_graph.errors import GraphUnavailable
```

Wrap the `fetch_top_entities` call in the top-entities branch:

```python
    if q is None and not seed_node_ids and not seed_chunk_ids:
        cap = limit if limit is not None else 30
        cap = min(cap, 200)
        try:
            nodes_raw, edges_raw = await graph_store.fetch_top_entities(
                project_id=project_id, limit=cap
            )
        except GraphUnavailable as e:
            raise HTTPException(status_code=503, detail="graph_unavailable") from e
        return _build_graph_response(...)
```

Wrap the `fetch_subgraph_by_seeds` call in the expand branch with the same try/except.

For the search branch, the retriever call stays as-is (it has its own degradation). Only wrap the `fetch_subgraph_by_seeds` call:

```python
    chunk_hits = [c.chunk.id for c in retrieval.chunks]
    degraded: list[str] = []
    if not chunk_hits:
        return GraphResponse(...)
    try:
        nodes_raw, edges_raw = await graph_store.fetch_subgraph_by_seeds(
            project_id=project_id,
            seed_ids=chunk_hits,
            neighbors_per_seed=25,
        )
    except GraphUnavailable:
        # Fallback: synthesize chunk nodes from the retrieval result, no edges.
        nodes_raw = [
            {
                "id": str(c.chunk.id),
                "type": "Chunk",
                "label": (c.chunk.content or "")[:80],
                "pagerank": None,
                "metadata": {"text_preview": (c.chunk.content or "")[:200]},
            }
            for c in retrieval.chunks
        ]
        edges_raw = []
        degraded = ["graph_unavailable"]
    return _build_graph_response(
        mode="search",
        nodes_raw=nodes_raw,
        edges_raw=edges_raw,
        cap=cap,
        types_filter=types_filter,
        hit_node_ids=chunk_hits,
        degraded_stages=degraded,
    )
```

- [ ] **Step 4: Run the tests (pass)**

```bash
uv run pytest apps/api/atlas_api/tests/test_knowledge_graph_endpoint.py -v
```

Expected: all tests PASS.

- [ ] **Step 5: Commit**

```bash
git add apps/api/atlas_api/routers/knowledge.py apps/api/atlas_api/tests/test_knowledge_graph_endpoint.py
git commit -m "feat(api/knowledge): /knowledge/graph — graceful degradation when Neo4j unavailable"
```

---

### Task 8: Real-Neo4j acceptance test

**Files:**
- Create: `packages/atlas-graph/atlas_graph/tests/test_subgraph_integration.py`

- [ ] **Step 1: Write the test**

```python
"""Opt-in real-Neo4j acceptance test for Plan 5 subgraph fetches.

Run with: ATLAS_RUN_NEO4J_INTEGRATION=1 uv run pytest -m slow ...
"""
from __future__ import annotations

import os
from uuid import uuid4

import pytest
import pytest_asyncio


pytestmark = pytest.mark.skipif(
    os.getenv("ATLAS_RUN_NEO4J_INTEGRATION") != "1",
    reason="set ATLAS_RUN_NEO4J_INTEGRATION=1 to enable",
)


@pytest_asyncio.fixture
async def seeded_project(real_graph_store, isolated_project_id):
    """Tiny project: 1 doc, 2 chunks, 3 entities. Mentions edges between them."""
    pid = isolated_project_id
    doc_id = uuid4()
    chunk_a, chunk_b = uuid4(), uuid4()
    ent1, ent2, ent3 = uuid4(), uuid4(), uuid4()

    async with real_graph_store._driver.session() as s:
        await s.run(
            """
            CREATE (d:Document {id: $doc, project_id: $pid, title: "Doc", source_type: "markdown"})
            CREATE (c1:Chunk {id: $c1, project_id: $pid, document_id: $doc, chunk_index: 0, text: "alpha"})
            CREATE (c2:Chunk {id: $c2, project_id: $pid, document_id: $doc, chunk_index: 1, text: "beta"})
            CREATE (e1:Entity {id: $e1, project_id: $pid, label: "E1", entity_type: "PERSON",
                              pagerank_global: 0.5, mention_count: 2})
            CREATE (e2:Entity {id: $e2, project_id: $pid, label: "E2", entity_type: "ORG",
                              pagerank_global: 0.3, mention_count: 1})
            CREATE (e3:Entity {id: $e3, project_id: $pid, label: "E3", entity_type: "CONCEPT",
                              pagerank_global: 0.1, mention_count: 1})
            CREATE (d)-[:HAS_CHUNK]->(c1)
            CREATE (d)-[:HAS_CHUNK]->(c2)
            CREATE (c1)-[:MENTIONS]->(e1)
            CREATE (c1)-[:MENTIONS]->(e2)
            CREATE (c2)-[:MENTIONS]->(e2)
            CREATE (c2)-[:MENTIONS]->(e3)
            CREATE (e1)-[:RELATED_TO]->(e2)
            """,
            doc=str(doc_id), pid=str(pid),
            c1=str(chunk_a), c2=str(chunk_b),
            e1=str(ent1), e2=str(ent2), e3=str(ent3),
        )
    return {
        "pid": pid,
        "doc_id": doc_id,
        "chunks": [chunk_a, chunk_b],
        "entities": [ent1, ent2, ent3],
    }


@pytest.mark.asyncio
@pytest.mark.slow
async def test_fetch_top_entities_returns_entities_sorted_by_pagerank(
    real_graph_store, seeded_project
):
    nodes, edges = await real_graph_store.fetch_top_entities(
        project_id=seeded_project["pid"], limit=10
    )
    assert len(nodes) == 3
    assert all(n["type"] == "Entity" for n in nodes)
    pageranks = [n["pagerank"] for n in nodes]
    assert pageranks == sorted(pageranks, reverse=True)
    # E1-E2 RELATED_TO edge is between two top entities
    assert any(e["type"] == "RELATED_TO" for e in edges)


@pytest.mark.asyncio
@pytest.mark.slow
async def test_fetch_subgraph_by_seeds_expands_one_hop(
    real_graph_store, seeded_project
):
    chunk_a = seeded_project["chunks"][0]
    nodes, edges = await real_graph_store.fetch_subgraph_by_seeds(
        project_id=seeded_project["pid"],
        seed_ids=[chunk_a],
        neighbors_per_seed=25,
    )
    node_ids = {n["id"] for n in nodes}
    # The seed is included.
    assert str(chunk_a) in node_ids
    # 1-hop: doc + 2 entities mentioned by chunk_a.
    assert len(nodes) >= 4
    # MENTIONS and HAS_CHUNK edges present.
    edge_types = {e["type"] for e in edges}
    assert "MENTIONS" in edge_types
    assert "HAS_CHUNK" in edge_types
```

- [ ] **Step 2: Run the test (skipped without env, passes with it)**

```bash
uv run pytest packages/atlas-graph/atlas_graph/tests/test_subgraph_integration.py -v
```

Expected without env: SKIPPED.

If you have a local Neo4j running:

```bash
ATLAS_RUN_NEO4J_INTEGRATION=1 uv run pytest packages/atlas-graph/atlas_graph/tests/test_subgraph_integration.py -v -m slow
```

Expected: 2 PASSED.

- [ ] **Step 3: Commit**

```bash
git add packages/atlas-graph/atlas_graph/tests/test_subgraph_integration.py
git commit -m "test(graph): real-Neo4j acceptance for Plan 5 subgraph fetches"
```

---

## Phase B — Frontend foundation

### Task 9: Frontend deps + TS types + API client

**Files:**
- Modify: `apps/web/package.json`
- Create: `apps/web/src/lib/api/knowledge-graph.ts`

- [ ] **Step 1: Install deps**

```bash
cd apps/web && pnpm add cytoscape cytoscape-fcose && pnpm add -D @types/cytoscape
```

Expected: `package.json` and `pnpm-lock.yaml` updated; no errors.

- [ ] **Step 2: Create the API client**

`apps/web/src/lib/api/knowledge-graph.ts`:

```ts
export type NodeType = "Document" | "Chunk" | "Entity";
export type GraphMode = "top_entities" | "search" | "expand";

export interface GraphNode {
  id: string;
  type: NodeType;
  label: string;
  pagerank: number | null;
  metadata: Record<string, unknown>;
}

export interface GraphEdge {
  id: string;
  source: string;
  target: string;
  type: string;
}

export interface GraphMeta {
  mode: GraphMode;
  truncated: boolean;
  hit_node_ids: string[];
  degraded_stages: string[];
}

export interface GraphResponse {
  nodes: GraphNode[];
  edges: GraphEdge[];
  meta: GraphMeta;
}

export interface FetchKnowledgeGraphArgs {
  projectId: string;
  q?: string;
  seedNodeIds?: string[];
  seedChunkIds?: string[];
  nodeTypes?: NodeType[];
  limit?: number;
}

export class KnowledgeGraphError extends Error {
  constructor(
    message: string,
    public readonly status: number,
    public readonly degraded?: boolean,
  ) {
    super(message);
    this.name = "KnowledgeGraphError";
  }
}

export async function fetchKnowledgeGraph(
  args: FetchKnowledgeGraphArgs,
  signal?: AbortSignal,
): Promise<GraphResponse> {
  const params = new URLSearchParams({ project_id: args.projectId });
  if (args.q) params.set("q", args.q);
  if (args.seedNodeIds?.length) params.set("seed_node_ids", args.seedNodeIds.join(","));
  if (args.seedChunkIds?.length) params.set("seed_chunk_ids", args.seedChunkIds.join(","));
  if (args.nodeTypes?.length) params.set("node_types", args.nodeTypes.join(","));
  if (args.limit !== undefined) params.set("limit", String(args.limit));

  const resp = await fetch(`/api/v1/knowledge/graph?${params}`, { signal });
  if (resp.status === 503) {
    throw new KnowledgeGraphError("graph_unavailable", 503, true);
  }
  if (!resp.ok) {
    const detail = await resp.text();
    throw new KnowledgeGraphError(detail || resp.statusText, resp.status);
  }
  return resp.json();
}
```

- [ ] **Step 3: Type-check passes**

```bash
cd apps/web && pnpm typecheck
```

Expected: 0 errors.

- [ ] **Step 4: Commit**

```bash
git add apps/web/package.json apps/web/pnpm-lock.yaml apps/web/src/lib/api/knowledge-graph.ts
git commit -m "feat(web/explorer): add cytoscape deps and knowledge-graph API client"
```

---

### Task 10: Zustand store + tests

**Files:**
- Create: `apps/web/src/stores/explorer-store.ts`
- Create: `apps/web/src/stores/explorer-store.test.ts`

- [ ] **Step 1: Write the failing tests**

`apps/web/src/stores/explorer-store.test.ts`:

```ts
import { describe, expect, it, beforeEach } from "vitest";
import type { GraphResponse } from "@/lib/api/knowledge-graph";
import { useExplorerStore } from "./explorer-store";

const mkResponse = (overrides: Partial<GraphResponse> = {}): GraphResponse => ({
  nodes: [],
  edges: [],
  meta: { mode: "top_entities", truncated: false, hit_node_ids: [], degraded_stages: [] },
  ...overrides,
});

describe("explorer-store", () => {
  beforeEach(() => {
    useExplorerStore.getState().reset();
  });

  it("replaceGraph swaps nodes/edges/meta", () => {
    useExplorerStore.getState().mergeGraph(
      mkResponse({ nodes: [{ id: "a", type: "Entity", label: "A", pagerank: 0.1, metadata: {} }] }),
    );
    useExplorerStore.getState().replaceGraph(
      mkResponse({
        nodes: [{ id: "b", type: "Entity", label: "B", pagerank: 0.2, metadata: {} }],
        meta: { mode: "search", truncated: false, hit_node_ids: ["b"], degraded_stages: [] },
      }),
    );
    const s = useExplorerStore.getState();
    expect(s.nodes.map((n) => n.id)).toEqual(["b"]);
    expect(s.hitNodeIds).toEqual(new Set(["b"]));
    expect(s.mode).toBe("search");
  });

  it("mergeGraph dedupes by id", () => {
    useExplorerStore.getState().mergeGraph(
      mkResponse({
        nodes: [{ id: "a", type: "Entity", label: "A", pagerank: 0.1, metadata: {} }],
      }),
    );
    useExplorerStore.getState().mergeGraph(
      mkResponse({
        nodes: [
          { id: "a", type: "Entity", label: "A2", pagerank: 0.5, metadata: {} },
          { id: "b", type: "Chunk", label: "B", pagerank: null, metadata: {} },
        ],
        meta: { mode: "expand", truncated: false, hit_node_ids: [], degraded_stages: [] },
      }),
    );
    const s = useExplorerStore.getState();
    expect(s.nodes.map((n) => n.id).sort()).toEqual(["a", "b"]);
    // Latest wins on dedupe.
    expect(s.nodes.find((n) => n.id === "a")?.pagerank).toBe(0.5);
  });

  it("toggleType flips set membership", () => {
    useExplorerStore.getState().toggleType("Chunk");
    expect(useExplorerStore.getState().visibleTypes.has("Chunk")).toBe(false);
    useExplorerStore.getState().toggleType("Chunk");
    expect(useExplorerStore.getState().visibleTypes.has("Chunk")).toBe(true);
  });

  it("selectNode sets selectedNodeId; passing null clears", () => {
    useExplorerStore.getState().selectNode("a");
    expect(useExplorerStore.getState().selectedNodeId).toBe("a");
    useExplorerStore.getState().selectNode(null);
    expect(useExplorerStore.getState().selectedNodeId).toBeNull();
  });
});
```

- [ ] **Step 2: Run the tests (fail)**

```bash
cd apps/web && pnpm test src/stores/explorer-store.test.ts
```

Expected: tests FAIL (file does not exist).

- [ ] **Step 3: Implement the store**

`apps/web/src/stores/explorer-store.ts`:

```ts
import { create } from "zustand";
import type {
  GraphEdge,
  GraphMode,
  GraphNode,
  GraphResponse,
  NodeType,
} from "@/lib/api/knowledge-graph";

interface ExplorerState {
  nodes: GraphNode[];
  edges: GraphEdge[];
  hitNodeIds: Set<string>;
  selectedNodeId: string | null;
  visibleTypes: Set<NodeType>;
  query: string;
  mode: GraphMode;
  loading: boolean;
  error: string | null;
  degradedStages: string[];
  truncated: boolean;

  replaceGraph: (response: GraphResponse) => void;
  mergeGraph: (response: GraphResponse) => void;
  selectNode: (id: string | null) => void;
  toggleType: (t: NodeType) => void;
  setQuery: (q: string) => void;
  setLoading: (loading: boolean) => void;
  setError: (error: string | null) => void;
  reset: () => void;
}

const ALL_TYPES: NodeType[] = ["Document", "Chunk", "Entity"];

const INITIAL: Omit<
  ExplorerState,
  | "replaceGraph"
  | "mergeGraph"
  | "selectNode"
  | "toggleType"
  | "setQuery"
  | "setLoading"
  | "setError"
  | "reset"
> = {
  nodes: [],
  edges: [],
  hitNodeIds: new Set(),
  selectedNodeId: null,
  visibleTypes: new Set(ALL_TYPES),
  query: "",
  mode: "top_entities",
  loading: false,
  error: null,
  degradedStages: [],
  truncated: false,
};

export const useExplorerStore = create<ExplorerState>((set) => ({
  ...INITIAL,

  replaceGraph: (response) =>
    set({
      nodes: response.nodes,
      edges: response.edges,
      hitNodeIds: new Set(response.meta.hit_node_ids),
      mode: response.meta.mode,
      truncated: response.meta.truncated,
      degradedStages: response.meta.degraded_stages,
      error: null,
    }),

  mergeGraph: (response) =>
    set((state) => {
      const byId = new Map(state.nodes.map((n) => [n.id, n]));
      for (const n of response.nodes) byId.set(n.id, n);
      const edgeIds = new Set(state.edges.map((e) => e.id));
      const mergedEdges = [...state.edges];
      for (const e of response.edges) {
        if (!edgeIds.has(e.id)) {
          mergedEdges.push(e);
          edgeIds.add(e.id);
        }
      }
      return {
        nodes: [...byId.values()],
        edges: mergedEdges,
        hitNodeIds: new Set(response.meta.hit_node_ids),
        mode: response.meta.mode,
        truncated: state.truncated || response.meta.truncated,
        degradedStages: response.meta.degraded_stages,
        error: null,
      };
    }),

  selectNode: (id) => set({ selectedNodeId: id }),

  toggleType: (t) =>
    set((state) => {
      const next = new Set(state.visibleTypes);
      if (next.has(t)) next.delete(t);
      else next.add(t);
      return { visibleTypes: next };
    }),

  setQuery: (q) => set({ query: q }),
  setLoading: (loading) => set({ loading }),
  setError: (error) => set({ error }),

  reset: () => set({ ...INITIAL, visibleTypes: new Set(ALL_TYPES), hitNodeIds: new Set() }),
}));
```

- [ ] **Step 4: Run the tests (pass)**

```bash
cd apps/web && pnpm test src/stores/explorer-store.test.ts
```

Expected: 4 PASS.

- [ ] **Step 5: Commit**

```bash
git add apps/web/src/stores/explorer-store.ts apps/web/src/stores/explorer-store.test.ts
git commit -m "feat(web/explorer): zustand store for graph state with merge/replace"
```

---

### Task 11: Empty state component + tests

**Files:**
- Create: `apps/web/src/components/explorer/explorer-empty-state.tsx`
- Create: `apps/web/src/components/explorer/explorer-empty-state.test.tsx`

- [ ] **Step 1: Write the failing tests**

```tsx
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { ExplorerEmptyState } from "./explorer-empty-state";

describe("ExplorerEmptyState", () => {
  it("renders loading variant", () => {
    render(<ExplorerEmptyState variant="loading" />);
    expect(screen.getByText(/loading/i)).toBeInTheDocument();
  });

  it("renders error variant with the message", () => {
    render(<ExplorerEmptyState variant="error" message="boom" />);
    expect(screen.getByText(/boom/)).toBeInTheDocument();
  });

  it("renders empty variant", () => {
    render(<ExplorerEmptyState variant="empty" />);
    expect(screen.getByText(/no entities/i)).toBeInTheDocument();
  });

  it("renders degraded variant with the explanation", () => {
    render(<ExplorerEmptyState variant="degraded" />);
    expect(screen.getByText(/graph data unavailable/i)).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run the tests (fail)**

```bash
cd apps/web && pnpm test src/components/explorer/explorer-empty-state.test.tsx
```

Expected: tests FAIL.

- [ ] **Step 3: Implement the component**

```tsx
import { AlertTriangle, Loader2, Network } from "lucide-react";

export type EmptyStateVariant = "loading" | "error" | "empty" | "degraded";

interface Props {
  variant: EmptyStateVariant;
  message?: string;
}

export function ExplorerEmptyState({ variant, message }: Props) {
  if (variant === "loading") {
    return (
      <div className="flex h-full items-center justify-center text-muted-foreground">
        <Loader2 className="mr-2 h-4 w-4 animate-spin" />
        Loading graph…
      </div>
    );
  }
  if (variant === "error") {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-2 text-destructive">
        <AlertTriangle className="h-6 w-6" />
        <div className="text-sm">{message || "Failed to load graph."}</div>
      </div>
    );
  }
  if (variant === "degraded") {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-2 text-amber-700">
        <AlertTriangle className="h-6 w-6" />
        <div className="text-sm">
          Graph data unavailable — showing semantic results only.
        </div>
      </div>
    );
  }
  return (
    <div className="flex h-full flex-col items-center justify-center gap-2 text-muted-foreground">
      <Network className="h-6 w-6" />
      <div className="text-sm">No entities yet — ingest content to populate the graph.</div>
    </div>
  );
}
```

- [ ] **Step 4: Run the tests (pass)**

```bash
cd apps/web && pnpm test src/components/explorer/explorer-empty-state.test.tsx
```

Expected: 4 PASS.

- [ ] **Step 5: Commit**

```bash
git add apps/web/src/components/explorer/explorer-empty-state.tsx apps/web/src/components/explorer/explorer-empty-state.test.tsx
git commit -m "feat(web/explorer): empty/loading/error/degraded state component"
```

---

### Task 12: Filter pills + tests

**Files:**
- Create: `apps/web/src/components/explorer/explorer-filter-pills.tsx`
- Create: `apps/web/src/components/explorer/explorer-filter-pills.test.tsx`

- [ ] **Step 1: Write the failing tests**

```tsx
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, beforeEach } from "vitest";
import { useExplorerStore } from "@/stores/explorer-store";
import { ExplorerFilterPills } from "./explorer-filter-pills";

describe("ExplorerFilterPills", () => {
  beforeEach(() => useExplorerStore.getState().reset());

  it("renders three pills, all selected by default", () => {
    render(<ExplorerFilterPills />);
    for (const label of ["Document", "Chunk", "Entity"]) {
      const pill = screen.getByRole("button", { name: label });
      expect(pill).toHaveAttribute("aria-pressed", "true");
    }
  });

  it("clicking a pill toggles its visibility in the store", async () => {
    render(<ExplorerFilterPills />);
    await userEvent.click(screen.getByRole("button", { name: "Chunk" }));
    expect(useExplorerStore.getState().visibleTypes.has("Chunk")).toBe(false);
    await userEvent.click(screen.getByRole("button", { name: "Chunk" }));
    expect(useExplorerStore.getState().visibleTypes.has("Chunk")).toBe(true);
  });
});
```

- [ ] **Step 2: Run the tests (fail)**

```bash
cd apps/web && pnpm test src/components/explorer/explorer-filter-pills.test.tsx
```

Expected: tests FAIL.

- [ ] **Step 3: Implement the component**

```tsx
import { useExplorerStore } from "@/stores/explorer-store";
import { cn } from "@/lib/cn";
import type { NodeType } from "@/lib/api/knowledge-graph";

const TYPES: { type: NodeType; color: string }[] = [
  { type: "Document", color: "bg-blue-500/20 text-blue-700 ring-blue-500" },
  { type: "Chunk", color: "bg-gray-500/20 text-gray-700 ring-gray-500" },
  { type: "Entity", color: "bg-emerald-500/20 text-emerald-700 ring-emerald-500" },
];

export function ExplorerFilterPills() {
  const visibleTypes = useExplorerStore((s) => s.visibleTypes);
  const toggleType = useExplorerStore((s) => s.toggleType);

  return (
    <div className="flex gap-2">
      {TYPES.map(({ type, color }) => {
        const active = visibleTypes.has(type);
        return (
          <button
            key={type}
            type="button"
            aria-pressed={active}
            onClick={() => toggleType(type)}
            className={cn(
              "rounded-full px-3 py-1 text-xs font-medium ring-1 ring-inset transition",
              active ? color : "bg-transparent text-muted-foreground ring-muted",
            )}
          >
            {type}
          </button>
        );
      })}
    </div>
  );
}
```

- [ ] **Step 4: Run the tests (pass)**

```bash
cd apps/web && pnpm test src/components/explorer/explorer-filter-pills.test.tsx
```

Expected: 2 PASS.

- [ ] **Step 5: Commit**

```bash
git add apps/web/src/components/explorer/explorer-filter-pills.tsx apps/web/src/components/explorer/explorer-filter-pills.test.tsx
git commit -m "feat(web/explorer): filter pills bound to zustand visibleTypes"
```

---

### Task 13: Search bar + tests

**Files:**
- Create: `apps/web/src/components/explorer/explorer-search-bar.tsx`
- Create: `apps/web/src/components/explorer/explorer-search-bar.test.tsx`

- [ ] **Step 1: Write the failing tests**

```tsx
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, beforeEach, vi } from "vitest";
import { useExplorerStore } from "@/stores/explorer-store";
import { ExplorerSearchBar } from "./explorer-search-bar";

describe("ExplorerSearchBar", () => {
  beforeEach(() => useExplorerStore.getState().reset());

  it("typing updates store query and pressing Enter calls onSubmit with the value", async () => {
    const onSubmit = vi.fn();
    render(<ExplorerSearchBar onSubmit={onSubmit} />);
    const input = screen.getByRole("textbox");
    await userEvent.type(input, "hello");
    expect(useExplorerStore.getState().query).toBe("hello");
    await userEvent.type(input, "{Enter}");
    expect(onSubmit).toHaveBeenCalledWith("hello");
  });

  it("clear button resets query and fires onClear", async () => {
    const onClear = vi.fn();
    render(<ExplorerSearchBar onSubmit={() => {}} onClear={onClear} />);
    const input = screen.getByRole("textbox");
    await userEvent.type(input, "abc");
    await userEvent.click(screen.getByRole("button", { name: /clear/i }));
    expect(useExplorerStore.getState().query).toBe("");
    expect(onClear).toHaveBeenCalled();
  });
});
```

- [ ] **Step 2: Run the tests (fail)**

```bash
cd apps/web && pnpm test src/components/explorer/explorer-search-bar.test.tsx
```

Expected: tests FAIL.

- [ ] **Step 3: Implement the component**

```tsx
import { Search, X } from "lucide-react";
import { useExplorerStore } from "@/stores/explorer-store";
import { Input } from "@/components/ui/input";

interface Props {
  onSubmit: (query: string) => void;
  onClear?: () => void;
}

export function ExplorerSearchBar({ onSubmit, onClear }: Props) {
  const query = useExplorerStore((s) => s.query);
  const setQuery = useExplorerStore((s) => s.setQuery);

  return (
    <div className="relative flex-1 max-w-md">
      <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
      <Input
        value={query}
        onChange={(e) => setQuery(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter" && query.trim()) onSubmit(query.trim());
        }}
        placeholder="Search the graph…"
        className="pl-9 pr-8"
      />
      {query && (
        <button
          type="button"
          aria-label="Clear search"
          onClick={() => {
            setQuery("");
            onClear?.();
          }}
          className="absolute right-2 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground"
        >
          <X className="h-4 w-4" />
        </button>
      )}
    </div>
  );
}
```

- [ ] **Step 4: Run the tests (pass)**

```bash
cd apps/web && pnpm test src/components/explorer/explorer-search-bar.test.tsx
```

Expected: 2 PASS.

- [ ] **Step 5: Commit**

```bash
git add apps/web/src/components/explorer/explorer-search-bar.tsx apps/web/src/components/explorer/explorer-search-bar.test.tsx
git commit -m "feat(web/explorer): search bar component with clear action"
```

---

### Task 14: Side panel + tests

**Files:**
- Create: `apps/web/src/components/explorer/explorer-side-panel.tsx`
- Create: `apps/web/src/components/explorer/explorer-side-panel.test.tsx`

- [ ] **Step 1: Write the failing tests**

```tsx
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, beforeEach, vi } from "vitest";
import { useExplorerStore } from "@/stores/explorer-store";
import { ExplorerSidePanel } from "./explorer-side-panel";

describe("ExplorerSidePanel", () => {
  beforeEach(() => useExplorerStore.getState().reset());

  it("renders metadata of the selected node", () => {
    useExplorerStore.getState().mergeGraph({
      nodes: [
        {
          id: "a",
          type: "Entity",
          label: "Llama 3",
          pagerank: 0.5,
          metadata: { entity_type: "PRODUCT", mention_count: 7 },
        },
      ],
      edges: [],
      meta: { mode: "top_entities", truncated: false, hit_node_ids: [], degraded_stages: [] },
    });
    useExplorerStore.getState().selectNode("a");

    render(<ExplorerSidePanel onExpand={() => {}} />);
    expect(screen.getByText("Llama 3")).toBeInTheDocument();
    expect(screen.getByText(/PRODUCT/)).toBeInTheDocument();
    expect(screen.getByText(/7/)).toBeInTheDocument();
  });

  it("Expand button fires onExpand with the selected node id", async () => {
    useExplorerStore.getState().mergeGraph({
      nodes: [{ id: "a", type: "Entity", label: "A", pagerank: 0.1, metadata: {} }],
      edges: [],
      meta: { mode: "top_entities", truncated: false, hit_node_ids: [], degraded_stages: [] },
    });
    useExplorerStore.getState().selectNode("a");

    const onExpand = vi.fn();
    render(<ExplorerSidePanel onExpand={onExpand} />);
    await userEvent.click(screen.getByRole("button", { name: /expand neighborhood/i }));
    expect(onExpand).toHaveBeenCalledWith("a");
  });

  it("renders nothing when no node is selected", () => {
    const { container } = render(<ExplorerSidePanel onExpand={() => {}} />);
    expect(container.firstChild).toBeNull();
  });
});
```

- [ ] **Step 2: Run the tests (fail)**

```bash
cd apps/web && pnpm test src/components/explorer/explorer-side-panel.test.tsx
```

Expected: tests FAIL.

- [ ] **Step 3: Implement the component**

```tsx
import { useExplorerStore } from "@/stores/explorer-store";
import { Button } from "@/components/ui/button";

interface Props {
  onExpand: (seedId: string) => void;
}

export function ExplorerSidePanel({ onExpand }: Props) {
  const selectedNodeId = useExplorerStore((s) => s.selectedNodeId);
  const node = useExplorerStore((s) =>
    s.nodes.find((n) => n.id === s.selectedNodeId) ?? null
  );
  const selectNode = useExplorerStore((s) => s.selectNode);

  if (!selectedNodeId || !node) return null;

  return (
    <aside className="absolute right-0 top-0 z-10 flex h-full w-80 flex-col border-l bg-background shadow-lg">
      <header className="flex items-center justify-between border-b p-3">
        <div className="min-w-0">
          <div className="text-xs uppercase tracking-wider text-muted-foreground">
            {node.type}
          </div>
          <div className="truncate text-base font-semibold">{node.label}</div>
        </div>
        <button
          type="button"
          aria-label="Close panel"
          onClick={() => selectNode(null)}
          className="text-muted-foreground hover:text-foreground"
        >
          ×
        </button>
      </header>
      <div className="flex-1 overflow-y-auto p-3 text-sm">
        {node.pagerank !== null && (
          <div className="mb-2">
            <span className="text-muted-foreground">pagerank: </span>
            <span>{node.pagerank.toFixed(4)}</span>
          </div>
        )}
        <dl className="space-y-1">
          {Object.entries(node.metadata).map(([k, v]) => (
            <div key={k} className="grid grid-cols-[8rem_1fr] gap-2">
              <dt className="text-muted-foreground">{k}</dt>
              <dd className="break-words">{String(v)}</dd>
            </div>
          ))}
        </dl>
      </div>
      <footer className="border-t p-3">
        <Button
          variant="default"
          className="w-full"
          onClick={() => onExpand(node.id)}
        >
          Expand neighborhood
        </Button>
      </footer>
    </aside>
  );
}
```

- [ ] **Step 4: Run the tests (pass)**

```bash
cd apps/web && pnpm test src/components/explorer/explorer-side-panel.test.tsx
```

Expected: 3 PASS.

- [ ] **Step 5: Commit**

```bash
git add apps/web/src/components/explorer/explorer-side-panel.tsx apps/web/src/components/explorer/explorer-side-panel.test.tsx
git commit -m "feat(web/explorer): node detail side panel with expand button"
```

---

## Phase C — Frontend integration

### Task 15: Cytoscape canvas component

**Files:**
- Create: `apps/web/src/components/explorer/explorer-canvas.tsx`

This component does not get a unit test in v1 (per spec §6.2). Manual smoke test in Task 18.

- [ ] **Step 1: Implement the component**

```tsx
import { useEffect, useMemo, useRef } from "react";
import cytoscape, { type Core, type ElementDefinition } from "cytoscape";
import fcose from "cytoscape-fcose";
import { useExplorerStore } from "@/stores/explorer-store";

cytoscape.use(fcose);

const TYPE_COLORS: Record<string, string> = {
  Document: "#3b82f6",
  Chunk: "#9ca3af",
  Entity: "#10b981",
};

export function ExplorerCanvas() {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const cyRef = useRef<Core | null>(null);

  const nodes = useExplorerStore((s) => s.nodes);
  const edges = useExplorerStore((s) => s.edges);
  const hitNodeIds = useExplorerStore((s) => s.hitNodeIds);
  const visibleTypes = useExplorerStore((s) => s.visibleTypes);
  const selectedNodeId = useExplorerStore((s) => s.selectedNodeId);
  const selectNode = useExplorerStore((s) => s.selectNode);

  const elements = useMemo<ElementDefinition[]>(() => {
    const nodeEls: ElementDefinition[] = nodes.map((n) => ({
      group: "nodes",
      data: {
        id: n.id,
        label: n.label,
        type: n.type,
        pagerank: n.pagerank ?? 0,
        hit: hitNodeIds.has(n.id),
      },
    }));
    const edgeEls: ElementDefinition[] = edges.map((e) => ({
      group: "edges",
      data: { id: e.id, source: e.source, target: e.target, type: e.type },
    }));
    return [...nodeEls, ...edgeEls];
  }, [nodes, edges, hitNodeIds]);

  // Mount once.
  useEffect(() => {
    if (!containerRef.current) return;
    const cy = cytoscape({
      container: containerRef.current,
      elements: [],
      style: [
        {
          selector: "node",
          style: {
            "background-color": (ele: cytoscape.NodeSingular) =>
              TYPE_COLORS[ele.data("type")] ?? "#888",
            label: "data(label)",
            "font-size": 11,
            color: "#222",
            "text-wrap": "ellipsis",
            "text-max-width": "120px",
            width: (ele: cytoscape.NodeSingular) =>
              20 + Math.min(40, (ele.data("pagerank") ?? 0) * 200),
            height: (ele: cytoscape.NodeSingular) =>
              20 + Math.min(40, (ele.data("pagerank") ?? 0) * 200),
          },
        },
        {
          selector: "node[hit]",
          style: { "border-color": "#facc15", "border-width": 3 },
        },
        {
          selector: "node:selected",
          style: { "border-color": "#a855f7", "border-width": 4 },
        },
        {
          selector: "edge",
          style: {
            "line-color": "#cbd5e1",
            width: 1,
            "curve-style": "bezier",
          },
        },
      ],
      layout: { name: "preset" },
    });

    cy.on("tap", "node", (evt) => {
      selectNode(evt.target.id());
    });
    cy.on("tap", (evt) => {
      if (evt.target === cy) selectNode(null);
    });

    cyRef.current = cy;
    return () => {
      cy.destroy();
      cyRef.current = null;
    };
  }, [selectNode]);

  // Update elements when store changes.
  useEffect(() => {
    const cy = cyRef.current;
    if (!cy) return;
    cy.json({ elements });
    cy.layout({ name: "fcose", animate: false, randomize: false } as any).run();
  }, [elements]);

  // Apply visibility filter.
  useEffect(() => {
    const cy = cyRef.current;
    if (!cy) return;
    cy.nodes().forEach((n) => {
      const t = n.data("type");
      n.style("display", visibleTypes.has(t) ? "element" : "none");
    });
  }, [visibleTypes]);

  // Keep selection in sync.
  useEffect(() => {
    const cy = cyRef.current;
    if (!cy) return;
    cy.elements().unselect();
    if (selectedNodeId) {
      cy.getElementById(selectedNodeId).select();
    }
  }, [selectedNodeId]);

  return <div ref={containerRef} className="h-full w-full" />;
}
```

- [ ] **Step 2: Type-check**

```bash
cd apps/web && pnpm typecheck
```

Expected: 0 errors.

- [ ] **Step 3: Commit**

```bash
git add apps/web/src/components/explorer/explorer-canvas.tsx
git commit -m "feat(web/explorer): cytoscape canvas with fcose layout and click handlers"
```

---

### Task 16: Explorer route + react-query hooks

**Files:**
- Create: `apps/web/src/routes/explorer.tsx`

- [ ] **Step 1: Implement the route**

```tsx
import { useEffect } from "react";
import { useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  fetchKnowledgeGraph,
  KnowledgeGraphError,
  type GraphResponse,
} from "@/lib/api/knowledge-graph";
import { useExplorerStore } from "@/stores/explorer-store";
import { ExplorerCanvas } from "@/components/explorer/explorer-canvas";
import { ExplorerEmptyState } from "@/components/explorer/explorer-empty-state";
import { ExplorerFilterPills } from "@/components/explorer/explorer-filter-pills";
import { ExplorerSearchBar } from "@/components/explorer/explorer-search-bar";
import { ExplorerSidePanel } from "@/components/explorer/explorer-side-panel";

export function ExplorerRoute() {
  const { id: projectId } = useParams<{ id: string }>();
  const replaceGraph = useExplorerStore((s) => s.replaceGraph);
  const mergeGraph = useExplorerStore((s) => s.mergeGraph);
  const reset = useExplorerStore((s) => s.reset);
  const truncated = useExplorerStore((s) => s.truncated);
  const degradedStages = useExplorerStore((s) => s.degradedStages);
  const nodes = useExplorerStore((s) => s.nodes);
  const queryClient = useQueryClient();

  // Reset store when project changes.
  useEffect(() => {
    reset();
  }, [projectId, reset]);

  const overviewQuery = useQuery({
    queryKey: ["graph", projectId, "overview"],
    enabled: !!projectId,
    staleTime: 30_000,
    queryFn: ({ signal }) =>
      fetchKnowledgeGraph({ projectId: projectId!, limit: 30 }, signal),
  });

  useEffect(() => {
    if (overviewQuery.data) replaceGraph(overviewQuery.data);
  }, [overviewQuery.data, replaceGraph]);

  const searchMutation = useMutation({
    mutationFn: (q: string) =>
      fetchKnowledgeGraph({ projectId: projectId!, q, limit: 50 }),
    onSuccess: (data) => replaceGraph(data),
  });

  const expandMutation = useMutation({
    mutationFn: (seedId: string) =>
      fetchKnowledgeGraph({
        projectId: projectId!,
        seedNodeIds: [seedId],
        limit: 50,
      }),
    onSuccess: (data: GraphResponse) => mergeGraph(data),
  });

  if (!projectId) return null;

  const isLoading = overviewQuery.isPending || searchMutation.isPending;
  const error = overviewQuery.error || searchMutation.error || expandMutation.error;
  const errorMessage =
    error instanceof KnowledgeGraphError ? error.message : (error as Error)?.message;

  return (
    <div className="flex h-full flex-col">
      <header className="flex items-center gap-3 border-b p-3">
        <ExplorerSearchBar
          onSubmit={(q) => searchMutation.mutate(q)}
          onClear={() => {
            queryClient.invalidateQueries({ queryKey: ["graph", projectId, "overview"] });
            reset();
          }}
        />
        <ExplorerFilterPills />
      </header>
      {truncated && (
        <div className="border-b bg-amber-50 px-3 py-1 text-xs text-amber-900">
          Showing top results — refine your search to see more.
        </div>
      )}
      {degradedStages.includes("graph_unavailable") && (
        <div className="border-b bg-amber-50 px-3 py-1 text-xs text-amber-900">
          Graph data unavailable — showing semantic results only.
        </div>
      )}
      <div className="relative flex-1 overflow-hidden">
        {isLoading && nodes.length === 0 && <ExplorerEmptyState variant="loading" />}
        {!isLoading && error && (
          <ExplorerEmptyState variant="error" message={errorMessage} />
        )}
        {!isLoading && !error && nodes.length === 0 && (
          <ExplorerEmptyState variant="empty" />
        )}
        {nodes.length > 0 && <ExplorerCanvas />}
        <ExplorerSidePanel onExpand={(id) => expandMutation.mutate(id)} />
      </div>
    </div>
  );
}
```

- [ ] **Step 2: Type-check**

```bash
cd apps/web && pnpm typecheck
```

Expected: 0 errors.

- [ ] **Step 3: Commit**

```bash
git add apps/web/src/routes/explorer.tsx
git commit -m "feat(web/explorer): explorer route with react-query overview/search/expand"
```

---

### Task 17: Project shell + tab switcher + route wiring

**Files:**
- Create: `apps/web/src/components/sidebar/project-tabs.tsx`
- Modify: `apps/web/src/routes/project.tsx`
- Modify: `apps/web/src/main.tsx`

- [ ] **Step 1: Create the tabs component**

`apps/web/src/components/sidebar/project-tabs.tsx`:

```tsx
import { NavLink } from "react-router-dom";
import { MessageSquare, Network } from "lucide-react";
import { cn } from "@/lib/cn";

interface Props {
  projectId: string;
}

export function ProjectTabs({ projectId }: Props) {
  return (
    <nav className="flex gap-1 border-b px-3 py-2">
      <NavLink
        to={`/projects/${projectId}`}
        end
        className={({ isActive }) =>
          cn(
            "flex items-center gap-1.5 rounded-md px-3 py-1 text-sm transition",
            isActive ? "bg-accent font-medium" : "text-muted-foreground hover:bg-accent/50",
          )
        }
      >
        <MessageSquare className="h-4 w-4" />
        Chat
      </NavLink>
      <NavLink
        to={`/projects/${projectId}/explorer`}
        className={({ isActive }) =>
          cn(
            "flex items-center gap-1.5 rounded-md px-3 py-1 text-sm transition",
            isActive ? "bg-accent font-medium" : "text-muted-foreground hover:bg-accent/50",
          )
        }
      >
        <Network className="h-4 w-4" />
        Explorer
      </NavLink>
    </nav>
  );
}
```

- [ ] **Step 2: Wrap project route with shell**

Replace `apps/web/src/routes/project.tsx` with:

```tsx
import { Outlet, useParams } from "react-router-dom";
import { ProjectTabs } from "@/components/sidebar/project-tabs";
import { ChatPanel } from "@/components/chat/chat-panel";

export function ProjectShell() {
  const { id } = useParams<{ id: string }>();
  if (!id) return null;
  return (
    <div className="flex h-full flex-col">
      <ProjectTabs projectId={id} />
      <div className="flex-1 overflow-hidden">
        <Outlet />
      </div>
    </div>
  );
}

export function ChatRoute() {
  const { id } = useParams<{ id: string }>();
  if (!id) return null;
  return <ChatPanel project_id={id} />;
}
```

- [ ] **Step 3: Wire the routes**

Edit `apps/web/src/main.tsx` to nest the project routes under the shell:

```tsx
import { ChatRoute, ProjectShell } from "./routes/project";
import { ExplorerRoute } from "./routes/explorer";

// inside createBrowserRouter([...]) replace the "projects/:id" entry with:
{
  path: "projects/:id",
  element: <ProjectShell />,
  children: [
    { index: true, element: <ChatRoute /> },
    { path: "explorer", element: <ExplorerRoute /> },
  ],
},
```

(Keep all other route entries unchanged.)

- [ ] **Step 4: Type-check + run existing tests**

```bash
cd apps/web && pnpm typecheck && pnpm test
```

Expected: 0 type errors; all tests PASS (existing chat tests must still pass — the route now sits under a shell, not in isolation).

- [ ] **Step 5: Commit**

```bash
git add apps/web/src/components/sidebar/project-tabs.tsx apps/web/src/routes/project.tsx apps/web/src/main.tsx
git commit -m "feat(web): nested project shell with Chat/Explorer tab switcher"
```

---

### Task 18: Manual smoke test + acceptance checklist

**Files:** none (this task is a verification gate, not a code change).

- [ ] **Step 1: Spin up the stack**

```bash
docker compose up -d  # postgres + chroma + neo4j
uv run uvicorn atlas_api.main:app --reload --port 8000
# in another terminal:
cd apps/web && pnpm dev
```

- [ ] **Step 2: Ingest a real project**

In the UI, create a project, ingest a markdown file or two with the existing ingest panel, wait for the job to complete (chunks land in Postgres + Chroma + Neo4j; PageRank runs).

- [ ] **Step 3: Verify acceptance criteria from spec §7**

For each, mark pass/fail in the commit message:

1. Open `/projects/<id>/explorer` → up to 30 entities render, force-laid-out, sized by PageRank. ☐
2. Type a query, press Enter → canvas swaps to hit-centric subgraph; matching chunks have a gold border. ☐
3. Click any node → side panel opens with metadata + outgoing edges. ☐
4. Click "Expand neighborhood" → 1-hop neighbors of the selected node appear without resetting layout (≤ 25 new). ☐
5. Toggle a filter pill → that node type instantly hides/shows; no network call (verify via DevTools Network tab). ☐
6. Stop Neo4j (`docker compose stop neo4j`) → reload `/explorer`: degraded banner appears, search returns vector-only hits, no crash. ☐
7. Sidebar tab switcher navigates Chat ↔ Explorer for the open project. ☐

- [ ] **Step 4: Commit smoke results**

If all pass:

```bash
git commit --allow-empty -m "test(plan-5): manual smoke — all 7 acceptance criteria pass"
```

If any fail, file follow-up tasks before claiming done.

---

## Self-review

(Performed inline by the plan author. Issues found and fixed below.)

**Spec coverage:**
- §3 architecture, mode discrimination → Tasks 4-7.
- §4.1 endpoint params → Task 4 + Task 5 + Task 6 (all params + filter + 422).
- §4.2 wire format → Task 1 (Pydantic) + Task 9 (TS).
- §4.3 GraphStore.expand_nodes → renamed to `fetch_subgraph_by_seeds` in Tasks 2-3 (more descriptive of UI intent; expand_chunks stays untouched per spec §4.3).
- §4.4 wiring → Task 4 (router); deps already exist.
- §4.5 degraded paths → Task 7.
- §5.1 routes + sidebar tabs → Task 17.
- §5.2 component tree → Tasks 11-15.
- §5.3 library → Task 9 (deps), Task 15 (mount).
- §5.4 store → Task 10.
- §5.5 react-query keys → Task 16.
- §5.6 visuals → Task 15 (canvas styles).
- §5.7 interactions → Tasks 14, 16.
- §5.8 hand-written types → Task 9.
- §6.1 backend tests → Tasks 4-8.
- §6.2 frontend tests → Tasks 10-14; canvas test gap noted in Task 15.
- §7 acceptance → Task 18.

All spec sections covered.

**Naming consistency:** `expand_nodes` from spec became `fetch_subgraph_by_seeds` in the plan to better describe its role as a UI helper (vs Plan 4's `expand_chunks` retrieval helper). The spec text in §4.3 uses `expand_nodes`; this rename is intentional. Adjust the spec post-merge if desired.

No placeholders, no "implement later" stubs, no references to undefined functions.
