# ATLAS Phase 2 — Plan 2: Neo4j + Graph Schema + Write Path Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stand up Neo4j 5 community in the docker stack, ship a new `atlas-graph` package, and start writing `(:Document)` + `(:Chunk)` + structural-edge graph state on every ingestion (markdown / PDF / URL). One-shot backfill of the Phase 1 corpus included. No user-visible behavior change.

**Architecture:** New workspace member `packages/atlas-graph` wraps the `neo4j` async driver behind a `GraphStore` class. Lifespan applies file-based migrations from `atlas_graph/schema/migrations/*.cypher`, then optionally runs a one-shot backfill from Postgres → Neo4j (idempotent via Cypher `MERGE`). `IngestionService` gains an optional `graph_writer` constructor kwarg (Protocol-based, so `atlas-knowledge` stays free of `atlas-graph` imports). Connection failures surface as a `GraphUnavailableError` that the existing service-level except path catches and turns into a `failed` ingestion job.

**Tech Stack:**
- Python 3.13, `neo4j>=5.20,<6` async driver, `structlog`, pydantic-settings.
- Existing: FastAPI, SQLAlchemy async, pytest + pytest-asyncio.
- Infra: `neo4j:5-community` image with the `graph-data-science` plugin, ~2 GB heap + 512 MB pagecache.

**Authoritative spec:** `docs/superpowers/specs/2026-04-27-atlas-phase-2-knowledge-graph-design.md` §5.2 (Plan 2 sketch) plus cross-cutting decisions in §3.3, §4.1, §4.6, §4.7.
**Per-plan design:** `docs/superpowers/plans/2026-04-28-atlas-phase-2-plan-2-neo4j-graph-schema-design.md`.
**Branch:** `feat/phase-2-plan-2-neo4j-graph-schema` (already checked out; design committed at `81489c6`).

**Locked decisions** (per-plan design §9):
- Q1=A — `IngestionService` gets an optional `graph_writer` constructor arg.
- Q2=B — single `GraphStore` on `app.state.graph_store`; driver lives inside.
- Q3=C — lazy connect; per-request errors map to `failed` ingestion jobs.
- Q4=A — file-based migration runner with `(:Migration)` ledger; no checksum.
- Q5=C — backfill is `MERGE`-idempotent; `(:BackfillState)` exists for progress visibility, not resume logic.
- Q6=A — Cypher inlined as string constants inside method bodies.

**Important contract details discovered during planning:**
- `IngestionService.ingest(...)` has steps 1-6 (see `service.py:62-131`) ending in `# 6. Mark job complete.` Plan 2 inserts a new step 5.5 between step 5 (stamp embedding_id) and the existing step 6, so the existing step-6 commit semantics are preserved. Updated comment numbers will be needed.
- `IngestionService.__init__` signature today: `(embedder, vector_store, *, chunker=None)`. New kwarg goes after `chunker`.
- `AtlasConfig` lives in `packages/atlas-core/atlas_core/config.py` and sub-configs use `BaseSettings` with `SettingsConfigDict(env_prefix="ATLAS_X__", extra="ignore")`. Mirror that pattern for `GraphConfig`.
- `create_session_factory(engine)` is exported from `packages/atlas-core/atlas_core/db/session.py:41`. `session_scope(factory)` is at `:52`. The CLI uses both directly.
- `KnowledgeNodeORM` lives in `packages/atlas-core/atlas_core/db/orm.py:136`. Document rows have `type="document"`, `title`, `metadata_` (JSONB). Chunk rows have `type="chunk"`, `parent_id` (FK to document), `metadata_["index"]` (the chunk position) and `metadata_["token_count"]`. Backfill reads from this table.
- `ProjectORM` is at the same file line ~19; it has `id`, `name`, `user_id`. Backfill needs `name` to populate `(:Project {name})`.
- Existing pytest conftest at `/Users/redam94/Coding/Projects/atlas-agent/conftest.py` provides `db_session` (savepoint-isolated AsyncSession) and `app_client` (httpx ASGI client). The `db_session` fixture works without modification for backfill tests.
- The `integration` pytest marker is already registered in root `pyproject.toml:36`. New integration tests can use it directly.
- Neo4j 5 properties on nodes only accept primitives + lists of primitives. Nested dicts must be JSON-encoded as strings — `_serialize_metadata` handles this.
- `neo4j` Python package raises `neo4j.exceptions.ServiceUnavailable` (connection refused, driver shutdown) and `neo4j.exceptions.TransientError` (deadlock, server unavailable for a tx). Both are retried; everything else propagates.

---

## File Map

**New workspace package — `packages/atlas-graph/`:**
- Create: `packages/atlas-graph/pyproject.toml`
- Create: `packages/atlas-graph/atlas_graph/__init__.py`
- Create: `packages/atlas-graph/atlas_graph/__main__.py`
- Create: `packages/atlas-graph/atlas_graph/store.py`
- Create: `packages/atlas-graph/atlas_graph/protocols.py`
- Create: `packages/atlas-graph/atlas_graph/schema/__init__.py`
- Create: `packages/atlas-graph/atlas_graph/schema/runner.py`
- Create: `packages/atlas-graph/atlas_graph/schema/migrations/001_initial_schema.cypher`
- Create: `packages/atlas-graph/atlas_graph/backfill.py`
- Create: `packages/atlas-graph/atlas_graph/tests/__init__.py`
- Create: `packages/atlas-graph/atlas_graph/tests/conftest.py`
- Create: `packages/atlas-graph/atlas_graph/tests/fixtures.py`
- Create: `packages/atlas-graph/atlas_graph/tests/test_store.py`
- Create: `packages/atlas-graph/atlas_graph/tests/test_runner.py`
- Create: `packages/atlas-graph/atlas_graph/tests/test_backfill.py`
- Create: `packages/atlas-graph/atlas_graph/tests/test_store_integration.py`
- Create: `packages/atlas-graph/atlas_graph/tests/test_runner_integration.py`

**Backend modifications:**
- Modify: `pyproject.toml` (root) — add `atlas-graph` to `[tool.uv.workspace].members` and `[tool.uv.sources]`; bump `[tool.pytest.ini_options].testpaths` to include atlas-graph tests.
- Modify: `packages/atlas-core/atlas_core/config.py` — add `GraphConfig`; mount on `AtlasConfig.graph`.
- Create: `packages/atlas-knowledge/atlas_knowledge/ingestion/protocols.py` — `GraphWriter` Protocol.
- Modify: `packages/atlas-knowledge/atlas_knowledge/ingestion/service.py` — add `graph_writer` kwarg + step-5.5 call.
- Modify: `packages/atlas-knowledge/atlas_knowledge/tests/test_ingestion_service.py` — assert default still no-graph + new graph-write test.
- Modify: `apps/api/atlas_api/main.py` — lifespan: connect driver, run migrations, optionally backfill, build `GraphStore`, wire into `IngestionService`, close driver on shutdown.
- Modify: `apps/api/atlas_api/deps.py` — add `get_graph_store(connection)`.

**Infra modifications:**
- Modify: `infra/docker-compose.yml` — add `neo4j` service, `neo4j_data` + `neo4j_logs` volumes, api `depends_on: neo4j`.
- Modify: `.env.example` — add `ATLAS_GRAPH__PASSWORD`, `ATLAS_GRAPH__BACKFILL_ON_START`.

**No alembic migrations.** **No Chroma changes.** **No frontend changes.**

---

## Verification baseline

Before Task 1, confirm `feat/phase-2-plan-2-neo4j-graph-schema` is clean and the suite is green:

```bash
git status   # working tree clean
uv run pytest -q
```

Expected: 213 passed + 1 skipped (Phase 1 + Plan 1 baseline). If anything is red, stop and surface it before proceeding.

---

## Task 1: Scaffold the `atlas-graph` workspace package

**Files:**
- Create: `packages/atlas-graph/pyproject.toml`
- Create: `packages/atlas-graph/atlas_graph/__init__.py`
- Create: `packages/atlas-graph/atlas_graph/tests/__init__.py`
- Modify: `pyproject.toml` (root)

The package will be empty after this task except for a single `__init__.py` and a tests package. The goal is to make `uv sync --all-packages` succeed with the new dep tree, then commit so subsequent tasks have a stable foundation.

- [ ] **Step 1: Create `packages/atlas-graph/pyproject.toml`**

```toml
[project]
name = "atlas-graph"
version = "0.1.0"
description = "ATLAS graph: Neo4j store, schema migrations, backfill"
requires-python = ">=3.13"
dependencies = [
    "atlas-core",
    "neo4j>=5.20,<6",
    "structlog>=24.4",
]

[project.scripts]
atlas-graph = "atlas_graph.__main__:main"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["atlas_graph"]
```

- [ ] **Step 2: Create the empty package files**

Create `packages/atlas-graph/atlas_graph/__init__.py`:

```python
"""ATLAS graph: Neo4j store, schema migrations, backfill."""
```

Create `packages/atlas-graph/atlas_graph/tests/__init__.py` as an empty file (`""`).

- [ ] **Step 3: Add the workspace to root `pyproject.toml`**

Edit `/Users/redam94/Coding/Projects/atlas-agent/pyproject.toml`:

Replace the `[tool.uv.workspace].members` block:

```toml
[tool.uv.workspace]
members = [
    "apps/api",
    "packages/atlas-core",
    "packages/atlas-knowledge",
    "packages/atlas-graph",
]
```

Add to `[tool.uv.sources]`:

```toml
[tool.uv.sources]
atlas-core = { workspace = true }
atlas-knowledge = { workspace = true }
atlas-graph = { workspace = true }
atlas-api = { workspace = true }
```

