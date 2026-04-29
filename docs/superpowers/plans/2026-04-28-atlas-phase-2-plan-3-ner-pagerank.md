# Phase 2 Plan 3 — NER + Entity Edges + PageRank — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every ingested document writes Entity nodes + REFERENCES/SEMANTICALLY_NEAR/TEMPORAL_NEAR edges and triggers a project-scoped global PageRank, producing the graph data Plan 4 will retrieve over.

**Architecture:** New code lives in `packages/atlas-graph/atlas_graph/ingestion/` (NER + edge builders) and is invoked from `atlas-knowledge.IngestionService.ingest()` via the existing `GraphWriter` Protocol (extended with new methods). NER calls LM Studio (`/v1/chat/completions` with `response_format: json_schema`); cosine-near pairs are computed in IngestionService against the Chroma vector store and passed as primitive tuples to `graph_writer.merge_semantic_near`; temporal edges + PageRank are pure Cypher / `gds` calls. Failure tiering: NER + entity write + semantic + temporal are required (abort + roll back); PageRank is best-effort (try/except, sets `IngestionJob.pagerank_status`).

**Tech Stack:** Python 3.13, `neo4j` async driver, `gds` plugin, `httpx` for LM Studio, Pydantic v2, FastAPI, SQLAlchemy 2 async, Alembic, Chroma, pytest-asyncio.

**Pre-flight:** Verify `git status` shows only the design doc commit on branch `feat/phase-2-plan-3-ner-pagerank`. Run `uv sync` from repo root. Run `pytest packages/atlas-graph packages/atlas-knowledge apps/api -q` and confirm green before starting.

---

## File structure

### New files

| Path | Responsibility |
|---|---|
| `packages/atlas-graph/atlas_graph/ingestion/__init__.py` | Package marker. |
| `packages/atlas-graph/atlas_graph/ingestion/ner.py` | `NerExtractor` LM Studio client + 11-type vocabulary + JSON schema + `extract_batch`. |
| `packages/atlas-graph/atlas_graph/ingestion/entities.py` | `Entity` dataclass + Cypher templates for `MERGE Entity` and `REFERENCES` edges. |
| `packages/atlas-graph/atlas_graph/ingestion/temporal.py` | `temporal_near_cypher()` constant; one-shot Cypher for the rolling 7-day predicate. |
| `packages/atlas-graph/atlas_graph/ingestion/pagerank.py` | `run_pagerank()` — projects subgraph, writes `pagerank_global`, drops projection in a `finally`. |
| `packages/atlas-graph/atlas_graph/schema/migrations/002_entities_and_edges.cypher` | Entity uniqueness + indexes; null-`created_at` fixup on existing Documents. |
| `packages/atlas-graph/atlas_graph/tests/test_ner.py` | NerExtractor unit tests against mocked httpx. |
| `packages/atlas-graph/atlas_graph/tests/test_entities.py` | `write_entities` against fake driver. |
| `packages/atlas-graph/atlas_graph/tests/test_temporal.py` | `build_temporal_near` against fake driver. |
| `packages/atlas-graph/atlas_graph/tests/test_pagerank.py` | `run_pagerank` against fake driver, including failure-cleanup. |
| `packages/atlas-graph/atlas_graph/tests/test_semantic_pairs.py` | `merge_semantic_near` against fake driver, including canonical ordering and self-exclusion. |
| `infra/alembic/versions/0004_add_pagerank_status.py` | Alembic — adds `ingestion_jobs.pagerank_status`. |

### Modified files

| Path | Change |
|---|---|
| `packages/atlas-core/atlas_core/config.py` | New fields on `GraphConfig`: `ner_enabled`, `ner_max_entities_per_chunk`, `semantic_near_threshold`, `semantic_near_top_k`, `temporal_near_window_days`, `pagerank_enabled`. |
| `packages/atlas-core/atlas_core/tests/test_config.py` | Cover the new defaults + env override. |
| `packages/atlas-core/atlas_core/db/orm.py` | `IngestionJobORM` gains `pagerank_status: Mapped[str]`. |
| `packages/atlas-core/atlas_core/tests/test_db_orm.py` | Cover the new column default. |
| `packages/atlas-graph/atlas_graph/protocols.py` | New `ChunkWithText` dataclass. |
| `packages/atlas-graph/atlas_graph/store.py` | New methods: `write_entities`, `merge_semantic_near`, `build_temporal_near`, `run_pagerank`. `write_document_chunks` gains `document_created_at` param. |
| `packages/atlas-graph/atlas_graph/tests/test_store.py` | Update existing `write_document_chunks` tests for the new param. |
| `packages/atlas-graph/atlas_graph/tests/test_store_integration.py` | New end-to-end real-Neo4j case for Plan 3. |
| `packages/atlas-knowledge/atlas_knowledge/ingestion/protocols.py` | New `ChunkWithTextLike` Protocol; extend `GraphWriter` with `write_entities`, `merge_semantic_near`, `build_temporal_near`, `run_pagerank`. `write_document_chunks` gains `document_created_at`. |
| `packages/atlas-knowledge/atlas_knowledge/ingestion/service.py` | New private adapter `_ChunkWithTextAdapter`; new private helper `_compute_semantic_near_pairs`; updated `ingest()` flow; `pagerank_status` writeback on the job row. |
| `packages/atlas-knowledge/atlas_knowledge/tests/test_ingestion_service.py` | Update existing graph_writer mock cases; new cases for entities, semantic-near pairs, temporal-near, pagerank tiering. |
| `apps/api/atlas_api/main.py` | Construct `NerExtractor`, pass it into `GraphStore` (lifespan). |
| `apps/api/atlas_api/tests/test_lifespan_graph.py` | Cover the new wiring. |

---

## Task 1: Add `GraphConfig` flags

**Files:**
- Modify: `packages/atlas-core/atlas_core/config.py:35-44`
- Modify: `packages/atlas-core/atlas_core/tests/test_config.py`

- [ ] **Step 1: Write the failing test**

Append to `packages/atlas-core/atlas_core/tests/test_config.py`:

```python
def test_graph_config_plan3_defaults(monkeypatch):
    """Plan 3 — NER + edge builder + PageRank knobs default to sensible values."""
    monkeypatch.setenv("ATLAS_DB__DATABASE_URL", "postgresql://x")
    monkeypatch.setenv("ATLAS_GRAPH__PASSWORD", "pw")
    cfg = AtlasConfig()
    assert cfg.graph.ner_enabled is True
    assert cfg.graph.ner_max_entities_per_chunk == 20
    assert cfg.graph.semantic_near_threshold == 0.85
    assert cfg.graph.semantic_near_top_k == 50
    assert cfg.graph.temporal_near_window_days == 7
    assert cfg.graph.pagerank_enabled is True


def test_graph_config_plan3_env_override(monkeypatch):
    """All Plan 3 knobs are overridable via ATLAS_GRAPH__* env vars."""
    monkeypatch.setenv("ATLAS_DB__DATABASE_URL", "postgresql://x")
    monkeypatch.setenv("ATLAS_GRAPH__PASSWORD", "pw")
    monkeypatch.setenv("ATLAS_GRAPH__NER_ENABLED", "false")
    monkeypatch.setenv("ATLAS_GRAPH__NER_MAX_ENTITIES_PER_CHUNK", "5")
    monkeypatch.setenv("ATLAS_GRAPH__SEMANTIC_NEAR_THRESHOLD", "0.9")
    monkeypatch.setenv("ATLAS_GRAPH__SEMANTIC_NEAR_TOP_K", "10")
    monkeypatch.setenv("ATLAS_GRAPH__TEMPORAL_NEAR_WINDOW_DAYS", "3")
    monkeypatch.setenv("ATLAS_GRAPH__PAGERANK_ENABLED", "false")
    cfg = AtlasConfig()
    assert cfg.graph.ner_enabled is False
    assert cfg.graph.ner_max_entities_per_chunk == 5
    assert cfg.graph.semantic_near_threshold == 0.9
    assert cfg.graph.semantic_near_top_k == 10
    assert cfg.graph.temporal_near_window_days == 3
    assert cfg.graph.pagerank_enabled is False
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
uv run pytest packages/atlas-core/atlas_core/tests/test_config.py::test_graph_config_plan3_defaults packages/atlas-core/atlas_core/tests/test_config.py::test_graph_config_plan3_env_override -v
```

Expected: FAIL with `AttributeError: 'GraphConfig' object has no attribute 'ner_enabled'` (or similar).

- [ ] **Step 3: Add the new fields**

Edit `packages/atlas-core/atlas_core/config.py`. Replace the `GraphConfig` class body so the fields read:

```python
class GraphConfig(BaseSettings):
    """Neo4j configuration."""

    model_config = SettingsConfigDict(env_prefix="ATLAS_GRAPH__", extra="ignore")

    uri: AnyUrl = Field(default="bolt://neo4j:7687")
    user: str = "neo4j"
    password: SecretStr  # required
    backfill_on_start: bool = False

    # Plan 3 knobs.
    ner_enabled: bool = True
    ner_max_entities_per_chunk: int = 20
    semantic_near_threshold: float = 0.85
    semantic_near_top_k: int = 50
    temporal_near_window_days: int = 7
    pagerank_enabled: bool = True
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
uv run pytest packages/atlas-core/atlas_core/tests/test_config.py -v
```

Expected: all green.

- [ ] **Step 5: Commit**

```bash
git add packages/atlas-core/atlas_core/config.py packages/atlas-core/atlas_core/tests/test_config.py
git commit -m "feat(core/graph): add Plan 3 NER + edge + pagerank config knobs"
```

---

## Task 2: Alembic migration `0004` — `ingestion_jobs.pagerank_status`

