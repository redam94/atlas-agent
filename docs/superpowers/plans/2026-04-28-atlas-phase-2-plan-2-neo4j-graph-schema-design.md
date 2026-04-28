# ATLAS Phase 2 — Plan 2 — Neo4j + Graph Schema + Write Path Design

**Status:** Draft · 2026-04-28
**Implements:** Phase 2 spec §5.2 (`docs/superpowers/specs/2026-04-27-atlas-phase-2-knowledge-graph-design.md`); cross-cutting decisions in §3.3, §4.1, §4.6, §4.7.
**Predecessor:** Plan 1 (URL ingestion) merged 2026-04-27 (PR #7) + follow-ups (PR #8). `IngestionService` orchestrates parser → chunker → embed → Chroma + Postgres for markdown / PDF / URL sources.
**Successor:** Plan 3 (NER + entity edges + PageRank) — depends on the graph being populated by this plan.

---

## 1. Purpose

Stand up Neo4j 5 community alongside the existing stack and start populating it with `Document` + `Chunk` nodes plus structural `PART_OF` and `BELONGS_TO` edges on every ingestion. Backfill the Phase 1 corpus once. **No behavior change visible to the user.** Plan 4's hybrid retrieval will read from the graph; Plan 2 just gets the writes wired.

This is the lowest-risk way to introduce a new database service: the graph is being written but nothing reads it yet, so a misbehaving graph layer can't break user-facing chat.

---

## 2. Scope

### In scope
- New `neo4j` service in `infra/docker-compose.yml` with the `graph-data-science` plugin and the memory limits from spec §4.7.
- New workspace package `packages/atlas-graph/` containing:
  - `GraphStore` — async wrapper around the `neo4j` driver. Plan 2 ships only the write path (`write_document_chunks`) plus `healthcheck`.
  - `MigrationRunner` — discovers `atlas_graph/schema/migrations/NNN_*.cypher`, applies in id order, records `(:Migration {id, applied_at})`.
  - `backfill_phase1` — one-shot Postgres → Neo4j walk, idempotent via `MERGE`, batches of 1000, persists `(:BackfillState)` for progress visibility.
  - `atlas-graph` CLI (`uv run atlas-graph backfill`).
- `001_initial_schema.cypher` — uniqueness constraints on `(:Project {id})`, `(:Document {id})`, `(:Chunk {id})`; indexes on `Chunk.project_id`, `Document.project_id`.
- `IngestionService` extension: optional `graph_writer` constructor kwarg (Q1=A); when supplied, calls `write_document_chunks` after Chroma upsert, inside the same try/except that owns job-status accounting.
- `GraphConfig` (new pydantic-settings class with `ATLAS_GRAPH__` prefix): `uri`, `user`, `password`, `backfill_on_start`.
- Lifespan plumbing: connect driver → run pending migrations → optionally backfill → construct `GraphStore` → wire into `IngestionService`.
- Unit + integration + lifespan tests; manual docker-compose smoke pass.

### Out of scope
- NER / entity nodes / `REFERENCES` edges — Plan 3.
- `SEMANTICALLY_NEAR`, `TEMPORAL_NEAR` edges — Plan 3.
- `gds.pageRank` calls — Plan 3.
- Hybrid retrieval — Plan 4.
- Knowledge Explorer UI — Plan 5.
- Note editor — Plan 6.
- Enterprise Neo4j features (Aura, clustering, SSO).

---

## 3. Architecture

### 3.1 Service topology

```
┌──────────────────────────────────────────────────────────────┐
│ docker-compose                                               │
├──────────────────────────────────────────────────────────────┤
│  postgres   (already)                                        │
│  redis      (already)                                        │
│  api        (already; gains app.state.graph_store)           │
│  web        (already)                                        │
│  neo4j      (NEW; graph-data-science plugin, ~2.5 GB RAM)    │
└──────────────────────────────────────────────────────────────┘
```

The api `depends_on` adds `neo4j: { condition: service_healthy }`. Neo4j's healthcheck probes `http://localhost:7474` (HTTP UI = process is up); cold-start budget is ~2 minutes (12 retries × 10 s).

### 3.2 Package boundaries

```
atlas-knowledge   (lower-level)
   │
   │ defines: GraphWriter Protocol (1 method)
   │
   ▼
atlas-graph       (upper-level)
   └── GraphStore implements GraphWriter; depends on atlas-core for config / ORM
```

The `Protocol` lives in `atlas_knowledge.ingestion.protocols` so `atlas-knowledge` does NOT import `atlas-graph`. Dependency direction stays clean: `atlas-knowledge` only knows there's *some* graph-writer interface; `atlas-graph` knows nothing about ingestion service internals.

### 3.3 Data flow on ingest

```
POST /api/v1/knowledge/ingest/{markdown,pdf,url}
   │
   ▼
IngestionService.ingest(...)
   ├── 1. persist (:IngestionJobORM)
   ├── 2. persist (:KnowledgeNodeORM type=document)
   ├── 3. chunk + persist (:KnowledgeNodeORM type=chunk) rows
   ├── 4. embed + Chroma upsert
   ├── 5. stamp embedding_id on chunk rows
   ├── 6. (NEW) graph_writer.write_document_chunks(...) — 5 MERGEs in 1 tx
   └── 7. mark job 'completed'
```

Step 6 happens inside the same `try` that already owns steps 1-5. Failure marks the job `failed` with the error string; Postgres + Chroma are not rolled back (idempotent backfill recovers the gap).

### 3.4 Graph schema after Plan 2

Two node labels and two edge types (Plan 3 adds `:Entity`, `REFERENCES`, `SEMANTICALLY_NEAR`, `TEMPORAL_NEAR`; Plan 6 adds `:Note`).

```
(:Project {id: UUID, name: str})
(:Document {id: UUID, project_id: UUID, title: str, source_type: str, metadata: map})
(:Chunk {id: UUID, project_id: UUID, parent_id: UUID, position: int, token_count: int, text_preview: str})

(:Document)-[:PART_OF]->(:Project)
(:Chunk)-[:BELONGS_TO]->(:Document)
```

Constraints from `001_initial_schema.cypher`:

```cypher
CREATE CONSTRAINT project_id_unique IF NOT EXISTS
  FOR (p:Project) REQUIRE p.id IS UNIQUE;
CREATE CONSTRAINT document_id_unique IF NOT EXISTS
  FOR (d:Document) REQUIRE d.id IS UNIQUE;
CREATE CONSTRAINT chunk_id_unique IF NOT EXISTS
  FOR (c:Chunk) REQUIRE c.id IS UNIQUE;
CREATE INDEX chunk_project_id IF NOT EXISTS FOR (c:Chunk) ON (c.project_id);
CREATE INDEX document_project_id IF NOT EXISTS FOR (d:Document) ON (d.project_id);
```

`text_preview` is the first 200 chars of chunk text. Full text stays in Postgres; the graph holds enough for explorer-UI cards (Plan 5) without duplicating storage.

---

## 4. Components

### 4.1 `packages/atlas-graph/` (new workspace member)

```
packages/atlas-graph/
├── pyproject.toml
├── atlas_graph/
│   ├── __init__.py            re-exports GraphStore, MigrationRunner, ChunkSpec, GraphUnavailableError
│   ├── __main__.py            CLI entry: `atlas-graph backfill`
│   ├── store.py               GraphStore — driver wrapper + write_document_chunks + healthcheck
│   ├── protocols.py           ChunkSpec dataclass (lives here, not in atlas-knowledge, to keep deps clean)
│   ├── schema/
│   │   ├── __init__.py
│   │   ├── runner.py          MigrationRunner
│   │   └── migrations/
│   │       └── 001_initial_schema.cypher
│   ├── backfill.py            backfill_phase1(...) + BackfillResult dataclass
│   └── tests/
│       ├── __init__.py
│       ├── conftest.py        `real_neo4j_driver` fixture (skipped if ATLAS_TEST_NEO4J_URL unset)
│       ├── fixtures.py        ChunkSpec/document fixture builders
│       ├── test_runner.py     unit, mocked driver
│       ├── test_store.py      unit, mocked driver
│       ├── test_backfill.py   integration with real Postgres + mocked GraphStore
│       ├── test_runner_integration.py    opt-in real Neo4j
│       └── test_store_integration.py     opt-in real Neo4j
```

`pyproject.toml` deps: `atlas-core` (workspace), `neo4j>=5.20,<6`, `structlog>=24.4`. CLI script entry:

```toml
[project.scripts]
atlas-graph = "atlas_graph.__main__:main"
```

The Protocol that `atlas-knowledge` imports lives at `atlas_knowledge.ingestion.protocols.GraphWriter` (one method, `write_document_chunks(...) -> None`). The implementation in `atlas_graph.store.GraphStore` matches the protocol structurally; no inheritance.

### 4.2 `GraphStore` (atlas_graph/store.py)

```python
from contextlib import asynccontextmanager
from neo4j import AsyncDriver
from neo4j.exceptions import ServiceUnavailable, TransientError
import asyncio
import structlog

log = structlog.get_logger("atlas.graph.store")


class GraphUnavailableError(RuntimeError):
    """Raised when Neo4j is unreachable after exhausting retries."""


class GraphStore:
    def __init__(self, driver: AsyncDriver, *, max_retries: int = 3) -> None:
        self._driver = driver
        self._max_retries = max_retries

    async def close(self) -> None:
        await self._driver.close()

    async def healthcheck(self) -> None:
        async with self._session() as s:
            await s.run("RETURN 1")

    async def write_document_chunks(
        self,
        *,
        project_id: UUID,
        project_name: str,
        document_id: UUID,
        document_title: str,
        document_source_type: str,
        document_metadata: dict,
        chunks: list[ChunkSpec],
    ) -> None:
        async def _do(tx: AsyncTransaction) -> None:
            await tx.run(
                "MERGE (p:Project {id: $project_id}) "
                "ON CREATE SET p.name = $name "
                "ON MATCH SET p.name = coalesce(p.name, $name)",
                project_id=str(project_id), name=project_name,
            )
            await tx.run(
                "MERGE (d:Document {id: $id}) "
                "SET d.project_id = $project_id, d.title = $title, "
                "    d.source_type = $source_type, d.metadata = $metadata",
                id=str(document_id), project_id=str(project_id),
                title=document_title, source_type=document_source_type,
                metadata=_serialize_metadata(document_metadata),
            )
            await tx.run(
                "MATCH (d:Document {id: $document_id}), (p:Project {id: $project_id}) "
                "MERGE (d)-[:PART_OF]->(p)",
                document_id=str(document_id), project_id=str(project_id),
            )
            await tx.run(
                "UNWIND $chunks AS c "
                "MERGE (ch:Chunk {id: c.id}) "
                "SET ch.project_id = $project_id, ch.parent_id = $document_id, "
                "    ch.position = c.position, ch.token_count = c.token_count, "
                "    ch.text_preview = c.text_preview",
                chunks=[c.to_param() for c in chunks],
                project_id=str(project_id), document_id=str(document_id),
            )
            await tx.run(
                "MATCH (d:Document {id: $document_id}) "
                "UNWIND $chunk_ids AS cid "
                "MATCH (c:Chunk {id: cid}) "
                "MERGE (c)-[:BELONGS_TO]->(d)",
                document_id=str(document_id),
                chunk_ids=[str(c.id) for c in chunks],
            )

        await self._with_retry(_do)

    async def _with_retry(self, fn: Callable[[AsyncTransaction], Awaitable[None]]) -> None:
        delay = 0.5
        for attempt in range(1, self._max_retries + 1):
            try:
                async with self._session() as s:
                    await s.execute_write(fn)
                return
            except (ServiceUnavailable, TransientError) as e:
                if attempt == self._max_retries:
                    raise GraphUnavailableError(f"neo4j unavailable: {e}") from e
                log.warning("graph.retry", attempt=attempt, error=str(e))
                await asyncio.sleep(delay)
                delay *= 2

    @asynccontextmanager
    async def _session(self):
        async with self._driver.session() as s:
            yield s
```

`ChunkSpec` (in `atlas_graph/protocols.py`):

```python
@dataclass(frozen=True)
class ChunkSpec:
    id: UUID
    position: int
    token_count: int
    text_preview: str

    def to_param(self) -> dict[str, object]:
        return {
            "id": str(self.id), "position": self.position,
            "token_count": self.token_count, "text_preview": self.text_preview,
        }
```

`_serialize_metadata` flattens the dict to JSON-serializable primitives Neo4j accepts (str/int/float/bool/list of those). Neo4j 5 doesn't allow nested dict properties; nested values get JSON-encoded as strings. Implementation: a small recursive walk.

### 4.3 `MigrationRunner` (atlas_graph/schema/runner.py)

```python
class MigrationRunner:
    def __init__(self, driver: AsyncDriver, migrations_dir: Path) -> None: ...

    async def run_pending(self) -> list[str]:
        applied = await self._load_applied()
        files = sorted(
            f for f in self._migrations_dir.glob("*.cypher")
            if _MIGRATION_FILE_RE.match(f.name)
        )
        newly_applied: list[str] = []
        for f in files:
            mid = f.stem.split("_", 1)[0]
            if mid in applied:
                continue
            cypher = f.read_text()
            async with self._driver.session() as s:
                await s.execute_write(self._apply, mid, cypher)
            newly_applied.append(mid)
            log.info("graph.migration.applied", id=mid, file=f.name)
        return newly_applied

    @staticmethod
    async def _apply(tx, mid: str, cypher: str) -> None:
        # Cypher files are single-statement (one MERGE/CREATE block).
        # If a file has multiple statements separated by ';', split and run sequentially.
        for stmt in [s.strip() for s in cypher.split(";") if s.strip()]:
            await tx.run(stmt)
        await tx.run(
            "MERGE (m:Migration {id: $id}) ON CREATE SET m.applied_at = datetime()",
            id=mid,
        )
```

`_MIGRATION_FILE_RE = re.compile(r"^\d{3}_[a-z0-9_]+\.cypher$")`. Files not matching are silently skipped (allows README files etc. to coexist).

### 4.4 `backfill_phase1` (atlas_graph/backfill.py)

```python
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
    batch_size: int = 1000,
    progress_cb: Callable[[int, int], None] | None = None,
) -> BackfillResult:
    started = datetime.now(UTC)
    project_rows = await db.execute(select(ProjectORM))
    projects = {p.id: p.name for p in project_rows.scalars()}

    # Walk Documents in created_at order, then their chunks.
    docs_q = (
        select(KnowledgeNodeORM)
        .where(KnowledgeNodeORM.type == "document")
        .order_by(KnowledgeNodeORM.created_at)
    )
    doc_rows = (await db.execute(docs_q)).scalars().all()

    total_docs = len(doc_rows)
    total_chunks = 0
    batch_idx = 0
    pending: list[tuple[KnowledgeNodeORM, list[ChunkSpec]]] = []

    for doc in doc_rows:
        chunks_q = (
            select(KnowledgeNodeORM)
            .where(KnowledgeNodeORM.parent_id == doc.id)
            .order_by(KnowledgeNodeORM.created_at)
        )
        chunk_rows = (await db.execute(chunks_q)).scalars().all()
        specs = [
            ChunkSpec(
                id=c.id,
                position=int(c.metadata_.get("index", 0)),
                token_count=int(c.metadata_.get("token_count", 0)),
                text_preview=c.text[:200],
            )
            for c in chunk_rows
        ]
        pending.append((doc, specs))
        total_chunks += len(specs)
        if len(pending) >= _DOCS_PER_BATCH:
            await _flush_batch(graph, projects, pending)
            batch_idx += 1
            await _update_state(graph, key="phase1", batch=batch_idx, last_doc_id=doc.id)
            if progress_cb:
                progress_cb(batch_idx, ceil(total_docs / _DOCS_PER_BATCH))
            pending.clear()

    if pending:
        await _flush_batch(graph, projects, pending)
        batch_idx += 1
        await _update_state(graph, key="phase1", batch=batch_idx, last_doc_id=doc_rows[-1].id)

    finished = datetime.now(UTC)
    await _finalize_state(graph, key="phase1", finished_at=finished)
    return BackfillResult(
        documents=total_docs, chunks=total_chunks, batches=batch_idx,
        started_at=started, finished_at=finished,
    )
```

`_DOCS_PER_BATCH` = 50 (50 docs × ~20 chunks/doc ≈ 1000 chunks/batch, matching the spec's "batch size 1000"). `_flush_batch` issues one `write_document_chunks` per document. `_update_state` writes/updates `(:BackfillState {key:'phase1', batches_done, last_doc_id, started_at})`. `_finalize_state` adds `finished_at`. Resume is via the idempotency of `MERGE` (Q5=C); `BackfillState` is for progress visibility, not resume logic.

### 4.5 CLI (atlas_graph/__main__.py)

```python
def main() -> None:
    parser = argparse.ArgumentParser(prog="atlas-graph")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("backfill", help="Backfill Phase 1 chunks into Neo4j")
    args = parser.parse_args()
    if args.cmd == "backfill":
        asyncio.run(_run_backfill())


async def _run_backfill() -> None:
    config = AtlasConfig()
    engine = create_engine_from_config(config)
    factory = create_session_factory(engine)
    driver = AsyncGraphDatabase.driver(
        str(config.graph.uri),
        auth=(config.graph.user, config.graph.password.get_secret_value()),
    )
    graph = GraphStore(driver)
    try:
        async with session_scope(factory) as db:
            await backfill_phase1(
                db=db, graph=graph,
                progress_cb=lambda b, total: print(f"batch {b}/{total} ({b*100//total}%)"),
            )
    finally:
        await graph.close()
        await engine.dispose()
```

### 4.6 `IngestionService` extension

```python
# packages/atlas-knowledge/atlas_knowledge/ingestion/protocols.py  (NEW)
from typing import Protocol
from uuid import UUID


class GraphWriter(Protocol):
    async def write_document_chunks(
        self,
        *,
        project_id: UUID,
        project_name: str,
        document_id: UUID,
        document_title: str,
        document_source_type: str,
        document_metadata: dict,
        chunks: "Sequence[ChunkSpecLike]",  # duck-typed; only id/position/token_count/text_preview accessed
    ) -> None: ...
```

`ChunkSpecLike` is a structural type — `atlas-knowledge` does NOT import `atlas_graph.protocols.ChunkSpec`. The `IngestionService` builds plain dicts that match the shape:

```python
# IngestionService.ingest, after step 5:
if self._graph_writer is not None:
    await self._graph_writer.write_document_chunks(
        project_id=project_id,
        project_name=...,  # fetched from ProjectORM at start of ingest()
        document_id=doc_row.id,
        document_title=doc_row.title or "Untitled",
        document_source_type=source_type,
        document_metadata=dict(doc_row.metadata_ or {}),
        chunks=[
            _ChunkSpecAdapter(
                id=r.id,
                position=int(r.metadata_.get("index", 0)),
                token_count=int(r.metadata_.get("token_count", 0)),
                text_preview=r.text[:200],
            )
            for r in chunk_rows
        ],
    )
```

`_ChunkSpecAdapter` is a tiny frozen dataclass internal to `atlas-knowledge` matching the duck-typed interface. The `to_param` method that `GraphStore` calls is implemented identically — the boundary is structural.

The `graph_writer` constructor kwarg defaults to `None`, so existing tests that construct `IngestionService(embedder=..., vector_store=...)` need no change.

### 4.7 Lifespan plumbing (apps/api/atlas_api/main.py)

```python
# After session_factory + registry, before vector_store:
from atlas_graph import GraphStore, MigrationRunner, backfill_phase1
from neo4j import AsyncGraphDatabase

graph_driver = AsyncGraphDatabase.driver(
    str(config.graph.uri),
    auth=(config.graph.user, config.graph.password.get_secret_value()),
)
migrations_dir = Path(atlas_graph.__file__).parent / "schema" / "migrations"
applied = await MigrationRunner(graph_driver, migrations_dir).run_pending()
log.info("graph.migrations.applied", ids=applied)
graph_store = GraphStore(graph_driver)
app.state.graph_driver = graph_driver
app.state.graph_store = graph_store

if config.graph.backfill_on_start:
    log.info("graph.backfill.start")
    async with session_scope(app.state.session_factory) as db:
        result = await backfill_phase1(
            db=db, graph=graph_store,
            progress_cb=lambda b, t: log.info("graph.backfill.progress", batch=b, total=t),
        )
    log.info(
        "graph.backfill.done",
        documents=result.documents, chunks=result.chunks, batches=result.batches,
    )

# Then later:
app.state.ingestion_service = IngestionService(
    embedder=embedder, vector_store=vector_store, graph_writer=graph_store,
)

# Shutdown:
finally:
    await graph_store.close()
    await engine.dispose()
```

### 4.8 docker-compose (infra/docker-compose.yml)

```yaml
neo4j:
  image: neo4j:5-community
  container_name: atlas-neo4j
  restart: unless-stopped
  environment:
    NEO4J_AUTH: "neo4j/${ATLAS_GRAPH__PASSWORD}"
    NEO4J_PLUGINS: '["graph-data-science"]'
    NEO4J_dbms_memory_heap_max__size: "2G"
    NEO4J_dbms_memory_pagecache_size: "512M"
    NEO4J_dbms_security_procedures_unrestricted: "gds.*"
  ports:
    - "7474:7474"
    - "7687:7687"
  volumes:
    - neo4j_data:/data
    - neo4j_logs:/logs
  healthcheck:
    test: ["CMD-SHELL", "wget --no-verbose --tries=1 --spider http://localhost:7474 || exit 1"]
    interval: 10s
    timeout: 5s
    retries: 12

# In api:
depends_on:
  postgres:
    condition: service_healthy
  redis:
    condition: service_healthy
  neo4j:
    condition: service_healthy

# At bottom, named volumes:
neo4j_data:
neo4j_logs:
```

`.env.example` gains: `ATLAS_GRAPH__PASSWORD=changeme` and `ATLAS_GRAPH__BACKFILL_ON_START=false`.

---

## 5. Data flow & error handling

| Failure | Where | Surface |
|---|---|---|
| Neo4j down at api startup | `MigrationRunner.run_pending()` | Lifespan raises → api crashes; compose restart loop until neo4j is healthy |
| Cypher syntax error in a migration file | `MigrationRunner._apply` | Lifespan raises with offending file path; api refuses to start |
| Neo4j down at first ingest (lazy probe) | `GraphStore._with_retry` after 3 attempts | `GraphUnavailableError`; `IngestionService` except path marks job `failed` with error string |
| Neo4j transient (single failed attempt) | `GraphStore._with_retry` | Retries with 0.5s → 1s → 2s backoff; second-or-third attempt usually succeeds |
| Backfill crashes mid-run | `backfill_phase1` | `(:BackfillState)` shows last-completed batch; CLI re-run picks up from beginning; MERGE makes already-written nodes no-ops |
| Old test constructs IngestionService(embedder, vector_store) | unchanged | `graph_writer=None` default → step 6 skipped → tests continue to pass |
| Production vs test env: integration test wants real Neo4j | `pytest.mark.integration` + `ATLAS_TEST_NEO4J_URL` env var | Skipped by default; opt-in run hits a real Neo4j |

**Strict invariants:**

- Every `(:Document)` and `(:Chunk)` node has `project_id` plus a `PART_OF` / `BELONGS_TO` edge. Enforced by `write_document_chunks` running all 5 MERGEs in one transaction.
- Migration files are append-only and never edited after merge (Q4=A — no checksum validation).
- `atlas-knowledge` does not import `atlas-graph` (Protocol-based decoupling).

---

## 6. Testing strategy

### 6.1 Unit (mocked driver, no Neo4j)
- `packages/atlas-graph/atlas_graph/tests/test_runner.py`:
  - `MigrationRunner.run_pending()` discovery + ordering + skip-already-applied; gap in id sequence runs both; non-matching filename ignored; multi-statement file split correctly.
  - Driver `session().execute_write(...)` is called the right number of times.
- `test_store.py`:
  - `write_document_chunks` issues 5 expected `tx.run` calls in one transaction, with the right Cypher prefixes and parameter shapes. Asserts on substring matches in the Cypher (e.g. `"MERGE (p:Project"`) — full-text matching is brittle.
  - `_with_retry` retries on `ServiceUnavailable` / `TransientError`, raises `GraphUnavailableError` after `max_retries`, succeeds on transient recovery.
  - `healthcheck()` runs `RETURN 1`.

### 6.2 Backfill (real Postgres, mocked GraphStore)
- `test_backfill.py`:
  - Seeds Postgres with 3 projects × 5 documents × 6 chunks. Runs `backfill_phase1(db, mock_graph)` — asserts 15 `write_document_chunks` calls, each with the right document + chunks. `BackfillState` updated per batch (with `_DOCS_PER_BATCH=50`, all 15 land in one batch).
  - Idempotency: re-run with the same DB → same number of calls, same parameters (the mock can confirm the second run is byte-identical).
  - Edge case: empty Postgres → 0 calls, `BackfillResult(documents=0, chunks=0, batches=0)`.

### 6.3 IngestionService extension
- `packages/atlas-knowledge/atlas_knowledge/tests/test_ingestion_service.py` (extend):
  - Existing tests continue to pass with `graph_writer=None` default.
  - New: with `graph_writer=MagicMock(spec=GraphWriter)`, ingest a markdown doc, assert `write_document_chunks` was called with the right shape.
  - Failure: `graph_writer.write_document_chunks` raises `RuntimeError` → job ends `failed` with the error string in `error`.

### 6.4 Integration (real Neo4j, opt-in)
- `test_runner_integration.py` and `test_store_integration.py` marked `@pytest.mark.integration` and skipped unless `ATLAS_TEST_NEO4J_URL=bolt://localhost:7687` is set:
  - `MigrationRunner` end-to-end: applies `001_initial_schema.cypher` to a clean DB, second run is a no-op, `(:Migration {id:'001'})` exists.
  - `GraphStore.write_document_chunks` writes 1 Document + 3 Chunks; Cypher counts confirm 4 nodes + 4 edges (1 PART_OF, 3 BELONGS_TO). Idempotent re-run unchanged.
  - Both tests use a unique project_id per run + cleanup `MATCH (n) WHERE n.project_id = $tid DETACH DELETE n` in teardown so they don't accumulate.

### 6.5 Lifespan smoke
- `apps/api/atlas_api/tests/test_main_lifespan.py` (extend or add): with `ATLAS_GRAPH__BACKFILL_ON_START=false`, lifespan startup constructs `app.state.graph_store`, runs migrations against a real Neo4j (under `pytest.mark.integration`), and `/health` returns 200.

### 6.6 Manual end-to-end smoke
- `docker compose up -d --build` → neo4j healthy.
- Ingest a markdown doc through the existing modal.
- `docker compose exec neo4j cypher-shell -u neo4j -p $ATLAS_GRAPH__PASSWORD "MATCH (d:Document)-[:PART_OF]->(p:Project) RETURN d.title, p.name"` returns the row.
- Set `ATLAS_GRAPH__BACKFILL_ON_START=true`, restart, confirm Phase 1 corpus now has graph nodes.

---

## 7. Definition of Done

1. `docker compose up` brings `neo4j` up alongside the rest of the stack with no manual steps; `neo4j_data` volume persists.
2. On lifespan startup, `001_initial_schema.cypher` applies; a `(:Migration {id:'001'})` node exists; re-running lifespan is a no-op.
3. An ingestion job (markdown / PDF / URL) writes `(:Document)` + `(:Chunk)` nodes plus `PART_OF` / `BELONGS_TO` edges. Ad-hoc Cypher confirms.
4. Setting `ATLAS_GRAPH__BACKFILL_ON_START=true` once on a fresh stack populates the graph from the Phase 1 corpus.
5. `uv run atlas-graph backfill` runs the same backfill from the CLI and prints batch progress.
6. Neo4j outage at request time produces a `failed` ingestion job with a useful error string; api keeps serving other requests.
7. Existing IngestionService tests still pass without code change (default `graph_writer=None`).
8. Unit + backfill tests pass via `uv run pytest`. Integration tests pass via `ATLAS_TEST_NEO4J_URL=bolt://localhost:7687 uv run pytest -m integration` against a running Neo4j.

---

## 8. Risks

- **Lifespan slowness on cold neo4j start.** The api retries the bolt connection during lifespan — a cold neo4j takes ~30-60 s to be ready. Mitigation: docker `depends_on: condition: service_healthy` blocks api start until neo4j answers HTTP. Healthcheck retries set to 12 × 10 s = 2 min total budget.
- **Backfill on a large corpus.** ~50k chunks × 50 docs/batch = ~50 batches; each batch is one write tx with ~1000 MERGEs. Estimated ~5-10 s on the 2 GB heap. If practice grows past ~500k chunks, batches need tuning — not a Plan 2 problem.
- **Metadata serialization.** Neo4j 5 properties are scalar/list-of-scalar only; nested dicts aren't allowed. `_serialize_metadata` JSON-encodes nested values as strings. Reasonable trade-off; readers (Plan 5) will JSON-decode on display.
- **Schema drift.** Migrations are append-only and not checksummed (Q4=A). If someone edits an applied migration, the runner won't detect it. For a solo project this is acceptable; revisit if the team grows.
- **Neo4j password in `.env`.** Stored as a docker env var. For personal-use single-tenant; don't commit `.env`. `.env.example` documents the variable.
- **`atlas-graph` CLI assumes the api can reach `neo4j:7687`.** The default in `GraphConfig.uri` is `bolt://neo4j:7687` (the docker-compose service name). For host-side runs, `bolt://localhost:7687` works because port 7687 is published.

---

## 9. Open items deferred to per-plan brainstorms (none)

All Plan-2-specific decisions resolved during brainstorm:

- Q1=A — `IngestionService` gets an optional `graph_writer` constructor arg.
- Q2=B — single `GraphStore` on `app.state.graph_store`; driver lives inside.
- Q3=C — lazy connect; per-request errors map to `failed` ingestion jobs.
- Q4=A — file-based migration runner with `(:Migration)` ledger; no checksum.
- Q5=C — backfill is `MERGE`-idempotent; `(:BackfillState)` exists for progress visibility, not resume logic.
- Q6=A — Cypher inlined as string constants inside method bodies.

---

*ATLAS Phase 2 — Plan 2 — Neo4j + Graph Schema + Write Path · 2026-04-28*