(Place `atlas-graph` after `atlas-knowledge` to preserve alphabetical-ish order. Don't change `atlas-api`.)

Add `packages/atlas-graph/atlas_graph/tests` to `[tool.pytest.ini_options].testpaths`:

```toml
[tool.pytest.ini_options]
testpaths = [
    "apps/api/atlas_api/tests",
    "packages/atlas-core/atlas_core/tests",
    "packages/atlas-knowledge/atlas_knowledge/tests",
    "packages/atlas-graph/atlas_graph/tests",
]
```

(Leave the rest of `[tool.pytest.ini_options]` unchanged.)

- [ ] **Step 4: Sync the workspace**

```bash
uv sync --all-packages
```

Expected: lock updates with `neo4j` (5.20+) added; no errors. The `atlas-graph` editable install registers.

- [ ] **Step 5: Confirm the package imports**

```bash
uv run python -c "import atlas_graph; print('atlas_graph OK')"
```

Expected: `atlas_graph OK`.

- [ ] **Step 6: Confirm the test suite still passes**

```bash
uv run pytest -q
```

Expected: same baseline (213 passed + 1 skipped). The new empty test directory adds 0 tests.

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml uv.lock packages/atlas-graph/
git commit -m "chore(workspace): scaffold atlas-graph package"
```

---

## Task 2: `ChunkSpec` + `GraphWriter` Protocol

**Files:**
- Create: `packages/atlas-graph/atlas_graph/protocols.py`
- Create: `packages/atlas-knowledge/atlas_knowledge/ingestion/protocols.py`
- Create: `packages/atlas-graph/atlas_graph/tests/test_protocols.py`

Pure types — no Neo4j or async involvement. Lives behind the Protocol boundary so `atlas-knowledge` stays free of `atlas-graph` imports.

- [ ] **Step 1: Write the failing test**

Create `packages/atlas-graph/atlas_graph/tests/test_protocols.py`:

```python
"""Tests for the ChunkSpec dataclass."""
from __future__ import annotations

from uuid import uuid4

from atlas_graph.protocols import ChunkSpec


def test_chunk_spec_round_trips_via_to_param():
    cid = uuid4()
    spec = ChunkSpec(id=cid, position=3, token_count=128, text_preview="Hello world.")
    param = spec.to_param()
    assert param == {
        "id": str(cid),
        "position": 3,
        "token_count": 128,
        "text_preview": "Hello world.",
    }


def test_chunk_spec_is_frozen():
    spec = ChunkSpec(id=uuid4(), position=0, token_count=10, text_preview="x")
    import dataclasses
    with pytest.raises(dataclasses.FrozenInstanceError):
        spec.position = 1  # type: ignore[misc]


def test_chunk_spec_to_param_uses_str_uuid():
    cid = uuid4()
    spec = ChunkSpec(id=cid, position=0, token_count=1, text_preview="")
    assert isinstance(spec.to_param()["id"], str)
    assert spec.to_param()["id"] == str(cid)
```

Add `import pytest` at the top.

- [ ] **Step 2: Run the test, confirm it fails**

```bash
uv run pytest packages/atlas-graph/atlas_graph/tests/test_protocols.py -v
```

Expected: ImportError on `atlas_graph.protocols`.

- [ ] **Step 3: Implement `ChunkSpec`**

Create `packages/atlas-graph/atlas_graph/protocols.py`:

```python
"""Pure data types shared between ingestion clients and the graph writer.

ChunkSpec is the structural shape that crosses the package boundary; clients
(atlas-knowledge.IngestionService) don't import it, they build duck-typed
adapters with the same interface. See the GraphWriter Protocol in
atlas_knowledge.ingestion.protocols.
"""
from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID


@dataclass(frozen=True)
class ChunkSpec:
    """Minimal chunk shape needed by GraphStore.write_document_chunks."""

    id: UUID
    position: int
    token_count: int
    text_preview: str

    def to_param(self) -> dict[str, object]:
        """Serialize for use as a Cypher parameter."""
        return {
            "id": str(self.id),
            "position": self.position,
            "token_count": self.token_count,
            "text_preview": self.text_preview,
        }
```

- [ ] **Step 4: Run the test, confirm it passes**

```bash
uv run pytest packages/atlas-graph/atlas_graph/tests/test_protocols.py -v
```

Expected: 3 passed.

- [ ] **Step 5: Add the `GraphWriter` Protocol on the knowledge side**

Create `packages/atlas-knowledge/atlas_knowledge/ingestion/protocols.py`:

```python
"""Structural types that decouple IngestionService from any specific graph backend.

GraphWriter is a Protocol satisfied by atlas_graph.store.GraphStore (Plan 2)
and any future graph backend. atlas-knowledge does NOT import atlas-graph;
the type relationship is structural, not nominal.
"""
from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol
from uuid import UUID


class ChunkSpecLike(Protocol):
    """Minimal duck-type a chunk passed to write_document_chunks must satisfy."""

    id: UUID
    position: int
    token_count: int
    text_preview: str

    def to_param(self) -> dict[str, object]: ...


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
        chunks: Sequence[ChunkSpecLike],
    ) -> None: ...
```

- [ ] **Step 6: Confirm both modules import cleanly**

```bash
uv run python -c "from atlas_graph.protocols import ChunkSpec; from atlas_knowledge.ingestion.protocols import GraphWriter; print('OK')"
```

Expected: `OK`.

- [ ] **Step 7: Run the full atlas-graph + atlas-knowledge suites**

```bash
uv run pytest packages/atlas-graph packages/atlas-knowledge -q
```

Expected: all green; +3 new tests in atlas-graph; atlas-knowledge unchanged.

Run ruff:

```bash
uv run ruff check packages/atlas-graph/atlas_graph/protocols.py \
                  packages/atlas-knowledge/atlas_knowledge/ingestion/protocols.py \
                  packages/atlas-graph/atlas_graph/tests/test_protocols.py
```

Expected: All checks passed!

- [ ] **Step 8: Commit**

```bash
git add packages/atlas-graph/atlas_graph/protocols.py \
        packages/atlas-knowledge/atlas_knowledge/ingestion/protocols.py \
        packages/atlas-graph/atlas_graph/tests/test_protocols.py
git commit -m "feat(graph): add ChunkSpec + GraphWriter Protocol"
```

---

## Task 3: `GraphConfig` + `GraphUnavailableError`

**Files:**
- Modify: `packages/atlas-core/atlas_core/config.py`
- Create: `packages/atlas-graph/atlas_graph/errors.py`
- Modify: `packages/atlas-core/atlas_core/tests/test_config.py` *(create-or-extend; check first)*
- Create: `packages/atlas-graph/atlas_graph/tests/test_errors.py`

`GraphConfig` is env-driven settings; `GraphUnavailableError` is the sentinel exception. Both are tiny.

- [ ] **Step 1: Check whether `test_config.py` exists in atlas-core**

```bash
ls packages/atlas-core/atlas_core/tests/test_config.py 2>/dev/null
```

If it exists, append. If not, you'll create it in Step 2.

- [ ] **Step 2: Add the failing config test**

Append (or create) `packages/atlas-core/atlas_core/tests/test_config.py`:

```python
"""Tests for AtlasConfig and its sub-configs."""
from __future__ import annotations

import os

import pytest
from pydantic import SecretStr, ValidationError

from atlas_core.config import AtlasConfig, GraphConfig


def test_graph_config_reads_env(monkeypatch):
    monkeypatch.setenv("ATLAS_GRAPH__URI", "bolt://example.local:7687")
    monkeypatch.setenv("ATLAS_GRAPH__USER", "neo4j")
    monkeypatch.setenv("ATLAS_GRAPH__PASSWORD", "s3cret")
    monkeypatch.setenv("ATLAS_GRAPH__BACKFILL_ON_START", "true")
    cfg = GraphConfig()
    assert str(cfg.uri) == "bolt://example.local:7687"
    assert cfg.user == "neo4j"
    assert isinstance(cfg.password, SecretStr)
    assert cfg.password.get_secret_value() == "s3cret"
    assert cfg.backfill_on_start is True


def test_graph_config_defaults(monkeypatch):
    # Only password is required.
    monkeypatch.setenv("ATLAS_GRAPH__PASSWORD", "p")
    cfg = GraphConfig()
    assert str(cfg.uri).startswith("bolt://")
    assert cfg.user == "neo4j"
    assert cfg.backfill_on_start is False


def test_graph_config_requires_password(monkeypatch):
    monkeypatch.delenv("ATLAS_GRAPH__PASSWORD", raising=False)
    with pytest.raises(ValidationError):
        GraphConfig()


def test_atlas_config_mounts_graph_subconfig(monkeypatch):
    monkeypatch.setenv("ATLAS_GRAPH__PASSWORD", "p")
    cfg = AtlasConfig()
    assert isinstance(cfg.graph, GraphConfig)
```

- [ ] **Step 3: Run, confirm fail**

```bash
uv run pytest packages/atlas-core/atlas_core/tests/test_config.py -v
```

Expected: ImportError on `GraphConfig` (or AttributeError on `cfg.graph`).

- [ ] **Step 4: Add `GraphConfig` to `config.py`**

Edit `/Users/redam94/Coding/Projects/atlas-agent/packages/atlas-core/atlas_core/config.py`. Add after `DatabaseConfig`:

```python
class GraphConfig(BaseSettings):
    """Neo4j configuration."""

    model_config = SettingsConfigDict(env_prefix="ATLAS_GRAPH__", extra="ignore")

    uri: AnyUrl = Field(default="bolt://neo4j:7687")
    user: str = "neo4j"
    password: SecretStr  # required
    backfill_on_start: bool = False
```

(`AnyUrl`, `SecretStr`, and `Field` are already imported in this module.)

Add `graph: GraphConfig = Field(default_factory=GraphConfig)` to `AtlasConfig`:

```python
class AtlasConfig(BaseSettings):
    ...

    llm: LLMConfig = Field(default_factory=LLMConfig)
    db: DatabaseConfig = Field(default_factory=DatabaseConfig)
    graph: GraphConfig = Field(default_factory=GraphConfig)
    environment: Literal["development", "production"] = "development"
    log_level: str = "INFO"
    user_id: str = "matt"
```

- [ ] **Step 5: Run, confirm pass**

```bash
uv run pytest packages/atlas-core/atlas_core/tests/test_config.py -v
```

Expected: 4 passed (the new ones; existing config tests, if any, untouched).

- [ ] **Step 6: Add the `GraphUnavailableError` test**

Create `packages/atlas-graph/atlas_graph/tests/test_errors.py`:

```python
"""Tests for GraphUnavailableError."""
from __future__ import annotations

import pytest

from atlas_graph.errors import GraphUnavailableError


def test_graph_unavailable_is_runtime_error():
    err = GraphUnavailableError("neo4j unavailable: connection refused")
    assert isinstance(err, RuntimeError)
    assert str(err) == "neo4j unavailable: connection refused"


def test_graph_unavailable_chains_cause():
    cause = ConnectionRefusedError("nope")
    try:
        raise GraphUnavailableError("wrapped") from cause
    except GraphUnavailableError as e:
        assert e.__cause__ is cause
```

- [ ] **Step 7: Run, confirm fail**

```bash
uv run pytest packages/atlas-graph/atlas_graph/tests/test_errors.py -v
```

Expected: ImportError on `atlas_graph.errors`.

- [ ] **Step 8: Implement `GraphUnavailableError`**

Create `packages/atlas-graph/atlas_graph/errors.py`:

```python
"""Errors raised by the graph layer."""
from __future__ import annotations


class GraphUnavailableError(RuntimeError):
    """Raised when Neo4j is unreachable after exhausting retries.

    Surfaces at the router as a 502 (lifespan-time) or as a `failed` ingestion
    job (request-time, via IngestionService's existing exception handler).
    """
```

- [ ] **Step 9: Run, confirm pass**

```bash
uv run pytest packages/atlas-graph packages/atlas-core -q
```

Expected: all green.

Run ruff:

```bash
uv run ruff check packages/atlas-core/atlas_core/config.py \
                  packages/atlas-graph/atlas_graph/errors.py \
                  packages/atlas-graph/atlas_graph/tests/test_errors.py \
                  packages/atlas-core/atlas_core/tests/test_config.py