**Files:**
- Create: `infra/alembic/versions/0004_add_pagerank_status.py`
- Modify: `packages/atlas-core/atlas_core/db/orm.py:174-198`
- Modify: `packages/atlas-core/atlas_core/tests/test_db_orm.py`

- [ ] **Step 1: Write the failing test**

Append to `packages/atlas-core/atlas_core/tests/test_db_orm.py`:

```python
@pytest.mark.asyncio
async def test_ingestion_job_pagerank_status_default(db_session):
    """A freshly-inserted IngestionJob defaults pagerank_status to 'skipped'."""
    from atlas_core.db.orm import IngestionJobORM, ProjectORM

    proj = ProjectORM(user_id="matt", name="P", default_model="claude-sonnet-4-6")
    db_session.add(proj)
    await db_session.flush()

    job = IngestionJobORM(
        user_id="matt", project_id=proj.id, source_type="markdown",
    )
    db_session.add(job)
    await db_session.flush()
    await db_session.refresh(job)
    assert job.pagerank_status == "skipped"
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
uv run pytest packages/atlas-core/atlas_core/tests/test_db_orm.py::test_ingestion_job_pagerank_status_default -v
```

Expected: FAIL — column doesn't exist.

- [ ] **Step 3: Add the column to ORM**

Edit `packages/atlas-core/atlas_core/db/orm.py`. After the existing `completed_at` column inside `IngestionJobORM`, add:

```python
    pagerank_status: Mapped[str] = mapped_column(
        Text, nullable=False, server_default="skipped"
    )
```

- [ ] **Step 4: Create the alembic migration**

Create `infra/alembic/versions/0004_add_pagerank_status.py`:

```python
"""add pagerank_status to ingestion_jobs

Revision ID: 0004
Revises: 0003
Create Date: 2026-04-28
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "ingestion_jobs",
        sa.Column(
            "pagerank_status",
            sa.Text(),
            nullable=False,
            server_default="skipped",
        ),
    )


def downgrade() -> None:
    op.drop_column("ingestion_jobs", "pagerank_status")
```

- [ ] **Step 5: Apply the migration in the test environment**

The test conftest re-creates the DB schema from ORM each session (via `Base.metadata.create_all`). Re-run:

```bash
uv run pytest packages/atlas-core/atlas_core/tests/test_db_orm.py::test_ingestion_job_pagerank_status_default -v
```

Expected: PASS.

- [ ] **Step 6: Apply the migration against the dev Postgres (manual sanity)**

```bash
cd infra && uv run alembic upgrade head
```

Expected: `INFO  [alembic.runtime.migration] Running upgrade 0003 -> 0004, add pagerank_status to ingestion_jobs`. If alembic is not configured to find the local DB, skip this step — the test is what gates correctness.

- [ ] **Step 7: Commit**

```bash
git add infra/alembic/versions/0004_add_pagerank_status.py packages/atlas-core/atlas_core/db/orm.py packages/atlas-core/atlas_core/tests/test_db_orm.py
git commit -m "feat(core/db): add pagerank_status to ingestion_jobs (alembic 0004)"
```

---

## Task 3: Cypher migration `002_entities_and_edges`

**Files:**
- Create: `packages/atlas-graph/atlas_graph/schema/migrations/002_entities_and_edges.cypher`
- Modify: `packages/atlas-graph/atlas_graph/tests/test_runner.py`
- Modify: `packages/atlas-graph/atlas_graph/tests/test_runner_integration.py`

- [ ] **Step 1: Write the failing test (unit, against fake driver)**

Append to `packages/atlas-graph/atlas_graph/tests/test_runner.py` a test that verifies `002` is discovered, parsed into DDL + write halves, and applied. Read `test_runner.py` for the existing fake-driver pattern; adapt the test to assert that:
1. The DDL statements include `CREATE CONSTRAINT entity_project_name_type` and the two new indexes (`entity_project_id`, `entity_type`).
2. The write statements include the `MATCH (d:Document) WHERE d.created_at IS NULL SET d.created_at = datetime()` fixup.
3. Migration id `002` is recorded in the ledger after run.

```python
@pytest.mark.asyncio
async def test_runner_applies_002_entities_and_edges(tmp_path):
    """Migration 002 is split into DDL (constraints + indexes) and a write fixup."""
    from atlas_graph.schema.runner import MigrationRunner

    migrations_dir = (
        Path(__file__).resolve().parent.parent / "schema" / "migrations"
    )
    driver = _FakeDriver()  # reuse the existing fake-driver helper from this file
    runner = MigrationRunner(driver, migrations_dir)
    applied = await runner.run_pending()
    assert "002" in applied
    statements = driver.executed_statements
    # DDL transaction:
    assert any("CREATE CONSTRAINT entity_project_name_type" in s for s in statements)
    assert any("CREATE INDEX entity_project_id" in s for s in statements)
    assert any("CREATE INDEX entity_type" in s for s in statements)
    # Write transaction:
    assert any(
        "WHERE d.created_at IS NULL" in s and "SET d.created_at = datetime()" in s
        for s in statements
    )
```

If `test_runner.py` does not yet have `_FakeDriver` recording statements, add one or extend the existing one. Read the file first.

- [ ] **Step 2: Run the test to verify it fails**

```bash
uv run pytest packages/atlas-graph/atlas_graph/tests/test_runner.py::test_runner_applies_002_entities_and_edges -v
```

Expected: FAIL because the migration file does not exist.

- [ ] **Step 3: Create the migration file**

Create `packages/atlas-graph/atlas_graph/schema/migrations/002_entities_and_edges.cypher`:

```cypher
CREATE CONSTRAINT entity_project_name_type IF NOT EXISTS
  FOR (e:Entity) REQUIRE (e.project_id, e.name, e.type) IS UNIQUE;
CREATE INDEX entity_project_id IF NOT EXISTS
  FOR (e:Entity) ON (e.project_id);
CREATE INDEX entity_type IF NOT EXISTS
  FOR (e:Entity) ON (e.type);
MATCH (d:Document) WHERE d.created_at IS NULL SET d.created_at = datetime();
```

> **Note on the `created_at` fixup:** sets *migration time* on existing nulls, not the original ingestion time. For the few Plan 2-era documents this is acceptable: they will all cluster as `TEMPORAL_NEAR` of each other (they were ingested in the same session anyway), and going forward Task 4 sets the actual ingestion timestamp. We deliberately avoid extending the migration runner with Python-script support to keep this PR small.

- [ ] **Step 4: Run the test to verify it passes**

```bash
uv run pytest packages/atlas-graph/atlas_graph/tests/test_runner.py::test_runner_applies_002_entities_and_edges -v
```

Expected: PASS.

- [ ] **Step 5: Run the broader test suite to ensure no regressions**

```bash
uv run pytest packages/atlas-graph -q
```

Expected: green.

- [ ] **Step 6: Optional integration check (gated)**

If `ATLAS_GRAPH__INTEGRATION=1` is set (real Neo4j running), `test_runner_integration.py` should now apply both 001 and 002 cleanly. Read that file; if it references "applied migrations" by id, add `"002"` to the assertion. Otherwise leave alone.

- [ ] **Step 7: Commit**

```bash
git add packages/atlas-graph/atlas_graph/schema/migrations/002_entities_and_edges.cypher packages/atlas-graph/atlas_graph/tests/test_runner.py
git commit -m "feat(graph/schema): add 002 entities + edge indexes + created_at fixup"
```

---

## Task 4: Update `write_document_chunks` to accept and set `Document.created_at`

**Files:**
- Modify: `packages/atlas-graph/atlas_graph/store.py:82-146`
- Modify: `packages/atlas-knowledge/atlas_knowledge/ingestion/protocols.py:24-38`
- Modify: `packages/atlas-knowledge/atlas_knowledge/ingestion/service.py:111-188`
- Modify: `packages/atlas-graph/atlas_graph/tests/test_store.py`
- Modify: `packages/atlas-knowledge/atlas_knowledge/tests/test_ingestion_service.py:113-144`

- [ ] **Step 1: Write the failing test (graph store)**

Append to `packages/atlas-graph/atlas_graph/tests/test_store.py`:

```python
@pytest.mark.asyncio
async def test_write_document_chunks_sets_created_at():
    """Document.created_at must be set from the document_created_at parameter."""
    driver = _FakeAsyncDriver()  # use the existing fake driver fixture
    store = GraphStore(driver)
    ts = datetime(2026, 4, 28, 12, 0, 0, tzinfo=UTC)

    await store.write_document_chunks(
        project_id=uuid4(),
        project_name="P",
        document_id=uuid4(),
        document_title="t",
        document_source_type="markdown",
        document_metadata={},
        document_created_at=ts,
        chunks=[],
    )

    # Find the Document MERGE statement and verify created_at parameter.
    doc_calls = [c for c in driver.calls if "MERGE (d:Document" in c.query]
    assert len(doc_calls) == 1
    assert doc_calls[0].kwargs["created_at"] == ts.isoformat()
```

(Adapt to whatever fixture pattern `test_store.py` already uses; read the file before writing.)

- [ ] **Step 2: Run the test to verify it fails**

```bash
uv run pytest packages/atlas-graph/atlas_graph/tests/test_store.py::test_write_document_chunks_sets_created_at -v
```

Expected: FAIL — `write_document_chunks` does not accept `document_created_at`.

- [ ] **Step 3: Update the GraphWriter Protocol**

Edit `packages/atlas-knowledge/atlas_knowledge/ingestion/protocols.py`. Update `GraphWriter.write_document_chunks` signature so the body reads:

```python
class GraphWriter(Protocol):
    """Side-effect interface for writing document/chunk nodes to a graph store."""

    async def write_document_chunks(
        self,
        *,
        project_id: UUID,
        project_name: str,
        document_id: UUID,
        document_title: str,
        document_source_type: str,
        document_metadata: dict,
        document_created_at: datetime,
        chunks: Sequence[ChunkSpecLike],
    ) -> None: ...
```

Add `from datetime import datetime` to the imports at the top of the file.

- [ ] **Step 4: Update `GraphStore.write_document_chunks`**

Edit `packages/atlas-graph/atlas_graph/store.py`. Update `write_document_chunks` so it:
1. Adds `document_created_at: datetime` to the keyword args (between `document_metadata` and `chunks`).
2. Sets `d.created_at = $created_at` in the Document `SET` clause.
3. Passes `created_at=document_created_at.isoformat()` as a parameter.

The updated `MERGE (d:Document …)` Cypher inside the inner `_do` function becomes:

```python
            await tx.run(
                "MERGE (d:Document {id: $id}) "
                "SET d.project_id = $project_id, d.title = $title, "
                "    d.source_type = $source_type, d.metadata = $metadata, "
                "    d.created_at = $created_at",
                id=str(document_id),
                project_id=str(project_id),
                title=document_title,
                source_type=document_source_type,
                metadata=meta,
                created_at=document_created_at.isoformat(),
            )
```

Add `from datetime import datetime` to the imports.

- [ ] **Step 5: Update `IngestionService.ingest()` callers**

Edit `packages/atlas-knowledge/atlas_knowledge/ingestion/service.py`. There are two `await self._graph_writer.write_document_chunks(...)` call sites (the empty-chunks path around line 114 and the populated path around line 172). Each needs `document_created_at=doc_row.created_at or datetime.now(UTC)` added to the kwargs.

The fallback `or datetime.now(UTC)` covers the rare case where Postgres has not yet stamped `created_at` at flush time on this driver (it always does for these tables, but the fallback is defensive and free).

- [ ] **Step 6: Update Plan 2 test fixtures**

Edit `packages/atlas-knowledge/atlas_knowledge/tests/test_ingestion_service.py`. The mock-graph-writer cases (around lines 113-144) assert `kwargs` shape; add:

```python
    assert "document_created_at" in kwargs
    assert isinstance(kwargs["document_created_at"], datetime)
```

Add `from datetime import datetime` if not already imported.

- [ ] **Step 7: Run all affected tests**

```bash
uv run pytest packages/atlas-graph/atlas_graph/tests/test_store.py packages/atlas-knowledge/atlas_knowledge/tests/test_ingestion_service.py -v
```

Expected: green.

- [ ] **Step 8: Commit**

```bash
git add packages/atlas-graph/atlas_graph/store.py packages/atlas-graph/atlas_graph/tests/test_store.py packages/atlas-knowledge/atlas_knowledge/ingestion/protocols.py packages/atlas-knowledge/atlas_knowledge/ingestion/service.py packages/atlas-knowledge/atlas_knowledge/tests/test_ingestion_service.py
git commit -m "feat(graph): write Document.created_at on every ingest"
```

---

## Task 5: `NerExtractor` — LM Studio NER client

**Files:**
- Create: `packages/atlas-graph/atlas_graph/ingestion/__init__.py`
- Create: `packages/atlas-graph/atlas_graph/ingestion/ner.py`
- Create: `packages/atlas-graph/atlas_graph/tests/test_ner.py`

- [ ] **Step 1: Create the package marker**

Create `packages/atlas-graph/atlas_graph/ingestion/__init__.py` — empty file.

- [ ] **Step 2: Write the failing tests**

Create `packages/atlas-graph/atlas_graph/tests/test_ner.py`:

```python
"""NerExtractor unit tests — LM Studio client mocked at the httpx layer."""
from __future__ import annotations

import json
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest

from atlas_graph.ingestion.ner import (
    ENTITY_TYPES,
    Entity,
    NerExtractor,
    NerFailure,
)


def _ok_response(entities: list[dict]) -> httpx.Response:
    body = {
        "choices": [
            {
                "message": {
                    "content": json.dumps({"entities": entities}),
                }
            }
        ]
    }
    return httpx.Response(200, json=body)


@pytest.mark.asyncio
async def test_extract_batch_returns_entities_per_chunk():
    client = AsyncMock(spec=httpx.AsyncClient)
    client.post.side_effect = [
        _ok_response([{"name": "CircleK", "type": "CLIENT"}]),
        _ok_response([{"name": "MMM", "type": "METHOD"}]),
    ]
    extractor = NerExtractor(client=client, base_url="http://lms.local/v1", max_entities=20)

    cid_a, cid_b = uuid4(), uuid4()
    out = await extractor.extract_batch([(cid_a, "we worked with CircleK"), (cid_b, "MMM applied")])
    assert out == {
        cid_a: [Entity(name="CircleK", type="CLIENT")],
        cid_b: [Entity(name="MMM", type="METHOD")],
    }


@pytest.mark.asyncio
async def test_extract_batch_enforces_20_cap():
    """If LLM returns more than max_entities, only the first N are kept."""
    client = AsyncMock(spec=httpx.AsyncClient)
    too_many = [{"name": f"E{i}", "type": "CONCEPT" if False else "METHOD"} for i in range(50)]
    client.post.return_value = _ok_response(too_many)
    extractor = NerExtractor(client=client, base_url="http://lms.local/v1", max_entities=20)

    cid = uuid4()
    out = await extractor.extract_batch([(cid, "blah")])
    assert len(out[cid]) == 20
    assert out[cid][0].name == "E0"
    assert out[cid][-1].name == "E19"


@pytest.mark.asyncio
async def test_extract_batch_filters_unknown_types_and_empty_names():
    client = AsyncMock(spec=httpx.AsyncClient)
    client.post.return_value = _ok_response([
        {"name": "CircleK", "type": "CLIENT"},
        {"name": "X", "type": "BOGUS"},
        {"name": "", "type": "METHOD"},
    ])
    extractor = NerExtractor(client=client, base_url="http://lms.local/v1", max_entities=20)

    cid = uuid4()
    out = await extractor.extract_batch([(cid, "blah")])
    assert out[cid] == [Entity(name="CircleK", type="CLIENT")]


@pytest.mark.asyncio
async def test_extract_batch_retries_on_5xx_then_succeeds():
    client = AsyncMock(spec=httpx.AsyncClient)
    client.post.side_effect = [
        httpx.Response(503),
        _ok_response([{"name": "CircleK", "type": "CLIENT"}]),
    ]
    extractor = NerExtractor(client=client, base_url="http://lms.local/v1", max_entities=20)

    cid = uuid4()
    out = await extractor.extract_batch([(cid, "blah")])
    assert out[cid] == [Entity(name="CircleK", type="CLIENT")]
    assert client.post.call_count == 2


@pytest.mark.asyncio
async def test_extract_batch_raises_after_second_failure():
    client = AsyncMock(spec=httpx.AsyncClient)
    client.post.side_effect = [httpx.Response(500), httpx.Response(500)]
    extractor = NerExtractor(client=client, base_url="http://lms.local/v1", max_entities=20)

    with pytest.raises(NerFailure):
        await extractor.extract_batch([(uuid4(), "blah")])


@pytest.mark.asyncio
async def test_extract_batch_raises_on_persistent_malformed_json():
    client = AsyncMock(spec=httpx.AsyncClient)
    bad = httpx.Response(200, json={"choices": [{"message": {"content": "not-json"}}]})
    client.post.side_effect = [bad, bad]
    extractor = NerExtractor(client=client, base_url="http://lms.local/v1", max_entities=20)

    with pytest.raises(NerFailure):
        await extractor.extract_batch([(uuid4(), "blah")])


def test_entity_types_contains_all_eleven():
    """Drift protection: design lists exactly these eleven types."""
    expected = {
        "CLIENT", "METHOD", "METRIC", "TOOL", "PERSON", "ORG",
        "LOCATION", "TIME_PERIOD", "INDUSTRY", "CONTACT_INFO", "DATA_SOURCE",
    }
    assert set(ENTITY_TYPES) == expected
```

- [ ] **Step 3: Run the tests to verify they fail**

```bash
uv run pytest packages/atlas-graph/atlas_graph/tests/test_ner.py -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'atlas_graph.ingestion.ner'`.

- [ ] **Step 4: Implement `NerExtractor`**

Create `packages/atlas-graph/atlas_graph/ingestion/ner.py`:

```python
"""NerExtractor — extracts typed entities from chunk text via an LM Studio HTTP call.

LM Studio speaks the OpenAI chat-completions API and supports
``response_format: json_schema`` for structured output. We send one request per
chunk in parallel via asyncio.gather, retry once on 5xx / malformed-JSON, and
raise NerFailure if either retry also fails.
"""
from __future__ import annotations

import asyncio
import json
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final
from uuid import UUID

import httpx
import structlog

log = structlog.get_logger("atlas.graph.ner")

ENTITY_TYPES: Final[tuple[str, ...]] = (
    "CLIENT",
    "METHOD",
    "METRIC",
    "TOOL",
    "PERSON",
    "ORG",
    "LOCATION",
    "TIME_PERIOD",
    "INDUSTRY",
    "CONTACT_INFO",
    "DATA_SOURCE",
)


@dataclass(frozen=True)
class Entity:
    name: str
    type: str


class NerFailure(RuntimeError):
    """Raised when NER fails after the single allowed retry."""


_SYSTEM_PROMPT = """\
You extract typed entities from consulting documents. Return strict JSON matching the schema.

Types:
- CLIENT: companies the author works with or about (e.g. "CircleK", "Wendy's").
- METHOD: methodologies, frameworks, techniques (e.g. "geo lift", "MMM", "incrementality testing").
- METRIC: KPIs, financial measures (e.g. "CAC", "ROAS", "LTV").
- TOOL: software, platforms, vendors (e.g. "Snowflake", "GA4"). If the same name is referenced as a *data source*, prefer DATA_SOURCE.
- PERSON: individuals named in the text.
- ORG: non-client organizations (vendors, agencies, regulators).
- LOCATION: geographic context (e.g. "EMEA", "California").
- TIME_PERIOD: named time windows (e.g. "Q3 2025", "2024 holiday season").
- INDUSTRY: sectors (e.g. "QSR", "DTC retail").
- CONTACT_INFO: emails, phone numbers, addresses.
- DATA_SOURCE: datasets, public data sources, third-party panels (e.g. "Nielsen panel", "Census 2020").

Rules:
- Skip generic words. Only entities that would be useful as graph nodes for retrieval.
- Each entity ONE type only.
- Return at most 20 entities.
- Order by importance (most central concept first).
"""


_RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "entities": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "type": {"type": "string", "enum": list(ENTITY_TYPES)},
                },
                "required": ["name", "type"],
            },
        }
    },
    "required": ["entities"],
}


class NerExtractor:
    def __init__(
        self,
        *,
        client: httpx.AsyncClient,
        base_url: str,
        max_entities: int,
        request_timeout: float = 30.0,
    ) -> None:
        self._client = client
        self._base_url = base_url.rstrip("/")
        self._max_entities = max_entities
        self._timeout = request_timeout

    async def extract_batch(
        self, chunks: Sequence[tuple[UUID, str]]
    ) -> dict[UUID, list[Entity]]:
        results = await asyncio.gather(
            *(self._extract_one(text) for _, text in chunks),
            return_exceptions=False,
        )
        return {chunk_id: ents for (chunk_id, _), ents in zip(chunks, results, strict=True)}

    async def _extract_one(self, text: str) -> list[Entity]:
        payload = {
            "model": "ner",  # LM Studio ignores model name when one is loaded
            "messages": [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": text},
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": "entities", "schema": _RESPONSE_SCHEMA},
            },
        }
        last_err: Exception | None = None
        for attempt in range(2):
            try:
                resp = await self._client.post(
                    f"{self._base_url}/chat/completions",
                    json=payload,
                    timeout=self._timeout,
                )
                if resp.status_code >= 500:
                    raise NerFailure(f"LM Studio HTTP {resp.status_code}")
                resp.raise_for_status()
                content = resp.json()["choices"][0]["message"]["content"]
                parsed = json.loads(content)
                return self._validate(parsed.get("entities", []))
            except (NerFailure, httpx.HTTPError, json.JSONDecodeError, KeyError) as e:
                log.warning("ner.attempt_failed", attempt=attempt, error=str(e))
                last_err = e
        raise NerFailure(f"NER failed after retry: {last_err}")

    def _validate(self, raw: list[dict]) -> list[Entity]:
        out: list[Entity] = []
        valid_types = set(ENTITY_TYPES)
        for item in raw[: self._max_entities]:
            name = (item.get("name") or "").strip()
            etype = item.get("type")
            if not name or etype not in valid_types:
                continue
            out.append(Entity(name=name, type=etype))
        return out
```

- [ ] **Step 5: Run the tests to verify they pass**

```bash
uv run pytest packages/atlas-graph/atlas_graph/tests/test_ner.py -v
```

Expected: green.

- [ ] **Step 6: Commit**

```bash
git add packages/atlas-graph/atlas_graph/ingestion/__init__.py packages/atlas-graph/atlas_graph/ingestion/ner.py packages/atlas-graph/atlas_graph/tests/test_ner.py
git commit -m "feat(graph/ner): add NerExtractor — LM Studio JSON-schema entity extraction"
```

---

## Task 6: `GraphStore.write_entities` + `Entity` dataclass

**Files:**
- Create: `packages/atlas-graph/atlas_graph/ingestion/entities.py`
- Modify: `packages/atlas-graph/atlas_graph/store.py`
- Modify: `packages/atlas-graph/atlas_graph/protocols.py`
- Create: `packages/atlas-graph/atlas_graph/tests/test_entities.py`

- [ ] **Step 1: Add `ChunkWithText` to `protocols.py`**

Append to `packages/atlas-graph/atlas_graph/protocols.py`:

```python
@dataclass(frozen=True)
class ChunkWithText:
    """Chunk shape needed by GraphStore.write_entities — full text required for NER."""

    id: UUID
    text: str
```

- [ ] **Step 2: Add `ChunkWithTextLike` Protocol to atlas-knowledge**

Append to `packages/atlas-knowledge/atlas_knowledge/ingestion/protocols.py`:

```python
class ChunkWithTextLike(Protocol):
    """Minimal duck-type a chunk passed to write_entities must satisfy."""

    id: UUID
    text: str
```

Then update `class GraphWriter(Protocol)` in the same file by appending:

```python
    async def write_entities(
        self,
        *,
        project_id: UUID,
        chunks: Sequence[ChunkWithTextLike],
    ) -> None: ...
```

- [ ] **Step 3: Create the entities helper module**

Create `packages/atlas-graph/atlas_graph/ingestion/entities.py`:

```python
"""Entity-and-REFERENCES Cypher helpers shared by GraphStore.write_entities."""
from __future__ import annotations

from collections.abc import Iterable
from uuid import UUID

from atlas_graph.ingestion.ner import Entity

# UNWIND-shaped param row.
def to_entity_param(project_id: UUID, e: Entity) -> dict:
    return {"project_id": str(project_id), "name": e.name, "type": e.type}


def to_reference_param(project_id: UUID, chunk_id: UUID, e: Entity) -> dict:
    return {
        "project_id": str(project_id),
        "chunk_id": str(chunk_id),
        "name": e.name,
        "type": e.type,
    }


MERGE_ENTITIES_CYPHER = (
    "UNWIND $entities AS row "
    "MERGE (e:Entity {project_id: row.project_id, name: row.name, type: row.type})"
)


MERGE_REFERENCES_CYPHER = (
    "UNWIND $references AS ref "
    "MATCH (c:Chunk {id: ref.chunk_id}), "
    "      (e:Entity {project_id: ref.project_id, name: ref.name, type: ref.type}) "
    "MERGE (c)-[:REFERENCES]->(e)"
)


def flatten(
    project_id: UUID,
    chunk_entities: dict[UUID, list[Entity]],
) -> tuple[list[dict], list[dict]]:
    """Return (entity_params, reference_params) deduped by entity identity."""
    seen: set[tuple[str, str]] = set()
    entities: list[dict] = []
    references: list[dict] = []
    for chunk_id, ents in chunk_entities.items():
        for e in ents:
            key = (e.name, e.type)
            if key not in seen:
                seen.add(key)
                entities.append(to_entity_param(project_id, e))
            references.append(to_reference_param(project_id, chunk_id, e))
    return entities, references
```

- [ ] **Step 4: Write the failing test for `GraphStore.write_entities`**

Create `packages/atlas-graph/atlas_graph/tests/test_entities.py`:

```python
"""GraphStore.write_entities — fake-driver tests."""
from __future__ import annotations

from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from atlas_graph.ingestion.ner import Entity, NerExtractor
from atlas_graph.protocols import ChunkWithText
from atlas_graph.store import GraphStore


class _FakeNer(NerExtractor):
    def __init__(self, mapping):
        self._mapping = mapping

    async def extract_batch(self, chunks):
        return {chunk_id: self._mapping.get(chunk_id, []) for chunk_id, _ in chunks}


@pytest.mark.asyncio
async def test_write_entities_unwinds_entities_and_references(fake_async_driver):
    """write_entities issues two UNWIND statements: MERGE Entity + MERGE REFERENCES."""
    pid = uuid4()
    cid_a = uuid4()
    cid_b = uuid4()
    ner = _FakeNer({
        cid_a: [Entity(name="CircleK", type="CLIENT")],
        cid_b: [Entity(name="MMM", type="METHOD")],
    })
    store = GraphStore(fake_async_driver, ner_extractor=ner)

    await store.write_entities(
        project_id=pid,
        chunks=[
            ChunkWithText(id=cid_a, text="..."),
            ChunkWithText(id=cid_b, text="..."),
        ],
    )

    queries = [c.query for c in fake_async_driver.calls]
    assert any("MERGE (e:Entity" in q for q in queries)
    assert any("MERGE (c)-[:REFERENCES]->(e)" in q for q in queries)


@pytest.mark.asyncio
async def test_write_entities_dedupes_repeated_entity_within_batch(fake_async_driver):
    """If two chunks each reference 'CircleK' CLIENT, MERGE Entity is called once with one row."""
    pid = uuid4()
    cid_a, cid_b = uuid4(), uuid4()
    ner = _FakeNer({
        cid_a: [Entity(name="CircleK", type="CLIENT")],
        cid_b: [Entity(name="CircleK", type="CLIENT")],
    })
    store = GraphStore(fake_async_driver, ner_extractor=ner)

    await store.write_entities(
        project_id=pid,
        chunks=[ChunkWithText(id=cid_a, text="x"), ChunkWithText(id=cid_b, text="y")],
    )

    entity_calls = [c for c in fake_async_driver.calls if "MERGE (e:Entity" in c.query]
    assert len(entity_calls) == 1
    assert len(entity_calls[0].kwargs["entities"]) == 1
    ref_calls = [c for c in fake_async_driver.calls if "REFERENCES" in c.query]
    assert len(ref_calls) == 1
    assert len(ref_calls[0].kwargs["references"]) == 2


@pytest.mark.asyncio
async def test_write_entities_skips_when_chunks_empty(fake_async_driver):
    """No chunks → no Cypher calls."""
    store = GraphStore(fake_async_driver, ner_extractor=_FakeNer({}))
    await store.write_entities(project_id=uuid4(), chunks=[])
    assert fake_async_driver.calls == []
```