```

Expected: All checks passed!

- [ ] **Step 10: Commit**

```bash
git add packages/atlas-core/atlas_core/config.py \
        packages/atlas-core/atlas_core/tests/test_config.py \
        packages/atlas-graph/atlas_graph/errors.py \
        packages/atlas-graph/atlas_graph/tests/test_errors.py
git commit -m "feat(core/graph): add GraphConfig and GraphUnavailableError"
```

---

## Task 4: `GraphStore.healthcheck` + retry helper (mocked driver)

**Files:**
- Create: `packages/atlas-graph/atlas_graph/store.py`
- Create: `packages/atlas-graph/atlas_graph/tests/test_store.py`

The store wraps an `AsyncDriver`. Plan 2 ships `healthcheck()` + the internal `_with_retry` helper now; `write_document_chunks` lands in the next task.

- [ ] **Step 1: Write the failing tests**

Create `packages/atlas-graph/atlas_graph/tests/test_store.py`:

```python
"""Unit tests for GraphStore — mocked driver, no Neo4j."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from neo4j.exceptions import ServiceUnavailable, TransientError

from atlas_graph.errors import GraphUnavailableError
from atlas_graph.store import GraphStore


def _mock_driver_session_succeeds():
    """Driver mock whose session.run returns OK on first call."""
    session = AsyncMock()
    session.__aenter__.return_value = session
    session.__aexit__.return_value = None
    session.run = AsyncMock()
    session.execute_write = AsyncMock()
    driver = MagicMock()
    driver.session = MagicMock(return_value=session)
    driver.close = AsyncMock()
    return driver, session


@pytest.mark.asyncio
async def test_healthcheck_runs_return_one():
    driver, session = _mock_driver_session_succeeds()
    store = GraphStore(driver)
    await store.healthcheck()
    session.run.assert_awaited_once()
    args, _ = session.run.call_args
    assert args[0].strip() == "RETURN 1"


@pytest.mark.asyncio
async def test_close_closes_driver():
    driver, _ = _mock_driver_session_succeeds()
    store = GraphStore(driver)
    await store.close()
    driver.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_with_retry_succeeds_first_try():
    driver, session = _mock_driver_session_succeeds()
    store = GraphStore(driver, max_retries=3)
    fn = AsyncMock()
    await store._with_retry(fn)  # type: ignore[attr-defined]
    session.execute_write.assert_awaited_once_with(fn)


@pytest.mark.asyncio
async def test_with_retry_retries_then_succeeds(monkeypatch):
    driver, session = _mock_driver_session_succeeds()
    store = GraphStore(driver, max_retries=3)
    # First two attempts raise ServiceUnavailable, third succeeds.
    session.execute_write.side_effect = [
        ServiceUnavailable("attempt 1"),
        TransientError("attempt 2"),
        None,
    ]
    # Make sleep a no-op so the test is fast.
    monkeypatch.setattr("atlas_graph.store.asyncio.sleep", AsyncMock())

    fn = AsyncMock()
    await store._with_retry(fn)  # type: ignore[attr-defined]
    assert session.execute_write.await_count == 3


@pytest.mark.asyncio
async def test_with_retry_raises_graph_unavailable_after_exhausting(monkeypatch):
    driver, session = _mock_driver_session_succeeds()
    store = GraphStore(driver, max_retries=3)
    session.execute_write.side_effect = ServiceUnavailable("nope")
    monkeypatch.setattr("atlas_graph.store.asyncio.sleep", AsyncMock())

    fn = AsyncMock()
    with pytest.raises(GraphUnavailableError) as excinfo:
        await store._with_retry(fn)  # type: ignore[attr-defined]
    assert "neo4j unavailable" in str(excinfo.value).lower()
    assert isinstance(excinfo.value.__cause__, ServiceUnavailable)
    assert session.execute_write.await_count == 3
```

- [ ] **Step 2: Run, confirm fail**

```bash
uv run pytest packages/atlas-graph/atlas_graph/tests/test_store.py -v
```

Expected: ImportError on `atlas_graph.store.GraphStore`.

- [ ] **Step 3: Implement `GraphStore`**

Create `packages/atlas-graph/atlas_graph/store.py`:

```python
"""GraphStore — async wrapper around the neo4j driver."""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

import structlog
from neo4j.exceptions import ServiceUnavailable, TransientError

from atlas_graph.errors import GraphUnavailableError

if TYPE_CHECKING:
    from neo4j import AsyncDriver, AsyncTransaction

log = structlog.get_logger("atlas.graph.store")


class GraphStore:
    """Async wrapper around the neo4j AsyncDriver.

    Constructor does NOT open a connection — the first method call (or
    healthcheck()) is when the driver actually probes the server. On transient
    failures we retry with exponential backoff; persistent failures raise
    GraphUnavailableError.
    """

    def __init__(self, driver: "AsyncDriver", *, max_retries: int = 3) -> None:
        self._driver = driver
        self._max_retries = max_retries

    async def close(self) -> None:
        await self._driver.close()

    async def healthcheck(self) -> None:
        """Run `RETURN 1` against the driver. Raises GraphUnavailableError on persistent failure."""
        async with self._session() as s:
            await s.run("RETURN 1")

    async def _with_retry(
        self,
        fn: Callable[["AsyncTransaction"], Awaitable[None]],
    ) -> None:
        """Execute fn inside a write transaction, retrying transient failures.

        Retries up to ``max_retries`` times with exponential backoff
        (0.5 s → 1 s → 2 s). Wraps the final failure in GraphUnavailableError.
        """
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

- [ ] **Step 4: Run, confirm pass**

```bash
uv run pytest packages/atlas-graph/atlas_graph/tests/test_store.py -v
```

Expected: 5 passed.

Run ruff:

```bash
uv run ruff check packages/atlas-graph/atlas_graph/store.py \
                  packages/atlas-graph/atlas_graph/tests/test_store.py
```

Expected: All checks passed!

- [ ] **Step 5: Commit**

```bash
git add packages/atlas-graph/atlas_graph/store.py \
        packages/atlas-graph/atlas_graph/tests/test_store.py
git commit -m "feat(graph): add GraphStore with healthcheck and retry helper"
```

---

## Task 5: `GraphStore.write_document_chunks` (mocked driver)

**Files:**
- Modify: `packages/atlas-graph/atlas_graph/store.py`
- Modify: `packages/atlas-graph/atlas_graph/tests/test_store.py`

Adds the actual write path: 5 Cypher MERGE statements inside one write transaction (Q6=A — Cypher inlined).

- [ ] **Step 1: Write the failing tests**

Append to `packages/atlas-graph/atlas_graph/tests/test_store.py`:

```python
from uuid import uuid4

from atlas_graph.protocols import ChunkSpec


@pytest.mark.asyncio
async def test_write_document_chunks_runs_5_cypher_statements_in_one_tx(monkeypatch):
    """Captures the (cypher, params) sequence executed inside the write tx."""
    driver = MagicMock()
    session = AsyncMock()
    session.__aenter__.return_value = session
    session.__aexit__.return_value = None
    driver.session = MagicMock(return_value=session)

    captured: list[tuple[str, dict]] = []

    async def fake_execute_write(fn):
        # The fn the store passes to execute_write expects an AsyncTransaction.
        tx = AsyncMock()
        async def fake_run(cypher, **params):
            captured.append((cypher, params))
        tx.run = fake_run
        await fn(tx)

    session.execute_write = fake_execute_write

    store = GraphStore(driver)
    pid = uuid4()
    did = uuid4()
    chunks = [
        ChunkSpec(id=uuid4(), position=0, token_count=128, text_preview="alpha"),
        ChunkSpec(id=uuid4(), position=1, token_count=64, text_preview="beta"),
    ]
    await store.write_document_chunks(
        project_id=pid,
        project_name="P",
        document_id=did,
        document_title="Doc One",
        document_source_type="markdown",
        document_metadata={"author": "matt"},
        chunks=chunks,
    )

    assert len(captured) == 5
    cyphers = [c for c, _ in captured]
    assert "MERGE (p:Project" in cyphers[0]
    assert "MERGE (d:Document" in cyphers[1]
    assert "(d)-[:PART_OF]->(p)" in cyphers[2]
    assert "MERGE (ch:Chunk" in cyphers[3]
    assert "(c)-[:BELONGS_TO]->(d)" in cyphers[4]


@pytest.mark.asyncio
async def test_write_document_chunks_passes_str_uuids_for_ids(monkeypatch):
    driver = MagicMock()
    session = AsyncMock()
    session.__aenter__.return_value = session
    session.__aexit__.return_value = None
    driver.session = MagicMock(return_value=session)

    captured: list[tuple[str, dict]] = []

    async def fake_execute_write(fn):
        tx = AsyncMock()
        async def fake_run(cypher, **params):
            captured.append((cypher, params))
        tx.run = fake_run
        await fn(tx)

    session.execute_write = fake_execute_write

    store = GraphStore(driver)
    pid = uuid4()
    did = uuid4()
    cid = uuid4()
    await store.write_document_chunks(
        project_id=pid,
        project_name="P",
        document_id=did,
        document_title="t",
        document_source_type="markdown",
        document_metadata={},
        chunks=[ChunkSpec(id=cid, position=0, token_count=1, text_preview="x")],
    )
    # All id parameters are stringified UUIDs (Neo4j stores them as strings).
    project_call = captured[0][1]
    document_call = captured[1][1]
    chunk_unwind_call = captured[3][1]
    assert project_call["project_id"] == str(pid)
    assert document_call["id"] == str(did)
    assert chunk_unwind_call["chunks"][0]["id"] == str(cid)
    assert chunk_unwind_call["project_id"] == str(pid)
    assert chunk_unwind_call["document_id"] == str(did)


@pytest.mark.asyncio
async def test_write_document_chunks_serializes_nested_metadata(monkeypatch):
    """Neo4j 5 properties don't accept nested dicts. Nested values get JSON-encoded."""
    driver = MagicMock()
    session = AsyncMock()
    session.__aenter__.return_value = session
    session.__aexit__.return_value = None
    driver.session = MagicMock(return_value=session)

    captured: list[tuple[str, dict]] = []

    async def fake_execute_write(fn):
        tx = AsyncMock()
        async def fake_run(cypher, **params):
            captured.append((cypher, params))
        tx.run = fake_run
        await fn(tx)

    session.execute_write = fake_execute_write

    store = GraphStore(driver)
    await store.write_document_chunks(
        project_id=uuid4(),
        project_name="P",
        document_id=uuid4(),
        document_title="t",
        document_source_type="markdown",
        document_metadata={
            "scalar": "ok",
            "nested": {"a": 1, "b": [1, 2]},
            "list_of_dicts": [{"k": "v"}, {"k": "v2"}],
        },
        chunks=[],
    )
    document_call_meta = captured[1][1]["metadata"]
    assert document_call_meta["scalar"] == "ok"
    # Nested dict and list-of-dict become JSON strings.
    assert isinstance(document_call_meta["nested"], str)
    assert isinstance(document_call_meta["list_of_dicts"], str)
    import json
    assert json.loads(document_call_meta["nested"]) == {"a": 1, "b": [1, 2]}