(`fake_async_driver` fixture: read `tests/conftest.py` and `tests/test_store.py` first to find the existing fake-driver pattern; reuse or extend. If you need to extend, add a `calls` list that records `(query, kwargs)` for each `tx.run` call.)

- [ ] **Step 5: Run the test to verify it fails**

```bash
uv run pytest packages/atlas-graph/atlas_graph/tests/test_entities.py -v
```

Expected: FAIL — `GraphStore.__init__` doesn't accept `ner_extractor`.

- [ ] **Step 6: Implement `GraphStore.write_entities`**

Edit `packages/atlas-graph/atlas_graph/store.py`. Update `GraphStore.__init__` to accept an optional `ner_extractor`:

```python
    def __init__(
        self,
        driver: AsyncDriver,
        *,
        max_retries: int = 3,
        ner_extractor: "NerExtractor | None" = None,
    ) -> None:
        self._driver = driver
        self._max_retries = max_retries
        self._ner_extractor = ner_extractor
```

Add a guarded import at module-level (under `TYPE_CHECKING` for type, plus a runtime import inside the method that uses it to avoid making NER a required dep at import time):

```python
if TYPE_CHECKING:
    from atlas_graph.ingestion.ner import NerExtractor
```

Add a new method on `GraphStore`:

```python
    async def write_entities(
        self,
        *,
        project_id: UUID,
        chunks: Sequence[ChunkWithText],
    ) -> None:
        """Run NER over chunk text and MERGE Entity nodes + REFERENCES edges.

        No-op on empty chunks. Raises NerFailure if LM Studio is unreachable
        (inherits the abort-on-failure tier per Plan 3 §3.4).
        """
        if not chunks or self._ner_extractor is None:
            return
        from atlas_graph.ingestion.entities import (
            MERGE_ENTITIES_CYPHER,
            MERGE_REFERENCES_CYPHER,
            flatten,
        )

        chunk_entities = await self._ner_extractor.extract_batch(
            [(c.id, c.text) for c in chunks]
        )
        entities, references = flatten(project_id, chunk_entities)
        if not entities:
            return  # NER returned nothing; nothing to write.

        async def _do(tx: AsyncTransaction) -> None:
            await tx.run(MERGE_ENTITIES_CYPHER, entities=entities)
            if references:
                await tx.run(MERGE_REFERENCES_CYPHER, references=references)

        await self._with_retry(_do)
```

Add the `ChunkWithText` import at the top: `from atlas_graph.protocols import ChunkSpec, ChunkWithText`.

- [ ] **Step 7: Run the tests**

```bash
uv run pytest packages/atlas-graph/atlas_graph/tests/test_entities.py -v
```

Expected: green.

- [ ] **Step 8: Commit**

```bash
git add packages/atlas-graph/atlas_graph/ingestion/entities.py packages/atlas-graph/atlas_graph/protocols.py packages/atlas-graph/atlas_graph/store.py packages/atlas-graph/atlas_graph/tests/test_entities.py packages/atlas-knowledge/atlas_knowledge/ingestion/protocols.py
git commit -m "feat(graph): GraphStore.write_entities — NER + Entity MERGE + REFERENCES"
```

---

## Task 7: `GraphStore.merge_semantic_near` (writes pre-computed pairs)

**Files:**
- Modify: `packages/atlas-graph/atlas_graph/store.py`
- Modify: `packages/atlas-knowledge/atlas_knowledge/ingestion/protocols.py`
- Create: `packages/atlas-graph/atlas_graph/tests/test_semantic_pairs.py`

> **Note on design deviation:** the design doc §3.5 / §4.4 places the Chroma top-K query inside `GraphStore.build_semantic_near`. We split that work: the Chroma query and pair computation move to `IngestionService` (Task 11), and `GraphStore` exposes only `merge_semantic_near(pairs)` which writes pre-computed `(chunk_a_id, chunk_b_id, cosine)` tuples. This avoids `atlas-graph` taking a (Protocol-or-actual) dependency on `atlas-knowledge`'s `VectorStore`. Net behavior is identical.

- [ ] **Step 1: Extend the GraphWriter Protocol**

Append to `class GraphWriter(Protocol)` in `packages/atlas-knowledge/atlas_knowledge/ingestion/protocols.py`:

```python
    async def merge_semantic_near(
        self,
        *,
        pairs: Sequence[tuple[UUID, UUID, float]],
    ) -> None: ...
```

- [ ] **Step 2: Write the failing tests**

Create `packages/atlas-graph/atlas_graph/tests/test_semantic_pairs.py`:

```python
"""GraphStore.merge_semantic_near — fake-driver tests for canonical pair writes."""
from __future__ import annotations

from uuid import uuid4

import pytest

from atlas_graph.store import GraphStore


@pytest.mark.asyncio
async def test_merge_semantic_near_unwinds_pairs(fake_async_driver):
    """merge_semantic_near issues a single UNWIND MERGE for all pairs."""
    a, b, c = uuid4(), uuid4(), uuid4()
    pairs = [(a, b, 0.91), (a, c, 0.88)]
    store = GraphStore(fake_async_driver)

    await store.merge_semantic_near(pairs=pairs)

    queries = [call.query for call in fake_async_driver.calls]
    assert any(
        "UNWIND $pairs AS p" in q and "MERGE (x)-[r:SEMANTICALLY_NEAR]-(y)" in q
        for q in queries
    )
    write_call = next(
        c for c in fake_async_driver.calls if "SEMANTICALLY_NEAR" in c.query
    )
    written = write_call.kwargs["pairs"]
    # IDs are stringified for Cypher.
    assert {p["a"] for p in written} | {p["b"] for p in written} == {str(a), str(b), str(c)}


@pytest.mark.asyncio
async def test_merge_semantic_near_no_op_on_empty(fake_async_driver):
    store = GraphStore(fake_async_driver)
    await store.merge_semantic_near(pairs=[])
    assert fake_async_driver.calls == []


@pytest.mark.asyncio
async def test_merge_semantic_near_stores_cosine_on_edge(fake_async_driver):
    a, b = uuid4(), uuid4()
    store = GraphStore(fake_async_driver)
    await store.merge_semantic_near(pairs=[(a, b, 0.91)])
    write_call = next(
        c for c in fake_async_driver.calls if "SEMANTICALLY_NEAR" in c.query
    )
    written = write_call.kwargs["pairs"][0]
    assert written["cosine"] == 0.91
```

- [ ] **Step 3: Run the tests to verify they fail**

```bash
uv run pytest packages/atlas-graph/atlas_graph/tests/test_semantic_pairs.py -v
```

Expected: FAIL — `merge_semantic_near` does not exist.

- [ ] **Step 4: Implement `merge_semantic_near`**

Add to `GraphStore` in `packages/atlas-graph/atlas_graph/store.py`:

```python
    async def merge_semantic_near(
        self,
        *,
        pairs: Sequence[tuple[UUID, UUID, float]],
    ) -> None:
        """MERGE undirected SEMANTICALLY_NEAR edges with cosine on the relation.

        Caller is expected to canonicalize ``(a, b)`` so the same pair is not
        passed twice; we don't dedupe inside this method to keep it cheap.
        """
        if not pairs:
            return
        params = [
            {"a": str(a), "b": str(b), "cosine": float(score)}
            for (a, b, score) in pairs
        ]

        async def _do(tx: AsyncTransaction) -> None:
            await tx.run(
                "UNWIND $pairs AS p "
                "MATCH (x:Chunk {id: p.a}), (y:Chunk {id: p.b}) "
                "MERGE (x)-[r:SEMANTICALLY_NEAR]-(y) "
                "SET r.cosine = p.cosine",
                pairs=params,
            )

        await self._with_retry(_do)
```

- [ ] **Step 5: Run the tests**

```bash
uv run pytest packages/atlas-graph/atlas_graph/tests/test_semantic_pairs.py -v
```

Expected: green.

- [ ] **Step 6: Commit**

```bash
git add packages/atlas-graph/atlas_graph/store.py packages/atlas-graph/atlas_graph/tests/test_semantic_pairs.py packages/atlas-knowledge/atlas_knowledge/ingestion/protocols.py
git commit -m "feat(graph): GraphStore.merge_semantic_near — UNWIND MERGE pairs"
```

---

## Task 8: `GraphStore.build_temporal_near`

**Files:**
- Create: `packages/atlas-graph/atlas_graph/ingestion/temporal.py`
- Modify: `packages/atlas-graph/atlas_graph/store.py`
- Modify: `packages/atlas-knowledge/atlas_knowledge/ingestion/protocols.py`
- Create: `packages/atlas-graph/atlas_graph/tests/test_temporal.py`

- [ ] **Step 1: Extend the Protocol**

Append to `class GraphWriter(Protocol)` in `packages/atlas-knowledge/atlas_knowledge/ingestion/protocols.py`:

```python
    async def build_temporal_near(
        self,
        *,
        project_id: UUID,
        document_id: UUID,
        window_days: int,
    ) -> None: ...
```

- [ ] **Step 2: Write the Cypher constant**

Create `packages/atlas-graph/atlas_graph/ingestion/temporal.py`:

```python
"""TEMPORAL_NEAR — same-project Documents within a rolling N-day window."""

# Both endpoints must have a non-null created_at; null comparisons in
# duration.between would evaluate to null and silently drop the predicate.
TEMPORAL_NEAR_CYPHER = (
    "MATCH (d_new:Document {id: $document_id}), (d:Document) "
    "WHERE d.project_id = $project_id "
    "  AND d.id <> d_new.id "
    "  AND d.created_at IS NOT NULL "
    "  AND d_new.created_at IS NOT NULL "
    "  AND duration.between(datetime(d.created_at), datetime(d_new.created_at)).days "
    "      <= $window_days "
    "  AND duration.between(datetime(d.created_at), datetime(d_new.created_at)).days "
    "      >= -$window_days "
    "MERGE (d_new)-[:TEMPORAL_NEAR]-(d)"
)
```