```

- [ ] **Step 2: Run, confirm fail**

```bash
uv run pytest packages/atlas-graph/atlas_graph/tests/test_store.py -v -k "write_document_chunks or serialize"
```

Expected: AttributeError — `GraphStore.write_document_chunks` does not exist.

- [ ] **Step 3: Implement `write_document_chunks` + `_serialize_metadata`**

Edit `/Users/redam94/Coding/Projects/atlas-agent/packages/atlas-graph/atlas_graph/store.py`. At module top, add:

```python
import json
from collections.abc import Sequence
from typing import Any
from uuid import UUID

from atlas_graph.protocols import ChunkSpec
```

Add a helper function above the `GraphStore` class:

```python
def _serialize_metadata(metadata: dict) -> dict[str, Any]:
    """Flatten metadata to Neo4j-compatible primitive types.

    Neo4j 5 node/relationship properties accept str, int, float, bool, and
    homogeneous lists of those. Nested dicts and lists-of-dicts are JSON-encoded
    as strings. Plain lists of primitives pass through unchanged.
    """
    out: dict[str, Any] = {}
    for k, v in metadata.items():
        if isinstance(v, dict) or (
            isinstance(v, list) and any(isinstance(x, dict) for x in v)
        ):
            out[k] = json.dumps(v, default=str)
        else:
            out[k] = v
    return out
```

Add the `write_document_chunks` method to `GraphStore` (after `_with_retry`):

```python
    async def write_document_chunks(
        self,
        *,
        project_id: UUID,
        project_name: str,
        document_id: UUID,
        document_title: str,
        document_source_type: str,
        document_metadata: dict,
        chunks: Sequence[ChunkSpec],
    ) -> None:
        """Write Document + Chunk nodes + structural edges in one tx.

        All MERGE — idempotent. Property values for nested dicts in
        ``document_metadata`` are JSON-encoded as strings (Neo4j 5
        property-type constraint).
        """
        meta = _serialize_metadata(document_metadata)
        chunk_params = [c.to_param() for c in chunks]
        chunk_ids = [str(c.id) for c in chunks]

        async def _do(tx: "AsyncTransaction") -> None:
            await tx.run(
                "MERGE (p:Project {id: $project_id}) "
                "ON CREATE SET p.name = $name "
                "ON MATCH SET p.name = coalesce(p.name, $name)",
                project_id=str(project_id),
                name=project_name,
            )
            await tx.run(
                "MERGE (d:Document {id: $id}) "
                "SET d.project_id = $project_id, d.title = $title, "
                "    d.source_type = $source_type, d.metadata = $metadata",
                id=str(document_id),
                project_id=str(project_id),
                title=document_title,
                source_type=document_source_type,
                metadata=meta,
            )
            await tx.run(
                "MATCH (d:Document {id: $document_id}), (p:Project {id: $project_id}) "
                "MERGE (d)-[:PART_OF]->(p)",
                document_id=str(document_id),
                project_id=str(project_id),
            )
            await tx.run(
                "UNWIND $chunks AS c "
                "MERGE (ch:Chunk {id: c.id}) "
                "SET ch.project_id = $project_id, ch.parent_id = $document_id, "
                "    ch.position = c.position, ch.token_count = c.token_count, "
                "    ch.text_preview = c.text_preview",
                chunks=chunk_params,
                project_id=str(project_id),
                document_id=str(document_id),
            )
            await tx.run(
                "MATCH (d:Document {id: $document_id}) "
                "UNWIND $chunk_ids AS cid "
                "MATCH (c:Chunk {id: cid}) "
                "MERGE (c)-[:BELONGS_TO]->(d)",
                document_id=str(document_id),
                chunk_ids=chunk_ids,
            )

        await self._with_retry(_do)
```

- [ ] **Step 4: Run, confirm pass**

```bash
uv run pytest packages/atlas-graph/atlas_graph/tests/test_store.py -v
```

Expected: 8 passed (5 from Task 4 + 3 new).

Run ruff:

```bash
uv run ruff check packages/atlas-graph/atlas_graph/store.py \
                  packages/atlas-graph/atlas_graph/tests/test_store.py
```

Expected: All checks passed!

- [ ] **Step 5: Re-export public API from `atlas_graph/__init__.py`**

Replace `packages/atlas-graph/atlas_graph/__init__.py`:

```python
"""ATLAS graph: Neo4j store, schema migrations, backfill."""
from atlas_graph.errors import GraphUnavailableError
from atlas_graph.protocols import ChunkSpec
from atlas_graph.store import GraphStore

__all__ = [
    "ChunkSpec",
    "GraphStore",
    "GraphUnavailableError",
]
```

- [ ] **Step 6: Confirm import + tests**

```bash
uv run python -c "from atlas_graph import ChunkSpec, GraphStore, GraphUnavailableError; print('OK')"
uv run pytest packages/atlas-graph -q
```

Expected: `OK`; tests still green.

- [ ] **Step 7: Commit**

```bash
git add packages/atlas-graph/atlas_graph/store.py \
        packages/atlas-graph/atlas_graph/tests/test_store.py \
        packages/atlas-graph/atlas_graph/__init__.py
git commit -m "feat(graph): add GraphStore.write_document_chunks"
```

---

## Task 6: `MigrationRunner` + initial schema

**Files:**
- Create: `packages/atlas-graph/atlas_graph/schema/__init__.py`
- Create: `packages/atlas-graph/atlas_graph/schema/runner.py`
- Create: `packages/atlas-graph/atlas_graph/schema/migrations/001_initial_schema.cypher`
- Create: `packages/atlas-graph/atlas_graph/tests/test_runner.py`

File-based, ledger-tracked. Tests use a temp directory for migration files plus a mocked driver.

- [ ] **Step 1: Create the schema package + first migration file**

Create `packages/atlas-graph/atlas_graph/schema/__init__.py`:

```python
"""Schema migrations for the Neo4j graph."""
```

Create `packages/atlas-graph/atlas_graph/schema/migrations/001_initial_schema.cypher`:

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

- [ ] **Step 2: Write the failing tests**

Create `packages/atlas-graph/atlas_graph/tests/test_runner.py`:

```python
"""Unit tests for MigrationRunner — mocked driver, temp migrations dir."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from atlas_graph.schema.runner import MigrationRunner


def _mock_driver_with_session(applied_ids: list[str]):
    """Return a driver mock whose RUN of MATCH (m:Migration) yields applied_ids."""
    session = AsyncMock()
    session.__aenter__.return_value = session
    session.__aexit__.return_value = None

    captured_apply_calls: list[tuple[str, str]] = []

    async def fake_execute_read(fn):
        # The runner asks: MATCH (m:Migration) RETURN m.id
        # Return a list of records each having a single string element.
        records = [{"id": mid} for mid in applied_ids]
        result = MagicMock()
        result.data = AsyncMock(return_value=records)
        # The runner uses `async for` on the result. Provide an async iterator.
        async def _aiter():
            for r in records:
                yield {"id": r["id"]}
        result.__aiter__ = lambda self=result: _aiter()
        return await fn(result) if False else records  # not called this way; see below

    async def fake_run_read_query(cypher: str):
        return [{"id": mid} for mid in applied_ids]

    async def fake_apply(mid: str, cypher: str):
        captured_apply_calls.append((mid, cypher))

    session.run = AsyncMock()
    session.execute_read = AsyncMock(return_value=[{"id": mid} for mid in applied_ids])
    session.execute_write = AsyncMock()

    driver = MagicMock()
    driver.session = MagicMock(return_value=session)
    driver._captured_apply_calls = captured_apply_calls  # for tests
    driver.close = AsyncMock()
    return driver, session


@pytest.mark.asyncio
async def test_run_pending_applies_unapplied_files_in_order(tmp_path: Path):
    migrations_dir = tmp_path / "migrations"
    migrations_dir.mkdir()
    (migrations_dir / "001_first.cypher").write_text("CREATE CONSTRAINT a IF NOT EXISTS FOR (n:A) REQUIRE n.id IS UNIQUE;")
    (migrations_dir / "002_second.cypher").write_text("CREATE INDEX b IF NOT EXISTS FOR (n:A) ON (n.x);")

    driver, session = _mock_driver_with_session(applied_ids=[])
    runner = MigrationRunner(driver, migrations_dir)

    applied = await runner.run_pending()
    assert applied == ["001", "002"]
    assert session.execute_write.await_count == 2  # one execute_write per migration


@pytest.mark.asyncio
async def test_run_pending_skips_already_applied(tmp_path: Path):
    migrations_dir = tmp_path / "migrations"
    migrations_dir.mkdir()
    (migrations_dir / "001_first.cypher").write_text("CREATE CONSTRAINT a IF NOT EXISTS FOR (n:A) REQUIRE n.id IS UNIQUE;")
    (migrations_dir / "002_second.cypher").write_text("CREATE INDEX b IF NOT EXISTS FOR (n:A) ON (n.x);")

    driver, session = _mock_driver_with_session(applied_ids=["001"])
    runner = MigrationRunner(driver, migrations_dir)

    applied = await runner.run_pending()
    assert applied == ["002"]
    assert session.execute_write.await_count == 1


@pytest.mark.asyncio
async def test_run_pending_idempotent_when_all_applied(tmp_path: Path):
    migrations_dir = tmp_path / "migrations"
    migrations_dir.mkdir()
    (migrations_dir / "001_first.cypher").write_text("CREATE CONSTRAINT a IF NOT EXISTS FOR (n:A) REQUIRE n.id IS UNIQUE;")

    driver, session = _mock_driver_with_session(applied_ids=["001"])
    runner = MigrationRunner(driver, migrations_dir)

    applied = await runner.run_pending()
    assert applied == []
    session.execute_write.assert_not_awaited()


@pytest.mark.asyncio
async def test_run_pending_ignores_non_matching_filenames(tmp_path: Path):
    migrations_dir = tmp_path / "migrations"
    migrations_dir.mkdir()
    (migrations_dir / "001_real.cypher").write_text("RETURN 1;")
    (migrations_dir / "README.md").write_text("docs")
    (migrations_dir / "abc_invalid.cypher").write_text("RETURN 2;")
    (migrations_dir / "0001_too_many_digits.cypher").write_text("RETURN 3;")

    driver, _ = _mock_driver_with_session(applied_ids=[])
    runner = MigrationRunner(driver, migrations_dir)

    applied = await runner.run_pending()
    assert applied == ["001"]


@pytest.mark.asyncio
async def test_run_pending_handles_gap_in_id_sequence(tmp_path: Path):
    migrations_dir = tmp_path / "migrations"
    migrations_dir.mkdir()
    (migrations_dir / "001_a.cypher").write_text("RETURN 1;")
    (migrations_dir / "003_c.cypher").write_text("RETURN 3;")
    # No 002 — runner should still apply 001 then 003.

    driver, session = _mock_driver_with_session(applied_ids=[])
    runner = MigrationRunner(driver, migrations_dir)

    applied = await runner.run_pending()
    assert applied == ["001", "003"]
    assert session.execute_write.await_count == 2


@pytest.mark.asyncio
async def test_run_pending_splits_multi_statement_files(tmp_path: Path):
    """A .cypher file with multiple ;-separated statements should run each."""
    migrations_dir = tmp_path / "migrations"
    migrations_dir.mkdir()
    (migrations_dir / "001_multi.cypher").write_text(
        "CREATE CONSTRAINT a IF NOT EXISTS FOR (n:A) REQUIRE n.id IS UNIQUE;\n"
        "CREATE INDEX a_x IF NOT EXISTS FOR (n:A) ON (n.x);"
    )

    # Capture every tx.run inside the runner's apply transaction.
    captured: list[str] = []

    async def fake_execute_write(fn):
        tx = AsyncMock()
        async def fake_run(cypher, **params):
            captured.append(cypher.strip())
        tx.run = fake_run
        await fn(tx)

    session = AsyncMock()
    session.__aenter__.return_value = session
    session.__aexit__.return_value = None
    session.execute_read = AsyncMock(return_value=[])
    session.execute_write = fake_execute_write
    driver = MagicMock()
    driver.session = MagicMock(return_value=session)

    runner = MigrationRunner(driver, migrations_dir)
    applied = await runner.run_pending()
    assert applied == ["001"]
    # 2 schema statements + 1 ledger MERGE
    assert len(captured) == 3
    assert any("CREATE CONSTRAINT" in c for c in captured)
    assert any("CREATE INDEX" in c for c in captured)
    assert any("MERGE (m:Migration" in c for c in captured)
```

- [ ] **Step 3: Run, confirm fail**

```bash
uv run pytest packages/atlas-graph/atlas_graph/tests/test_runner.py -v
```

Expected: ImportError on `atlas_graph.schema.runner`.

- [ ] **Step 4: Implement `MigrationRunner`**

Create `packages/atlas-graph/atlas_graph/schema/runner.py`:

```python
"""Migration runner — applies *.cypher files in id order, records ledger."""
from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from neo4j import AsyncDriver, AsyncTransaction

log = structlog.get_logger("atlas.graph.migrations")

_MIGRATION_FILE_RE = re.compile(r"^(\d{3})_[a-z0-9_]+\.cypher$")


class MigrationRunner:
    """Discover and apply *.cypher files; record applied ids in (:Migration)."""

    def __init__(self, driver: "AsyncDriver", migrations_dir: Path) -> None:
        self._driver = driver
        self._migrations_dir = migrations_dir

    async def run_pending(self) -> list[str]:
        """Apply every migration not already in the (:Migration) ledger.

        Returns the ordered list of newly-applied migration ids.
        """
        applied = await self._load_applied()
        files: list[tuple[str, Path]] = []
        for f in sorted(self._migrations_dir.glob("*.cypher")):
            m = _MIGRATION_FILE_RE.match(f.name)
            if not m:
                continue
            mid = m.group(1)
            files.append((mid, f))

        newly_applied: list[str] = []
        for mid, path in files:
            if mid in applied:
                continue
            cypher = path.read_text()
            async with self._driver.session() as s:
                await s.execute_write(self._make_apply(mid, cypher))
            log.info("graph.migration.applied", id=mid, file=path.name)
            newly_applied.append(mid)
        return newly_applied

    async def _load_applied(self) -> set[str]:
        async with self._driver.session() as s:
            records = await s.execute_read(self._read_applied)
        return {r["id"] for r in records}

    @staticmethod
    async def _read_applied(tx: "AsyncTransaction"):
        result = await tx.run("MATCH (m:Migration) RETURN m.id AS id")
        return [r async for r in result]

    @staticmethod
    def _make_apply(mid: str, cypher: str):
        async def _apply(tx: "AsyncTransaction") -> None:
            for stmt in [s.strip() for s in cypher.split(";") if s.strip()]:
                await tx.run(stmt)
            await tx.run(
                "MERGE (m:Migration {id: $id}) "
                "ON CREATE SET m.applied_at = datetime()",
                id=mid,
            )
        return _apply
```

- [ ] **Step 5: Run, confirm pass**

```bash
uv run pytest packages/atlas-graph/atlas_graph/tests/test_runner.py -v
```

Expected: 6 passed.

Run ruff:

```bash
uv run ruff check packages/atlas-graph/atlas_graph/schema/ \
                  packages/atlas-graph/atlas_graph/tests/test_runner.py
```

Expected: All checks passed!

- [ ] **Step 6: Re-export `MigrationRunner`**

Edit `packages/atlas-graph/atlas_graph/__init__.py`:

```python
"""ATLAS graph: Neo4j store, schema migrations, backfill."""
from atlas_graph.errors import GraphUnavailableError
from atlas_graph.protocols import ChunkSpec
from atlas_graph.schema.runner import MigrationRunner
from atlas_graph.store import GraphStore

__all__ = [
    "ChunkSpec",
    "GraphStore",
    "GraphUnavailableError",
    "MigrationRunner",
]
```

- [ ] **Step 7: Commit**

```bash
git add packages/atlas-graph/atlas_graph/schema/ \
        packages/atlas-graph/atlas_graph/tests/test_runner.py \
        packages/atlas-graph/atlas_graph/__init__.py
git commit -m "feat(graph): add MigrationRunner with file-based ledger and 001 schema"
```

---

## Task 7: `backfill_phase1` (real Postgres, mocked GraphStore)

**Files:**
- Create: `packages/atlas-graph/atlas_graph/backfill.py`
- Create: `packages/atlas-graph/atlas_graph/tests/test_backfill.py`
- Modify: `packages/atlas-graph/atlas_graph/__init__.py`

Reads from real Postgres (atlas_test DB) via the existing `db_session` fixture; the GraphStore is a `MagicMock(spec=GraphStore)` whose `write_document_chunks` is recorded for assertion.

- [ ] **Step 1: Write the failing tests**

Create `packages/atlas-graph/atlas_graph/tests/test_backfill.py`:

```python
"""Backfill tests — real Postgres + mocked GraphStore."""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from atlas_core.db.orm import KnowledgeNodeORM, ProjectORM
from sqlalchemy.ext.asyncio import AsyncSession

from atlas_graph.backfill import BackfillResult, backfill_phase1
from atlas_graph.store import GraphStore


async def _seed_project_with_chunks(db: AsyncSession, *, n_chunks: int = 3):
    proj = ProjectORM(user_id="matt", name="P", default_model="claude-sonnet-4-6")
    db.add(proj)
    await db.flush()
    doc = KnowledgeNodeORM(
        user_id="matt",
        project_id=proj.id,
        type="document",
        title="Doc",
        text="full document text",
        metadata_={"source_type": "markdown"},
    )
    db.add(doc)
    await db.flush()
    chunks = []
    for i in range(n_chunks):
        ch = KnowledgeNodeORM(
            user_id="matt",
            project_id=proj.id,
            type="chunk",
            parent_id=doc.id,
            title="Doc",
            text=f"chunk text {i} " * 30,
            metadata_={"index": i, "token_count": 64},
        )
        db.add(ch)
        chunks.append(ch)
    await db.flush()
    return proj, doc, chunks


@pytest.mark.asyncio
async def test_backfill_writes_one_call_per_document(db_session: AsyncSession):
    graph = AsyncMock(spec=GraphStore)
    proj, doc, chunks = await _seed_project_with_chunks(db_session, n_chunks=3)

    result = await backfill_phase1(db=db_session, graph=graph)

    assert isinstance(result, BackfillResult)
    assert result.documents == 1
    assert result.chunks == 3
    assert result.batches >= 1
    graph.write_document_chunks.assert_awaited_once()
    kwargs = graph.write_document_chunks.await_args.kwargs
    assert kwargs["project_id"] == proj.id
    assert kwargs["project_name"] == "P"
    assert kwargs["document_id"] == doc.id
    assert kwargs["document_title"] == "Doc"
    assert kwargs["document_source_type"] == "markdown"
    assert len(kwargs["chunks"]) == 3
    # Chunks have id, position, token_count, text_preview.
    assert {c.position for c in kwargs["chunks"]} == {0, 1, 2}


@pytest.mark.asyncio
async def test_backfill_text_preview_is_first_200_chars(db_session: AsyncSession):
    graph = AsyncMock(spec=GraphStore)
    proj, doc, chunks = await _seed_project_with_chunks(db_session, n_chunks=1)
    # The seeded chunk text is "chunk text 0 " * 30 = 390 chars.
    await backfill_phase1(db=db_session, graph=graph)
    kwargs = graph.write_document_chunks.await_args.kwargs
    preview = kwargs["chunks"][0].text_preview
    assert len(preview) == 200
    assert preview.startswith("chunk text 0")


@pytest.mark.asyncio
async def test_backfill_empty_db_returns_zero_result(db_session: AsyncSession):
    graph = AsyncMock(spec=GraphStore)
    result = await backfill_phase1(db=db_session, graph=graph)
    assert result.documents == 0
    assert result.chunks == 0
    assert result.batches == 0
    graph.write_document_chunks.assert_not_awaited()


@pytest.mark.asyncio
async def test_backfill_progress_callback_invoked_per_batch(db_session: AsyncSession):
    """With docs_per_batch=1, three docs should fire three progress calls."""
    graph = AsyncMock(spec=GraphStore)
    proj = ProjectORM(user_id="matt", name="P", default_model="claude-sonnet-4-6")
    db_session.add(proj)
    await db_session.flush()
    for _ in range(3):
        doc = KnowledgeNodeORM(
            user_id="matt", project_id=proj.id, type="document",
            title="d", text="t", metadata_={"source_type": "markdown"},
        )
        db_session.add(doc)
    await db_session.flush()

    progress: list[tuple[int, int]] = []
    await backfill_phase1(
        db=db_session, graph=graph, docs_per_batch=1,
        progress_cb=lambda b, t: progress.append((b, t)),
    )
    assert len(progress) == 3
    assert [b for b, _ in progress] == [1, 2, 3]
    assert all(t == 3 for _, t in progress)
```

- [ ] **Step 2: Run, confirm fail**

```bash
uv run pytest packages/atlas-graph/atlas_graph/tests/test_backfill.py -v
```

Expected: ImportError on `atlas_graph.backfill`.

- [ ] **Step 3: Implement `backfill_phase1`**

Create `packages/atlas-graph/atlas_graph/backfill.py`:

```python
"""One-shot backfill: walks Phase 1 Postgres rows into Neo4j.

Idempotent via Cypher MERGE inside GraphStore.write_document_chunks.
Writes a (:BackfillState {key:'phase1'}) node for progress visibility — not
used for resume logic (re-running from scratch is safe and cheap).
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from math import ceil
from uuid import UUID