> **Why both bounds:** `duration.between(a, b).days` is signed. A new doc ingested *before* an existing doc would yield a negative day delta. We accept `[-N, +N]`.

- [ ] **Step 3: Write the failing test**

Create `packages/atlas-graph/atlas_graph/tests/test_temporal.py`:

```python
"""GraphStore.build_temporal_near — fake-driver tests."""
from __future__ import annotations

from uuid import uuid4

import pytest

from atlas_graph.ingestion.temporal import TEMPORAL_NEAR_CYPHER
from atlas_graph.store import GraphStore


@pytest.mark.asyncio
async def test_build_temporal_near_runs_temporal_cypher(fake_async_driver):
    pid, did = uuid4(), uuid4()
    store = GraphStore(fake_async_driver)
    await store.build_temporal_near(project_id=pid, document_id=did, window_days=7)

    call = next(c for c in fake_async_driver.calls if "TEMPORAL_NEAR" in c.query)
    assert call.query == TEMPORAL_NEAR_CYPHER
    assert call.kwargs["document_id"] == str(did)
    assert call.kwargs["project_id"] == str(pid)
    assert call.kwargs["window_days"] == 7
```

- [ ] **Step 4: Run to verify failure**

```bash
uv run pytest packages/atlas-graph/atlas_graph/tests/test_temporal.py -v
```

Expected: FAIL — method doesn't exist.

- [ ] **Step 5: Implement the method**

Add to `GraphStore` in `packages/atlas-graph/atlas_graph/store.py`:

```python
    async def build_temporal_near(
        self,
        *,
        project_id: UUID,
        document_id: UUID,
        window_days: int,
    ) -> None:
        from atlas_graph.ingestion.temporal import TEMPORAL_NEAR_CYPHER

        async def _do(tx: AsyncTransaction) -> None:
            await tx.run(
                TEMPORAL_NEAR_CYPHER,
                project_id=str(project_id),
                document_id=str(document_id),
                window_days=int(window_days),
            )

        await self._with_retry(_do)
```

- [ ] **Step 6: Run the tests**

```bash
uv run pytest packages/atlas-graph/atlas_graph/tests/test_temporal.py -v
```

Expected: green.

- [ ] **Step 7: Commit**

```bash
git add packages/atlas-graph/atlas_graph/ingestion/temporal.py packages/atlas-graph/atlas_graph/store.py packages/atlas-graph/atlas_graph/tests/test_temporal.py packages/atlas-knowledge/atlas_knowledge/ingestion/protocols.py
git commit -m "feat(graph): GraphStore.build_temporal_near — rolling 7-day window"
```

---

## Task 9: `GraphStore.run_pagerank`

**Files:**
- Create: `packages/atlas-graph/atlas_graph/ingestion/pagerank.py`
- Modify: `packages/atlas-graph/atlas_graph/store.py`
- Modify: `packages/atlas-knowledge/atlas_knowledge/ingestion/protocols.py`
- Create: `packages/atlas-graph/atlas_graph/tests/test_pagerank.py`

- [ ] **Step 1: Extend the Protocol**

Append to `class GraphWriter(Protocol)` in `packages/atlas-knowledge/atlas_knowledge/ingestion/protocols.py`:

```python
    async def run_pagerank(self, *, project_id: UUID) -> None: ...
```

- [ ] **Step 2: Cypher constants**

Create `packages/atlas-graph/atlas_graph/ingestion/pagerank.py`:

```python
"""PageRank — gds projection + write + drop.

The graph projection is named uniquely per call so concurrent ingests in the
same project do not collide on the named projection. The drop is invoked
unconditionally in the GraphStore method's ``finally`` block.
"""

PROJECT_CYPHER = (
    "CALL gds.graph.project.cypher("
    "  $name, "
    "  'MATCH (n) WHERE n.project_id = $pid RETURN id(n) AS id', "
    "  'MATCH (a)-[r]-(b) WHERE a.project_id = $pid AND b.project_id = $pid "
    "   RETURN id(a) AS source, id(b) AS target', "
    "  {parameters: {pid: $pid}}"
    ")"
)


WRITE_CYPHER = (
    "CALL gds.pageRank.write($name, {writeProperty: 'pagerank_global'})"
)


DROP_CYPHER = "CALL gds.graph.drop($name, false)"
```

- [ ] **Step 3: Write the failing test**

Create `packages/atlas-graph/atlas_graph/tests/test_pagerank.py`:

```python
"""GraphStore.run_pagerank — projection + write + drop, drop runs even on failure."""
from __future__ import annotations

from uuid import uuid4

import pytest

from atlas_graph.errors import GraphUnavailableError
from atlas_graph.store import GraphStore


@pytest.mark.asyncio
async def test_run_pagerank_invokes_project_write_drop_in_order(fake_async_driver):
    pid = uuid4()
    store = GraphStore(fake_async_driver)
    await store.run_pagerank(project_id=pid)

    queries = [c.query for c in fake_async_driver.calls]
    project_idx = next(i for i, q in enumerate(queries) if "gds.graph.project.cypher" in q)
    write_idx = next(i for i, q in enumerate(queries) if "gds.pageRank.write" in q)
    drop_idx = next(i for i, q in enumerate(queries) if "gds.graph.drop" in q)
    assert project_idx < write_idx < drop_idx


@pytest.mark.asyncio
async def test_run_pagerank_drops_projection_even_when_write_fails(fake_async_driver):
    """Failure mode: gds.pageRank.write raises → projection still dropped."""

    class _BoomDriver(type(fake_async_driver)):  # type: ignore[misc]
        async def _maybe_fail(self, query):
            if "gds.pageRank.write" in query:
                raise RuntimeError("boom")

    # Adapt: monkeypatch fake_async_driver's run() to raise on write.
    original_run = fake_async_driver._tx_run
    async def _patched(query, **kwargs):
        if "gds.pageRank.write" in query:
            raise RuntimeError("boom")
        return await original_run(query, **kwargs)
    fake_async_driver._tx_run = _patched

    store = GraphStore(fake_async_driver)
    with pytest.raises((RuntimeError, GraphUnavailableError)):
        await store.run_pagerank(project_id=uuid4())

    queries = [c.query for c in fake_async_driver.calls]
    assert any("gds.graph.drop" in q for q in queries)
```

> **Note for the implementer:** if the existing `fake_async_driver` fixture in `tests/conftest.py` does not expose a `_tx_run` injection point, adapt the test to use whatever hook the fixture provides for raising on a specific query. The intent is: "drop must run in `finally` even if write raises."

- [ ] **Step 4: Run to verify failure**

```bash
uv run pytest packages/atlas-graph/atlas_graph/tests/test_pagerank.py -v
```

Expected: FAIL — method doesn't exist.

- [ ] **Step 5: Implement `run_pagerank`**

Add to `GraphStore` in `packages/atlas-graph/atlas_graph/store.py`:

```python
    async def run_pagerank(self, *, project_id: UUID) -> None:
        """Compute global PageRank on the project's subgraph and persist it.

        Naming the projection uniquely-per-call avoids collisions if two
        ingests in the same project race. The drop runs unconditionally;
        a failed write must not leak the projection.
        """
        from atlas_graph.ingestion.pagerank import (
            DROP_CYPHER,
            PROJECT_CYPHER,
            WRITE_CYPHER,
        )
        import time

        proj_name = f"proj_{str(project_id).replace('-', '')[:12]}_{int(time.time() * 1000)}"

        async def _project_and_write(tx: AsyncTransaction) -> None:
            await tx.run(PROJECT_CYPHER, name=proj_name, pid=str(project_id))
            await tx.run(WRITE_CYPHER, name=proj_name)

        async def _drop(tx: AsyncTransaction) -> None:
            await tx.run(DROP_CYPHER, name=proj_name)

        try:
            await self._with_retry(_project_and_write)
        finally:
            try:
                await self._with_retry(_drop)
            except Exception as e:  # noqa: BLE001
                # Drop must not mask the original failure.
                log.warning("graph.pagerank.drop_failed", name=proj_name, error=str(e))
```

- [ ] **Step 6: Run the tests**

```bash
uv run pytest packages/atlas-graph/atlas_graph/tests/test_pagerank.py -v
```

Expected: green.

- [ ] **Step 7: Commit**

```bash
git add packages/atlas-graph/atlas_graph/ingestion/pagerank.py packages/atlas-graph/atlas_graph/store.py packages/atlas-graph/atlas_graph/tests/test_pagerank.py packages/atlas-knowledge/atlas_knowledge/ingestion/protocols.py
git commit -m "feat(graph): GraphStore.run_pagerank — gds project/write/drop with finally"
```

---

## Task 10: Wire all four operations into `IngestionService.ingest()`

**Files:**
- Modify: `packages/atlas-knowledge/atlas_knowledge/ingestion/service.py`
- Modify: `packages/atlas-knowledge/atlas_knowledge/tests/test_ingestion_service.py`

This is the largest task. Read the entire `service.py` and `test_ingestion_service.py` first.

- [ ] **Step 1: Add the `_ChunkWithTextAdapter` and a per-job pagerank-status helper**

Edit `packages/atlas-knowledge/atlas_knowledge/ingestion/service.py`. Below the existing `_ChunkSpecAdapter` dataclass, add:

```python
@dataclass(frozen=True)
class _ChunkWithTextAdapter:
    """Duck-type for atlas_graph.protocols.ChunkWithText.

    Carries the full chunk text for NER (atlas_graph reads this).
    """

    id: UUID
    text: str
```