import structlog
from atlas_core.db.orm import KnowledgeNodeORM, ProjectORM
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from atlas_graph.protocols import ChunkSpec
from atlas_graph.store import GraphStore

log = structlog.get_logger("atlas.graph.backfill")

_DEFAULT_DOCS_PER_BATCH = 50
_TEXT_PREVIEW_LEN = 200


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
    docs_per_batch: int = _DEFAULT_DOCS_PER_BATCH,
    progress_cb: Callable[[int, int], None] | None = None,
) -> BackfillResult:
    """Walk all Postgres documents/chunks and write them to Neo4j.

    Re-running this is safe (MERGE is idempotent). Updates a
    (:BackfillState {key:'phase1'}) node after each batch.
    """
    started = datetime.now(UTC)

    project_rows = (await db.execute(select(ProjectORM))).scalars().all()
    project_names: dict[UUID, str] = {p.id: p.name for p in project_rows}

    docs_q = (
        select(KnowledgeNodeORM)
        .where(KnowledgeNodeORM.type == "document")
        .order_by(KnowledgeNodeORM.created_at)
    )
    doc_rows = (await db.execute(docs_q)).scalars().all()
    total_docs = len(doc_rows)
    total_batches = ceil(total_docs / docs_per_batch) if total_docs else 0

    batches_done = 0
    chunks_total = 0

    for i, doc in enumerate(doc_rows, start=1):
        chunks_q = (
            select(KnowledgeNodeORM)
            .where(KnowledgeNodeORM.parent_id == doc.id)
            .order_by(KnowledgeNodeORM.created_at)
        )
        chunk_rows = (await db.execute(chunks_q)).scalars().all()
        specs = [
            ChunkSpec(
                id=c.id,
                position=int((c.metadata_ or {}).get("index", 0)),
                token_count=int((c.metadata_ or {}).get("token_count", 0)),
                text_preview=c.text[:_TEXT_PREVIEW_LEN],
            )
            for c in chunk_rows
        ]
        chunks_total += len(specs)

        await graph.write_document_chunks(
            project_id=doc.project_id,
            project_name=project_names.get(doc.project_id, "Unknown"),
            document_id=doc.id,
            document_title=doc.title or "Untitled",
            document_source_type=str((doc.metadata_ or {}).get("source_type", "unknown")),
            document_metadata=dict(doc.metadata_ or {}),
            chunks=specs,
        )

        if i % docs_per_batch == 0 or i == total_docs:
            batches_done += 1
            if progress_cb:
                progress_cb(batches_done, total_batches)
            log.info(
                "graph.backfill.progress",
                batch=batches_done, total=total_batches,
                docs_done=i, chunks_done=chunks_total,
            )

    finished = datetime.now(UTC)
    log.info(
        "graph.backfill.done",
        documents=total_docs, chunks=chunks_total, batches=batches_done,
    )
    return BackfillResult(
        documents=total_docs,
        chunks=chunks_total,
        batches=batches_done,
        started_at=started,
        finished_at=finished,
    )
```

(BackfillState writes are deferred to the integration-test phase since they require a real Neo4j; the unit tests only verify the per-document write calls, which is the core invariant. The progress callback gives operators the same visibility without needing Neo4j round-trips during unit tests. The integration test added in Task 12 will exercise the full path including the BackfillState writes — see Task 12 step 4.)

**Note for Task 12:** the integration phase also adds a private `_update_backfill_state` helper that writes to `(:BackfillState {key:'phase1', batches_done, last_doc_id, started_at, finished_at?})`. Adding this here would make Task 7 require a real Neo4j; deferring keeps the TDD loop tight.

- [ ] **Step 4: Run, confirm pass**

```bash
uv run pytest packages/atlas-graph/atlas_graph/tests/test_backfill.py -v
```

Expected: 4 passed (uses the existing `db_session` fixture from `/Users/redam94/Coding/Projects/atlas-agent/conftest.py`).

Run ruff:

```bash
uv run ruff check packages/atlas-graph/atlas_graph/backfill.py \
                  packages/atlas-graph/atlas_graph/tests/test_backfill.py
```

Expected: All checks passed!

- [ ] **Step 5: Re-export `backfill_phase1`**

Edit `packages/atlas-graph/atlas_graph/__init__.py`:

```python
"""ATLAS graph: Neo4j store, schema migrations, backfill."""
from atlas_graph.backfill import BackfillResult, backfill_phase1
from atlas_graph.errors import GraphUnavailableError
from atlas_graph.protocols import ChunkSpec
from atlas_graph.schema.runner import MigrationRunner
from atlas_graph.store import GraphStore

__all__ = [
    "BackfillResult",
    "ChunkSpec",
    "GraphStore",
    "GraphUnavailableError",
    "MigrationRunner",
    "backfill_phase1",
]
```

- [ ] **Step 6: Commit**

```bash
git add packages/atlas-graph/atlas_graph/backfill.py \
        packages/atlas-graph/atlas_graph/tests/test_backfill.py \
        packages/atlas-graph/atlas_graph/__init__.py
git commit -m "feat(graph): add backfill_phase1 with progress callback"
```

---

## Task 8: `IngestionService.graph_writer` extension

**Files:**
- Modify: `packages/atlas-knowledge/atlas_knowledge/ingestion/service.py`
- Modify: `packages/atlas-knowledge/atlas_knowledge/tests/test_ingestion_service.py`

`IngestionService` gains an optional `graph_writer` constructor kwarg. After step 5 (stamp embedding_id), if `graph_writer is not None`, call its `write_document_chunks`. Failure flows through the existing exception handler — job marked `failed`.

- [ ] **Step 1: Write the failing tests**

Append to `packages/atlas-knowledge/atlas_knowledge/tests/test_ingestion_service.py`:

```python
from unittest.mock import AsyncMock

from atlas_knowledge.ingestion.protocols import GraphWriter


@pytest.mark.asyncio
async def test_ingest_does_not_call_graph_writer_when_none(
    db_session, fake_embedder, fake_vector_store
):
    """Default constructor leaves graph_writer=None — existing behavior."""
    project = ProjectORM(user_id="matt", name="P", default_model="claude-sonnet-4-6")
    db_session.add(project)
    await db_session.flush()

    service = IngestionService(embedder=fake_embedder, vector_store=fake_vector_store)
    parsed = parse_markdown("# Title\n\nbody " * 100)
    job_id = await service.ingest(
        db=db_session, user_id="matt", project_id=project.id,
        parsed=parsed, source_type="markdown", source_filename=None,
    )
    job = await db_session.get(IngestionJobORM, job_id)
    assert job.status == "completed"


@pytest.mark.asyncio
async def test_ingest_calls_graph_writer_when_supplied(
    db_session, fake_embedder, fake_vector_store
):
    project = ProjectORM(user_id="matt", name="P", default_model="claude-sonnet-4-6")
    db_session.add(project)
    await db_session.flush()

    graph_writer = AsyncMock(spec=GraphWriter)
    service = IngestionService(
        embedder=fake_embedder, vector_store=fake_vector_store,
        graph_writer=graph_writer,
    )
    parsed = parse_markdown("# Title\n\nbody " * 100)
    job_id = await service.ingest(
        db=db_session, user_id="matt", project_id=project.id,
        parsed=parsed, source_type="markdown", source_filename=None,
    )
    job = await db_session.get(IngestionJobORM, job_id)
    assert job.status == "completed"
    graph_writer.write_document_chunks.assert_awaited_once()
    kwargs = graph_writer.write_document_chunks.await_args.kwargs
    assert kwargs["project_id"] == project.id
    assert kwargs["project_name"] == "P"
    assert kwargs["document_source_type"] == "markdown"
    assert len(kwargs["chunks"]) >= 1
    # ChunkSpecLike duck-type: each item has the required attributes.
    assert all(hasattr(c, "id") and hasattr(c, "position") and
               hasattr(c, "token_count") and hasattr(c, "text_preview")
               for c in kwargs["chunks"])


@pytest.mark.asyncio
async def test_ingest_marks_job_failed_when_graph_writer_raises(
    db_session, fake_embedder, fake_vector_store
):
    project = ProjectORM(user_id="matt", name="P", default_model="claude-sonnet-4-6")
    db_session.add(project)
    await db_session.flush()

    graph_writer = AsyncMock(spec=GraphWriter)
    graph_writer.write_document_chunks.side_effect = RuntimeError("graph down")
    service = IngestionService(
        embedder=fake_embedder, vector_store=fake_vector_store,
        graph_writer=graph_writer,
    )
    parsed = parse_markdown("# Title\n\nbody " * 100)
    job_id = await service.ingest(
        db=db_session, user_id="matt", project_id=project.id,
        parsed=parsed, source_type="markdown", source_filename=None,
    )
    job = await db_session.get(IngestionJobORM, job_id)
    assert job.status == "failed"
    assert "graph down" in job.error
```

(Reuse whatever fixtures the existing `test_ingestion_service.py` file uses for `fake_embedder` and `fake_vector_store` — read the file first to confirm the exact names. If they differ, adapt.)

- [ ] **Step 2: Run, confirm fail**

```bash
uv run pytest packages/atlas-knowledge/atlas_knowledge/tests/test_ingestion_service.py -v -k "graph"
```

Expected: TypeError on `graph_writer` kwarg, or AssertionError because the writer was never called.

- [ ] **Step 3: Update `IngestionService`**

Edit `/Users/redam94/Coding/Projects/atlas-agent/packages/atlas-knowledge/atlas_knowledge/ingestion/service.py`. Add to imports at top:

```python
from dataclasses import dataclass
from sqlalchemy import select

from atlas_core.db.orm import IngestionJobORM, KnowledgeNodeORM, ProjectORM
from atlas_knowledge.ingestion.protocols import GraphWriter
```

(Adjust `from atlas_core.db.orm import ...` if `ProjectORM` isn't already imported.)

Add a tiny adapter class above `IngestionService`:

```python
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
```

Update the `__init__` signature:

```python
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
```

Insert the new step BETWEEN existing step 5 and step 6. Find the block (around `service.py:120-130`) that ends with `await db.flush()` after the embedding_id stamp. Add:

```python
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
                            text_preview=r.text[:200],
                        )
                        for r in chunk_rows
                    ],
                )

            # 6. Mark job complete.
            ...
```

Do NOT change the existing exception handler — graph failures will propagate up to it, mark the job `failed`, and roll back doc/chunk rows. That's the desired behavior (Q3=C).

- [ ] **Step 4: Run, confirm pass**

```bash
uv run pytest packages/atlas-knowledge/atlas_knowledge/tests/test_ingestion_service.py -v
```

Expected: all green — existing tests + 3 new graph-related tests.

Run ruff:

```bash
uv run ruff check packages/atlas-knowledge/atlas_knowledge/ingestion/service.py \
                  packages/atlas-knowledge/atlas_knowledge/tests/test_ingestion_service.py
```

Expected: All checks passed!

- [ ] **Step 5: Run the full backend suite to confirm no regressions**

```bash
uv run pytest -q
```

Expected: previous count + 3 new tests, all green.

- [ ] **Step 6: Commit**

```bash
git add packages/atlas-knowledge/atlas_knowledge/ingestion/service.py \
        packages/atlas-knowledge/atlas_knowledge/tests/test_ingestion_service.py
git commit -m "feat(knowledge): wire IngestionService.graph_writer extension point"
```

---

## Task 9: docker-compose service + .env.example

**Files:**
- Modify: `infra/docker-compose.yml`
- Modify: `.env.example`

No tests for compose changes; the validation is `docker compose config` parses cleanly + `docker compose up neo4j` brings the container to healthy.

- [ ] **Step 1: Add the `neo4j` service to compose**

Edit `/Users/redam94/Coding/Projects/atlas-agent/infra/docker-compose.yml`. Insert the new `neo4j` service after `redis` and before `api`:

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
```

Add `neo4j: { condition: service_healthy }` to the `api` service's `depends_on`:

```yaml
    depends_on:
      postgres:
        condition: service_healthy
      redis:
        condition: service_healthy
      neo4j:
        condition: service_healthy
```

Add to the `environment:` block of the `api` service (so the api connects to the compose-network neo4j by service name):

```yaml
      ATLAS_GRAPH__URI: bolt://neo4j:7687
```

Add the volumes at the bottom under `volumes:`:

```yaml
volumes:
  postgres_data:
  redis_data:
  neo4j_data:
  neo4j_logs:
```

- [ ] **Step 2: Update `.env.example`**

Edit `/Users/redam94/Coding/Projects/atlas-agent/.env.example`. Append a new section at the bottom:

```
# ── Graph (Neo4j) ────────────────────────────────────────────
ATLAS_GRAPH__PASSWORD=changeme
ATLAS_GRAPH__BACKFILL_ON_START=false
```

(`ATLAS_GRAPH__URI` is hard-coded in compose, so users don't need to set it. If you run the api outside compose against a localhost neo4j, you'd export `ATLAS_GRAPH__URI=bolt://localhost:7687` — document this in the design doc, not `.env.example`.)

- [ ] **Step 3: Validate compose config parses**

```bash
cd /Users/redam94/Coding/Projects/atlas-agent && \
ATLAS_GRAPH__PASSWORD=test docker compose -f infra/docker-compose.yml config > /dev/null
```

Expected: no output, exit 0. (If `docker compose` is not installed or working, skip this — the smoke task will catch issues.)

- [ ] **Step 4: Bring just the neo4j service up to verify the image + healthcheck**

```bash
cd /Users/redam94/Coding/Projects/atlas-agent/infra && \
ATLAS_GRAPH__PASSWORD=changeme docker compose up -d neo4j
```

Wait ~60-90s for cold start, then:

```bash
docker compose ps neo4j
```

Expected: `neo4j  ...  Up X minutes (healthy)`. Then tear down: `docker compose down`.

If healthcheck is failing, investigate (`docker compose logs neo4j`) before committing. Common causes: wrong image tag, plugin download failed, port conflict on 7687.

- [ ] **Step 5: Commit**

```bash
git add infra/docker-compose.yml .env.example
git commit -m "feat(infra): add neo4j 5-community service with gds plugin"
```

---

## Task 10: Lifespan plumbing + `get_graph_store` dep

**Files:**
- Modify: `apps/api/atlas_api/main.py`
- Modify: `apps/api/atlas_api/deps.py`
- Create: `apps/api/atlas_api/tests/test_lifespan_graph.py`

Connects the driver, runs migrations, optionally backfills, builds `GraphStore`, wires `IngestionService`. Lifespan-test is integration-flavored (needs real Neo4j to apply migrations); marked with `pytest.mark.integration`.

- [ ] **Step 1: Add `get_graph_store` to `deps.py`**

Edit `/Users/redam94/Coding/Projects/atlas-agent/apps/api/atlas_api/deps.py`. Add the import:

```python
from atlas_graph import GraphStore
```

Add the dep at the bottom:

```python
def get_graph_store(connection: HTTPConnection) -> GraphStore:
    return connection.app.state.graph_store
```

- [ ] **Step 2: Update `main.py` lifespan**

Edit `/Users/redam94/Coding/Projects/atlas-agent/apps/api/atlas_api/main.py`. Add imports:

```python
from pathlib import Path

from atlas_core.db.session import session_scope
from atlas_graph import GraphStore, MigrationRunner, backfill_phase1
from neo4j import AsyncGraphDatabase
import atlas_graph
```

Inside the `lifespan` function, after `app.state.session_factory = ...`, add:

```python
    # Graph layer setup.
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
        async with session_scope(app.state.session_factory) as backfill_db:
            result = await backfill_phase1(
                db=backfill_db, graph=graph_store,
                progress_cb=lambda b, t: log.info(
                    "graph.backfill.progress", batch=b, total=t,
                ),
            )
            await backfill_db.commit()
        log.info(
            "graph.backfill.done",
            documents=result.documents, chunks=result.chunks, batches=result.batches,
        )
```

Update the `IngestionService` line to wire in the graph writer:

```python
    app.state.ingestion_service = IngestionService(
        embedder=embedder, vector_store=vector_store, graph_writer=graph_store,
    )
```

Update the shutdown branch:

```python
    try:
        yield
    finally:
        log.info("api.shutdown")
        await graph_store.close()
        await engine.dispose()
```

- [ ] **Step 3: Add the lifespan test**

Create `apps/api/atlas_api/tests/test_lifespan_graph.py`:

```python
"""Lifespan-time integration test: real Neo4j brings migrations + GraphStore up.

Skipped unless ATLAS_TEST_NEO4J_URL is set (e.g.
ATLAS_TEST_NEO4J_URL=bolt://localhost:7687) AND ATLAS_GRAPH__PASSWORD is set.
"""
from __future__ import annotations

import os

import pytest

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not os.getenv("ATLAS_TEST_NEO4J_URL") or not os.getenv("ATLAS_GRAPH__PASSWORD"),
        reason="set ATLAS_TEST_NEO4J_URL and ATLAS_GRAPH__PASSWORD to enable",
    ),
]


@pytest.mark.asyncio
async def test_lifespan_initializes_graph_store(monkeypatch):
    monkeypatch.setenv("ATLAS_GRAPH__URI", os.environ["ATLAS_TEST_NEO4J_URL"])
    monkeypatch.setenv("ATLAS_GRAPH__BACKFILL_ON_START", "false")
    # Re-import main to pick up env-driven config in a fresh process state.
    from atlas_api.main import app, lifespan

    async with lifespan(app):
        assert hasattr(app.state, "graph_store")
        # Healthcheck round-trips.
        await app.state.graph_store.healthcheck()
```

- [ ] **Step 4: Run the suite**

```bash
uv run pytest -q
```

Expected: 213+ passed + 1 skipped + 1 new lifespan-test skipped (because ATLAS_TEST_NEO4J_URL is unset by default).

Run ruff:

```bash
uv run ruff check apps/api/atlas_api/main.py apps/api/atlas_api/deps.py \
                  apps/api/atlas_api/tests/test_lifespan_graph.py
```

Expected: All checks passed!

- [ ] **Step 5: Commit**

```bash
git add apps/api/atlas_api/main.py apps/api/atlas_api/deps.py \
        apps/api/atlas_api/tests/test_lifespan_graph.py
git commit -m "feat(api): wire GraphStore + migrations + optional backfill into lifespan"
```

---

## Task 11: `atlas-graph backfill` CLI

**Files:**
- Create: `packages/atlas-graph/atlas_graph/__main__.py`

Wires the existing `backfill_phase1` into a console entrypoint. The `[project.scripts]` registration in Task 1 already pointed at `atlas_graph.__main__:main`.

- [ ] **Step 1: Implement the CLI**

Create `packages/atlas-graph/atlas_graph/__main__.py`:

```python
"""CLI entrypoint: `atlas-graph backfill`."""
from __future__ import annotations

import argparse
import asyncio
import sys

from atlas_core.config import AtlasConfig
from atlas_core.db.session import (
    create_engine_from_config,
    create_session_factory,
    session_scope,
)
from neo4j import AsyncGraphDatabase

from atlas_graph.backfill import backfill_phase1
from atlas_graph.store import GraphStore


def main() -> None:
    parser = argparse.ArgumentParser(prog="atlas-graph")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("backfill", help="Backfill Phase 1 chunks into Neo4j")
    args = parser.parse_args()
    if args.cmd == "backfill":
        sys.exit(asyncio.run(_run_backfill()))


async def _run_backfill() -> int:
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
            result = await backfill_phase1(
                db=db,
                graph=graph,
                progress_cb=_print_progress,
            )
            await db.commit()
        print(
            f"\nBackfill complete: {result.documents} docs, {result.chunks} chunks, "
            f"{result.batches} batches in "
            f"{(result.finished_at - result.started_at).total_seconds():.1f}s"
        )
    finally:
        await graph.close()
        await engine.dispose()
    return 0


def _print_progress(batch: int, total: int) -> None:
    pct = (batch * 100 // total) if total else 100
    print(f"batch {batch}/{total} ({pct}%)", flush=True)


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Confirm the script entrypoint resolves**

```bash
uv run atlas-graph --help
```

Expected: argparse usage output listing the `backfill` subcommand.

- [ ] **Step 3: Run the suite**

```bash
uv run pytest -q
```

Expected: same pass count — no new tests for the CLI (tested at the integration level in Task 12 manual smoke).

Run ruff:

```bash
uv run ruff check packages/atlas-graph/atlas_graph/__main__.py
```

Expected: All checks passed!

- [ ] **Step 4: Commit**

```bash
git add packages/atlas-graph/atlas_graph/__main__.py
git commit -m "feat(graph): add atlas-graph backfill CLI"
```

---

## Task 12: Real-Neo4j integration tests + manual smoke

**Files:**
- Create: `packages/atlas-graph/atlas_graph/tests/conftest.py`
- Create: `packages/atlas-graph/atlas_graph/tests/test_store_integration.py`
- Create: `packages/atlas-graph/atlas_graph/tests/test_runner_integration.py`

Opt-in tests against a real Neo4j (the compose service or any reachable instance). Skipped unless `ATLAS_TEST_NEO4J_URL` and `ATLAS_GRAPH__PASSWORD` are both set.

- [ ] **Step 1: Add the integration conftest**

Create `packages/atlas-graph/atlas_graph/tests/conftest.py`:

```python
"""Real-Neo4j fixtures for integration tests.

Skipped unless the environment supplies both ATLAS_TEST_NEO4J_URL and
ATLAS_GRAPH__PASSWORD. The fixture cleans up after itself by deleting any
nodes created with the per-test project_id.
"""
from __future__ import annotations

import os
from uuid import uuid4

import pytest_asyncio
from neo4j import AsyncGraphDatabase