Update the `_TEXT_PREVIEW_LEN` block by adding two new module-level constants right after it:

```python
_PAGERANK_STATUS_OK = "ok"
_PAGERANK_STATUS_FAILED = "failed"
_PAGERANK_STATUS_SKIPPED = "skipped"
```

- [ ] **Step 2: Write the failing tests**

Append to `packages/atlas-knowledge/atlas_knowledge/tests/test_ingestion_service.py`:

```python
@pytest.mark.asyncio
async def test_ingest_calls_full_plan3_pipeline_when_writer_supports_it(
    vector_store, project_id, db_session
):
    """When graph_writer has all Plan 3 methods, ingest calls them in the documented order."""
    graph_writer = AsyncMock(spec=GraphWriter)
    service_with_graph = IngestionService(
        embedder=FakeEmbedder(dim=16),
        vector_store=vector_store,
        graph_writer=graph_writer,
    )
    parsed = parse_markdown("# T\n\n" + ("body word " * 600))
    await service_with_graph.ingest(
        db=db_session, user_id="matt", project_id=project_id,
        parsed=parsed, source_type="markdown", source_filename=None,
    )

    # Order: write_document_chunks → write_entities → merge_semantic_near
    #        → build_temporal_near → run_pagerank
    method_call_order = [
        c.method for c in graph_writer.method_calls
        if c.method in {
            "write_document_chunks", "write_entities", "merge_semantic_near",
            "build_temporal_near", "run_pagerank",
        }
    ]
    assert method_call_order == [
        "write_document_chunks", "write_entities", "merge_semantic_near",
        "build_temporal_near", "run_pagerank",
    ]


@pytest.mark.asyncio
async def test_ingest_marks_pagerank_status_ok_on_success(
    vector_store, project_id, db_session
):
    graph_writer = AsyncMock(spec=GraphWriter)
    service_with_graph = IngestionService(
        embedder=FakeEmbedder(dim=16),
        vector_store=vector_store,
        graph_writer=graph_writer,
    )
    parsed = parse_markdown("# T\n\n" + ("body word " * 600))
    await service_with_graph.ingest(
        db=db_session, user_id="matt", project_id=project_id,
        parsed=parsed, source_type="markdown", source_filename=None,
    )

    job = (await db_session.execute(select(IngestionJobORM))).scalar_one()
    assert job.status == "completed"
    assert job.pagerank_status == "ok"


@pytest.mark.asyncio
async def test_ingest_pagerank_failure_does_not_abort_job(
    vector_store, project_id, db_session
):
    """run_pagerank failure → job completes with pagerank_status='failed'."""
    graph_writer = AsyncMock(spec=GraphWriter)
    graph_writer.run_pagerank.side_effect = RuntimeError("gds boom")
    service_with_graph = IngestionService(
        embedder=FakeEmbedder(dim=16),
        vector_store=vector_store,
        graph_writer=graph_writer,
    )
    parsed = parse_markdown("# T\n\n" + ("body word " * 600))
    await service_with_graph.ingest(
        db=db_session, user_id="matt", project_id=project_id,
        parsed=parsed, source_type="markdown", source_filename=None,
    )

    job = (await db_session.execute(select(IngestionJobORM))).scalar_one()
    assert job.status == "completed"
    assert job.pagerank_status == "failed"
    nodes = (await db_session.execute(select(KnowledgeNodeORM))).scalars().all()
    assert len(nodes) >= 2  # doc + chunks committed despite pagerank failure


@pytest.mark.asyncio
async def test_ingest_aborts_when_write_entities_fails(
    vector_store, project_id, db_session
):
    """write_entities failure → job aborts + Postgres rollback (NER is required tier)."""
    graph_writer = AsyncMock(spec=GraphWriter)
    graph_writer.write_entities.side_effect = RuntimeError("ner down")
    service_with_graph = IngestionService(
        embedder=FakeEmbedder(dim=16),
        vector_store=vector_store,
        graph_writer=graph_writer,
    )
    parsed = parse_markdown("# T\n\n" + ("body word " * 600))
    await service_with_graph.ingest(
        db=db_session, user_id="matt", project_id=project_id,
        parsed=parsed, source_type="markdown", source_filename=None,
    )

    job = (await db_session.execute(select(IngestionJobORM))).scalar_one()
    assert job.status == "failed"
    assert "ner down" in (job.error or "")
    nodes = (await db_session.execute(select(KnowledgeNodeORM))).scalars().all()
    assert nodes == []


@pytest.mark.asyncio
async def test_merge_semantic_near_pairs_canonicalized(
    vector_store, project_id, db_session
):
    """Pairs passed to merge_semantic_near are sorted by (a < b) lexicographically."""
    graph_writer = AsyncMock(spec=GraphWriter)
    service_with_graph = IngestionService(
        embedder=FakeEmbedder(dim=16),
        vector_store=vector_store,
        graph_writer=graph_writer,
    )
    parsed = parse_markdown("# T\n\n" + ("body word " * 600))
    await service_with_graph.ingest(
        db=db_session, user_id="matt", project_id=project_id,
        parsed=parsed, source_type="markdown", source_filename=None,
    )

    near_call = graph_writer.merge_semantic_near.await_args
    if near_call is None:
        pytest.skip("FakeEmbedder produces no near pairs above threshold")
    pairs = near_call.kwargs["pairs"]
    for a, b, _ in pairs:
        assert str(a) < str(b), f"pair not canonicalized: {a}, {b}"
```

- [ ] **Step 3: Run the tests to verify failures**

```bash
uv run pytest packages/atlas-knowledge/atlas_knowledge/tests/test_ingestion_service.py -v
```

Expected: failures on the four new tests; existing tests still pass after Task 4.

- [ ] **Step 4: Update `IngestionService.ingest()` populated path**

In `packages/atlas-knowledge/atlas_knowledge/ingestion/service.py`, replace step 5.5 (the `if self._graph_writer is not None:` block in the populated-chunks path, around lines 168-188) with the full Plan 3 sequence. After step 5 (`embedding_id` stamp + flush) and inside the existing try block, add:

```python
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
                try:
                    await self._graph_writer.run_pagerank(project_id=project_id)
                    pagerank_status = _PAGERANK_STATUS_OK
                except Exception:
                    log.exception("ingest.pagerank_failed", job_id=str(job.id))
                    pagerank_status = _PAGERANK_STATUS_FAILED
```

Then, just before the `job.status = "completed"` line lower in the function, add:

```python
            job.pagerank_status = pagerank_status
```

- [ ] **Step 5: Add the helper for semantic-near pair computation**

Append a private method to `class IngestionService` (place it below `ingest`, before the empty-chunks helper if any):

```python
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
        new_ids = {r.id for r in chunk_rows}
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
```

- [ ] **Step 6: Update the constructor for the two new knobs**