from atlas_graph.store import GraphStore


def _enabled() -> bool:
    return bool(os.getenv("ATLAS_TEST_NEO4J_URL")) and bool(os.getenv("ATLAS_GRAPH__PASSWORD"))


@pytest_asyncio.fixture
async def real_neo4j_driver():
    if not _enabled():
        import pytest
        pytest.skip("set ATLAS_TEST_NEO4J_URL + ATLAS_GRAPH__PASSWORD to enable")
    driver = AsyncGraphDatabase.driver(
        os.environ["ATLAS_TEST_NEO4J_URL"],
        auth=("neo4j", os.environ["ATLAS_GRAPH__PASSWORD"]),
    )
    try:
        yield driver
    finally:
        await driver.close()


@pytest_asyncio.fixture
async def real_graph_store(real_neo4j_driver):
    yield GraphStore(real_neo4j_driver)


@pytest_asyncio.fixture
async def isolated_project_id(real_neo4j_driver):
    """Yield a fresh UUID; teardown deletes every node tagged with it."""
    pid = uuid4()
    yield pid
    async with real_neo4j_driver.session() as s:
        await s.run(
            "MATCH (n) WHERE n.project_id = $pid DETACH DELETE n",
            pid=str(pid),
        )
        await s.run(
            "MATCH (p:Project {id: $pid}) DETACH DELETE p",
            pid=str(pid),
        )
```

- [ ] **Step 2: Add the runner integration test**

Create `packages/atlas-graph/atlas_graph/tests/test_runner_integration.py`:

```python
"""MigrationRunner against a real Neo4j."""
from __future__ import annotations

from pathlib import Path

import pytest

from atlas_graph.schema.runner import MigrationRunner

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_initial_schema_applies_then_is_idempotent(real_neo4j_driver):
    """Apply 001_initial_schema, second run is a no-op, ledger node exists."""
    migrations_dir = Path(__file__).parent.parent / "schema" / "migrations"
    runner = MigrationRunner(real_neo4j_driver, migrations_dir)

    # First run applies (or no-op if already applied).
    first = await runner.run_pending()
    # Second run is always a no-op.
    second = await runner.run_pending()
    assert second == []

    # Ledger node exists for 001.
    async with real_neo4j_driver.session() as s:
        result = await s.run("MATCH (m:Migration {id: '001'}) RETURN m.id AS id")
        records = [r async for r in result]
    assert len(records) == 1
    assert records[0]["id"] == "001"
    # If first call applied for the first time, applied list must contain '001'.
    if first:
        assert "001" in first
```

- [ ] **Step 3: Add the store integration test**

Create `packages/atlas-graph/atlas_graph/tests/test_store_integration.py`:

```python
"""GraphStore.write_document_chunks against a real Neo4j."""
from __future__ import annotations

from uuid import uuid4

import pytest

from atlas_graph.protocols import ChunkSpec

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_write_document_chunks_creates_nodes_and_edges(
    real_graph_store, real_neo4j_driver, isolated_project_id,
):
    pid = isolated_project_id
    did = uuid4()
    chunks = [
        ChunkSpec(id=uuid4(), position=0, token_count=100, text_preview="alpha"),
        ChunkSpec(id=uuid4(), position=1, token_count=120, text_preview="beta"),
        ChunkSpec(id=uuid4(), position=2, token_count=80, text_preview="gamma"),
    ]
    await real_graph_store.write_document_chunks(
        project_id=pid,
        project_name="IntegrationTest",
        document_id=did,
        document_title="Doc",
        document_source_type="markdown",
        document_metadata={"author": "matt"},
        chunks=chunks,
    )

    async with real_neo4j_driver.session() as s:
        result = await s.run(
            "MATCH (p:Project {id: $pid}) "
            "OPTIONAL MATCH (d:Document {id: $did})-[:PART_OF]->(p) "
            "OPTIONAL MATCH (c:Chunk)-[:BELONGS_TO]->(d) "
            "RETURN count(DISTINCT p) AS projects, count(DISTINCT d) AS docs, "
            "       count(DISTINCT c) AS chunks",
            pid=str(pid), did=str(did),
        )
        rec = (await result.single())
    assert rec["projects"] == 1
    assert rec["docs"] == 1
    assert rec["chunks"] == 3


@pytest.mark.asyncio
async def test_write_document_chunks_idempotent(
    real_graph_store, real_neo4j_driver, isolated_project_id,
):
    pid = isolated_project_id
    did = uuid4()
    cid = uuid4()
    spec = ChunkSpec(id=cid, position=0, token_count=10, text_preview="x")

    for _ in range(2):
        await real_graph_store.write_document_chunks(
            project_id=pid, project_name="P", document_id=did,
            document_title="t", document_source_type="markdown",
            document_metadata={}, chunks=[spec],
        )

    async with real_neo4j_driver.session() as s:
        result = await s.run(
            "MATCH (c:Chunk {id: $cid})-[r:BELONGS_TO]->(d:Document {id: $did}) "
            "RETURN count(c) AS chunks, count(r) AS edges",
            cid=str(cid), did=str(did),
        )
        rec = await result.single()
    # Two calls but MERGE means one node and one edge.
    assert rec["chunks"] == 1
    assert rec["edges"] == 1


@pytest.mark.asyncio
async def test_healthcheck_against_real_neo4j(real_graph_store):
    await real_graph_store.healthcheck()  # no exception = pass
```

- [ ] **Step 4: Confirm the integration tests skip by default**

```bash
uv run pytest packages/atlas-graph/atlas_graph/tests/test_store_integration.py \
              packages/atlas-graph/atlas_graph/tests/test_runner_integration.py \
              packages/atlas-graph/atlas_graph/tests/test_lifespan_graph.py 2>/dev/null \
              apps/api/atlas_api/tests/test_lifespan_graph.py -v
```

Expected: 4-5 tests skipped (the lifespan one + the integration ones).

- [ ] **Step 5: (Optional, local-only) Run integration tests against the compose neo4j**

```bash
cd /Users/redam94/Coding/Projects/atlas-agent/infra && \
ATLAS_GRAPH__PASSWORD=changeme docker compose up -d neo4j
# Wait ~60s for healthy.

cd /Users/redam94/Coding/Projects/atlas-agent && \
ATLAS_TEST_NEO4J_URL=bolt://localhost:7687 \
ATLAS_GRAPH__PASSWORD=changeme \
ATLAS_DB__DATABASE_URL='postgresql://atlas:atlas@localhost:5432/atlas_test' \
uv run pytest -m integration -v
```

Expected: integration tests pass. (The runner test applies 001_initial_schema; the store tests write + verify nodes/edges. Postgres test DB needs to be reachable for the lifespan test.)

If they fail, investigate before continuing — common causes: stale schema in neo4j_data volume from a prior run (run `docker compose down -v` to wipe), wrong password.

Tear down: `docker compose down`.

- [ ] **Step 6: Manual end-to-end smoke**

```bash
# 1. Bring the full stack up.
cd /Users/redam94/Coding/Projects/atlas-agent/infra && \
ATLAS_GRAPH__PASSWORD=changeme docker compose up -d --build
docker compose ps  # all healthy?

# 2. Ingest a markdown doc via the existing modal at http://localhost:3000.
#    Or via curl:
PID=$(curl -s -X POST http://localhost:8000/api/v1/projects \
    -H "content-type: application/json" \
    -d '{"name":"GraphSmoke","default_model":"claude-sonnet-4-6"}' | jq -r .id)
curl -s -X POST http://localhost:8000/api/v1/knowledge/ingest \
    -H "content-type: application/json" \
    -d "{\"project_id\":\"$PID\",\"source_type\":\"markdown\",\"text\":\"# Hello\\n\\n$(printf 'body %.0s' {1..200})\"}" \
    | jq -r .status

# 3. Confirm the graph has the document.
docker compose exec neo4j cypher-shell -u neo4j -p changeme \
    "MATCH (d:Document)-[:PART_OF]->(p:Project) RETURN d.title, p.name LIMIT 5"

# Expected output includes one row with the project + document.

# 4. Run the backfill CLI from the host.
ATLAS_DB__DATABASE_URL='postgresql://atlas:atlas@localhost:5432/atlas' \
ATLAS_GRAPH__URI='bolt://localhost:7687' \
ATLAS_GRAPH__PASSWORD=changeme \
uv run atlas-graph backfill

# Expected: prints "batch N/M (XX%)" lines and a summary.
```

- [ ] **Step 7: Tear down**

```bash
cd /Users/redam94/Coding/Projects/atlas-agent/infra && docker compose down
```

- [ ] **Step 8: Commit (integration tests only — manual smoke is verification, no commit)**

```bash
git add packages/atlas-graph/atlas_graph/tests/conftest.py \
        packages/atlas-graph/atlas_graph/tests/test_store_integration.py \
        packages/atlas-graph/atlas_graph/tests/test_runner_integration.py
git commit -m "test(graph): add opt-in real-Neo4j integration tests"
```

---

## Self-review notes

- **Spec coverage check:** every Plan 2 requirement from spec §5.2 + §4.6 + §4.7 has a task: docker neo4j service (Task 9), atlas-graph package layout (Task 1), GraphStore wrapper (Tasks 4-5), MigrationRunner + 001_initial_schema (Task 6), IngestionService extension (Task 8), one-shot backfill + CLI (Tasks 7, 11), backfill env-var gating + lifespan integration (Task 10), idempotent MERGE (verified in Task 12), `(:Migration)` ledger (verified in Task 6 + 12), Neo4j memory env vars (Task 9). Plan 5 read paths are explicitly out of scope.
- **Type consistency:** `ChunkSpec`/`ChunkSpecLike`/`_ChunkSpecAdapter` all have the same four fields and `to_param()` shape across Tasks 2, 5, 7, 8. `GraphWriter.write_document_chunks` signature is consistent across Tasks 2, 5, 7, 8. Constants — `_TEXT_PREVIEW_LEN=200`, `_DEFAULT_DOCS_PER_BATCH=50`, `max_retries=3` — are referenced where used.
- **Placeholder check:** every code step shows the actual code; commit messages are exact; expected pytest counts are concrete; no "similar to before" references.
- **Order matters:** Task 1 scaffolds the package so Task 2's tests can import it. Tasks 4-5 build the store before Task 6's migration runner uses the same driver pattern. Task 7's backfill depends on Tasks 5 (store) + the existing Postgres ORM. Task 8's IngestionService extension depends on Task 2's Protocol. Task 9 (compose) is independent of code; could land at any point but lands here so Task 10 (lifespan) has a real neo4j to connect to during manual verification. Task 10 wires it all into the api. Task 11 (CLI) depends on Task 7. Task 12 (integration tests + smoke) verifies everything end-to-end. Each task ends green-tests + commit so review checkpoints have a clean baseline.

---

*ATLAS Phase 2 — Plan 2 — Neo4j + Graph Schema + Write Path Implementation Plan · 2026-04-28*