Edit `IngestionService.__init__` to accept the threshold/top-k as explicit parameters (so tests can vary them and callers don't need to read AtlasConfig at this layer):

```python
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
    ) -> None:
        self._embedder = embedder
        self._vector_store = vector_store
        self._chunker = chunker or SemanticChunker(target_tokens=512, overlap_tokens=128)
        self._graph_writer = graph_writer
        self._semantic_near_threshold = semantic_near_threshold
        self._semantic_near_top_k = semantic_near_top_k
        self._temporal_near_window_days = temporal_near_window_days
```

- [ ] **Step 7: Update the empty-document path**

In `service.py`, the empty-chunks branch (around lines 106-127) currently calls only `write_document_chunks` with `chunks=[]`. Plan 3 deliberately *skips* NER, semantic, temporal, pagerank for empty docs (no chunks to extract from; no embeddings to compare). Add a comment explaining this and set `job.pagerank_status = _PAGERANK_STATUS_SKIPPED` before returning.

The empty-chunks block becomes:

```python
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
                return job.id
```

- [ ] **Step 8: Run the test suite**

```bash
uv run pytest packages/atlas-knowledge/atlas_knowledge/tests/test_ingestion_service.py -v
```

Expected: green for all old + new tests. If `test_merge_semantic_near_pairs_canonicalized` skips because `FakeEmbedder` doesn't produce above-threshold neighbors, that's fine — keep the skip.

- [ ] **Step 9: Run the broader knowledge + graph suites**

```bash
uv run pytest packages/atlas-knowledge packages/atlas-graph -q
```

Expected: green.

- [ ] **Step 10: Commit**

```bash
git add packages/atlas-knowledge/atlas_knowledge/ingestion/service.py packages/atlas-knowledge/atlas_knowledge/tests/test_ingestion_service.py
git commit -m "feat(knowledge/ingest): wire NER + edges + PageRank into IngestionService"
```

---

## Task 11: Wire `NerExtractor` into the api lifespan

**Files:**
- Modify: `apps/api/atlas_api/main.py`
- Modify: `apps/api/atlas_api/tests/test_lifespan_graph.py`

- [ ] **Step 1: Read the lifespan**

```bash
cat apps/api/atlas_api/main.py
```

Locate where `GraphStore` is currently constructed (Plan 2 added this). Identify whether `httpx.AsyncClient` is already constructed elsewhere (check imports + existing app.state population).

- [ ] **Step 2: Write the failing test**

Append to `apps/api/atlas_api/tests/test_lifespan_graph.py`:

```python
@pytest.mark.asyncio
async def test_lifespan_constructs_ner_extractor_and_attaches_to_graph_store(
    monkeypatch,
):
    """Lifespan creates a NerExtractor and passes it into GraphStore."""
    from atlas_api.main import _build_graph_store_from_config  # exposed for test
    from atlas_core.config import AtlasConfig

    monkeypatch.setenv("ATLAS_DB__DATABASE_URL", "postgresql://x")
    monkeypatch.setenv("ATLAS_GRAPH__PASSWORD", "pw")
    cfg = AtlasConfig()
    store = await _build_graph_store_from_config(cfg)
    try:
        assert store._ner_extractor is not None
    finally:
        await store.close()
```

(Adjust the import path / helper name to match what already exists in `main.py`. If `main.py` doesn't yet expose a builder helper, refactor minimally so the test can call into it without spinning up the full FastAPI app.)

- [ ] **Step 3: Run to verify failure**

```bash
uv run pytest apps/api/atlas_api/tests/test_lifespan_graph.py::test_lifespan_constructs_ner_extractor_and_attaches_to_graph_store -v
```

Expected: FAIL — NerExtractor not constructed.

- [ ] **Step 4: Update `main.py`**

In `apps/api/atlas_api/main.py`, where `GraphStore` is currently constructed during lifespan:

1. Add an `httpx.AsyncClient` if one isn't already present.
2. Construct `NerExtractor`:
   ```python
   from atlas_graph.ingestion.ner import NerExtractor
   import httpx

   http_client = httpx.AsyncClient()
   ner_extractor = NerExtractor(
       client=http_client,
       base_url=str(config.llm.lmstudio_base_url),
       max_entities=config.graph.ner_max_entities_per_chunk,
   )
   ```
3. Pass `ner_extractor=ner_extractor` to the `GraphStore(...)` constructor.
4. Close `http_client` in the lifespan teardown alongside the `GraphStore.close()`.
5. Wire `IngestionService` constructor (or wherever it's built) with the new threshold/top-k/window params from `config.graph`.

If the `IngestionService` is built per-request inside a dependency, update that dependency. If it's built once in lifespan, update there.

- [ ] **Step 5: Run the lifespan test**

```bash
uv run pytest apps/api/atlas_api/tests/test_lifespan_graph.py -v
```

Expected: green.

- [ ] **Step 6: Run the full api suite**

```bash
uv run pytest apps/api -q
```

Expected: green.

- [ ] **Step 7: Commit**

```bash
git add apps/api/atlas_api/main.py apps/api/atlas_api/tests/test_lifespan_graph.py
git commit -m "feat(api): construct NerExtractor in lifespan and pass to GraphStore"
```

---

## Task 12: Real-Neo4j integration test for the full pipeline

**Files:**
- Modify: `packages/atlas-graph/atlas_graph/tests/test_store_integration.py`

This task runs only when `ATLAS_GRAPH__INTEGRATION=1` and a real Neo4j is reachable. Skipped in CI by default.

- [ ] **Step 1: Read the existing integration test for the patterns and fixtures**

```bash
cat packages/atlas-graph/atlas_graph/tests/test_store_integration.py
```

Identify the existing fixture that produces a `GraphStore` against a real Neo4j and how migrations are applied.

- [ ] **Step 2: Write the failing integration test**

Append a new test that exercises the full Plan 3 pipeline end-to-end:

```python
@pytest.mark.integration
@pytest.mark.asyncio
async def test_full_plan3_pipeline_against_real_neo4j(real_graph_store):
    """End-to-end: write doc/chunks → entities → semantic → temporal → pagerank."""
    from datetime import UTC, datetime
    from uuid import uuid4
    from atlas_graph.ingestion.ner import Entity
    from atlas_graph.protocols import ChunkSpec, ChunkWithText

    pid = uuid4()
    did = uuid4()
    chunks = [ChunkSpec(id=uuid4(), position=i, token_count=128, text_preview=f"c{i}") for i in range(3)]

    # 1. Structural write.
    await real_graph_store.write_document_chunks(
        project_id=pid,
        project_name="Plan3 Test",
        document_id=did,
        document_title="Doc",
        document_source_type="markdown",
        document_metadata={},
        document_created_at=datetime.now(UTC),
        chunks=chunks,
    )

    # 2. Inject deterministic entities (bypass LM Studio for the integration test).
    class _StubNer:
        async def extract_batch(self, items):
            return {cid: [Entity(name="CircleK", type="CLIENT")] for cid, _ in items}

    real_graph_store._ner_extractor = _StubNer()
    await real_graph_store.write_entities(
        project_id=pid,
        chunks=[ChunkWithText(id=c.id, text="we worked with CircleK") for c in chunks],
    )

    # 3. Semantic-near (synthesize one pair).
    a, b = sorted((str(chunks[0].id), str(chunks[1].id)))
    await real_graph_store.merge_semantic_near(pairs=[(uuid_from_str(a), uuid_from_str(b), 0.92)])

    # 4. Temporal-near. (Single-doc case → 0 edges; this just confirms no error.)
    await real_graph_store.build_temporal_near(project_id=pid, document_id=did, window_days=7)

    # 5. PageRank.
    await real_graph_store.run_pagerank(project_id=pid)

    # Assertions via raw Cypher.
    async with real_graph_store._driver.session() as s:
        entities = await (await s.run(
            "MATCH (e:Entity {project_id: $pid, type: 'CLIENT'}) RETURN count(e) AS n",
            pid=str(pid),
        )).single()
        assert entities["n"] == 1

        refs = await (await s.run(
            "MATCH (:Chunk)-[:REFERENCES]->(:Entity {project_id: $pid}) RETURN count(*) AS n",
            pid=str(pid),
        )).single()
        assert refs["n"] == 3

        sn = await (await s.run(
            "MATCH (:Chunk {project_id: $pid})-[:SEMANTICALLY_NEAR]-(:Chunk {project_id: $pid}) "
            "RETURN count(*) AS n",
            pid=str(pid),
        )).single()
        # Counted twice because undirected MATCH returns each pair both ways.
        assert sn["n"] == 2

        pr_set = await (await s.run(
            "MATCH (n) WHERE n.project_id = $pid RETURN count(n.pagerank_global) AS n",
            pid=str(pid),
        )).single()
        # Project / Document / Entity / Chunk all included.
        assert pr_set["n"] >= 4


def uuid_from_str(s: str):
    from uuid import UUID
    return UUID(s)
```

- [ ] **Step 3: Run the integration test (if a real Neo4j is up)**

```bash
ATLAS_GRAPH__INTEGRATION=1 uv run pytest packages/atlas-graph/atlas_graph/tests/test_store_integration.py -v
```

Expected: green if Neo4j is up; if not, the test is skipped by the `@pytest.mark.integration` gate. Either result is acceptable; the test exists for manual / CI-with-neo4j use.

- [ ] **Step 4: Run the full suite once more for the whole branch**

```bash
uv run pytest -q
```

Expected: green.

- [ ] **Step 5: Commit**

```bash
git add packages/atlas-graph/atlas_graph/tests/test_store_integration.py
git commit -m "test(graph): real-Neo4j integration test for Plan 3 full pipeline"
```

---

## Done

Verify the Definition of Done from the design doc §7:

1. **Smoke ingest:** `docker compose up`, ingest a markdown file via the API, then run Cypher in `cypher-shell`:
   ```cypher
   MATCH (e:Entity) RETURN e.name, e.type LIMIT 20;
   MATCH (c:Chunk)-[:REFERENCES]->(e:Entity) RETURN count(*) AS refs;
   MATCH (a:Chunk)-[:SEMANTICALLY_NEAR]-(b:Chunk) RETURN count(*) AS sn;
   MATCH (a:Document)-[:TEMPORAL_NEAR]-(b:Document) RETURN count(*) AS tn;
   MATCH (n) WHERE n.pagerank_global IS NOT NULL RETURN count(n) AS scored;
   ```
   All four edge types should be > 0 after a few ingests; `pagerank_global` should be set on every node.

2. **Density check:** for a typical 10-chunk doc, `refs` is 10–30 and `sn` is 5–15 (per design §7).

3. **Kill switches:** `ATLAS_GRAPH__NER_ENABLED=false uv run pytest -q` should pass; manual smoke with the flag set should produce a job with `pagerank_status='skipped'` and no Entity nodes.

4. **LM Studio failure:** stop LM Studio, ingest → job marked `failed` with `"NER failed after retry"` in `error`; restart LM Studio, re-ingest → success.

5. **PageRank failure:** simulate by sending a malformed Cypher in `gds.pageRank.write` (or detach the gds plugin) → job completes with `pagerank_status='failed'`, every other piece written.

6. **Branch is ready for PR.** Open the PR against `main`; reviewer checklist: 12 commits, file-by-file matches the structure above, all tests green locally, design doc and plan committed in this branch.

---

## Self-review notes

This plan was checked against the design doc:

- Spec §3.1 (NER backend): Task 5 implements LM Studio NER with structured output.
- Spec §3.2 (entity types): Task 5's `ENTITY_TYPES` constant + drift-protection test cover the 11 types.
- Spec §3.3 (architecture): Tasks 5–9 implement the new ingestion submodule + writer methods; Task 10 wires them via Protocol calls.
- Spec §3.4 (failure tiers): Task 10 tests cover NER failure → abort, PageRank failure → graceful.
- Spec §3.5 (edge mechanics): Tasks 7, 8 cover SEMANTICALLY_NEAR and TEMPORAL_NEAR. Note design deviation: Chroma query lives in `IngestionService` not `GraphStore` (avoids cross-package Protocol dep). Documented in Task 7.
- Spec §3.6 (PageRank scope): Task 9.
- Spec §3.7 (entity cap): Task 5's `_validate` truncates at `max_entities`.
- Spec §4.1 (data flow): Task 10 step 4 implements the order verbatim.
- Spec §5 (config): Task 1.
- Spec §6 (testing): Tasks 1, 2, 5, 6, 7, 8, 9, 10, 12 cover every test file in the table.
- Spec §7 (definition of done): "Done" checklist references each item.
