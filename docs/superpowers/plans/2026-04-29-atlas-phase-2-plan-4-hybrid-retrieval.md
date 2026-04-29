# Phase 2 Plan 4 — Hybrid Retrieval Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace Phase 1's pure-vector RAG with a graph-aware hybrid pipeline (BM25 + dense vector + 1-hop graph expansion + cross-encoder rerank + personalized PageRank), gated by `ATLAS_RETRIEVAL__MODE` so the Phase 1 path remains a one-env-var rollback.

**Architecture:** New `atlas_knowledge/retrieval/hybrid/` submodule with one component per stage and a thin orchestrator (`HybridRetriever`) that wires them together with per-stage graceful degradation. New `GraphStore.expand_chunks` walks REFERENCES + SEMANTICALLY_NEAR with a separate-cap-per-edge-type budget. Postgres FTS via a generated `tsvector` column + partial GIN index. Personalized PageRank runs in-process via `igraph` on the small expanded subgraph.

**Tech Stack:** Python 3.13, asyncpg/SQLAlchemy 2.x async, neo4j-async-driver, sentence-transformers (cross-encoder), python-igraph, FastAPI, Alembic.

---

## File Structure

**New files:**
- `infra/alembic/versions/0005_chunks_fts.py` — generated `tsvector` column + partial GIN index
- `packages/atlas-knowledge/atlas_knowledge/retrieval/hybrid/__init__.py`
- `packages/atlas-knowledge/atlas_knowledge/retrieval/hybrid/bm25.py` — Postgres FTS search
- `packages/atlas-knowledge/atlas_knowledge/retrieval/hybrid/rrf.py` — pure RRF merge
- `packages/atlas-knowledge/atlas_knowledge/retrieval/hybrid/expansion.py` — thin wrapper around `GraphStore.expand_chunks`
- `packages/atlas-knowledge/atlas_knowledge/retrieval/hybrid/hydrate.py` — bulk-fetch chunk text from Postgres
- `packages/atlas-knowledge/atlas_knowledge/retrieval/hybrid/rerank.py` — `Reranker` (lazy cross-encoder) and `FakeReranker`
- `packages/atlas-knowledge/atlas_knowledge/retrieval/hybrid/pagerank.py` — in-process igraph personalized PageRank
- `packages/atlas-knowledge/atlas_knowledge/retrieval/hybrid/hybrid.py` — `HybridRetriever` orchestrator
- `packages/atlas-knowledge/atlas_knowledge/tests/test_hybrid_*.py` — per-component unit tests
- `packages/atlas-knowledge/atlas_knowledge/tests/test_hybrid_pipeline_integration.py` — full pipeline real-infra test
- `packages/atlas-knowledge/atlas_knowledge/tests/test_hybrid_indirect_concept.py` — Definition-of-Done acceptance test
- `packages/atlas-graph/atlas_graph/expansion.py` — `ExpansionSubgraph` dataclass + Cypher constants
- `packages/atlas-graph/atlas_graph/tests/test_expansion_integration.py` — real-Neo4j expansion test

**Modified files:**
- `packages/atlas-core/atlas_core/config.py` — add `RetrievalConfig` group
- `packages/atlas-knowledge/atlas_knowledge/models/retrieval.py` — add `degraded_stages` field
- `packages/atlas-knowledge/atlas_knowledge/retrieval/__init__.py` — export `RetrieverProtocol`, `HybridRetriever`
- `packages/atlas-knowledge/pyproject.toml` — add `igraph>=0.11`, `atlas-graph` workspace dep
- `packages/atlas-graph/atlas_graph/store.py` — add `expand_chunks` method
- `packages/atlas-graph/atlas_graph/__init__.py` — re-export `ExpansionSubgraph`
- `apps/api/atlas_api/main.py` — branch on retrieval mode, construct `Reranker`, build `HybridRetriever`
- `apps/api/atlas_api/deps.py` — `get_retriever` returns `RetrieverProtocol`
- `apps/api/atlas_api/tests/test_ws_chat_rag.py` — parametrize over `vector` and `hybrid` modes
- `.env.example` — document `ATLAS_RETRIEVAL__MODE` and `ATLAS_RETRIEVAL__RERANKER_MODEL`

---

## Task 1: Config, model field, and package deps

Add the retrieval config group, extend `RetrievalResult` with `degraded_stages`, and add new package deps. This is plumbing for everything that follows.

**Files:**
- Modify: `packages/atlas-core/atlas_core/config.py`
- Modify: `packages/atlas-knowledge/atlas_knowledge/models/retrieval.py`
- Modify: `packages/atlas-knowledge/pyproject.toml`
- Modify: `.env.example`
- Test: `packages/atlas-knowledge/atlas_knowledge/tests/test_models_retrieval.py`

- [ ] **Step 1: Write the failing test for `degraded_stages`**

Add to `packages/atlas-knowledge/atlas_knowledge/tests/test_models_retrieval.py`:

```python
def test_retrieval_result_degraded_stages_default_empty():
    """RetrievalResult.degraded_stages defaults to [] for backward compatibility."""
    from atlas_knowledge.models.retrieval import RetrievalResult

    result = RetrievalResult(query="q", chunks=[])
    assert result.degraded_stages == []


def test_retrieval_result_degraded_stages_accepts_values():
    from atlas_knowledge.models.retrieval import RetrievalResult

    result = RetrievalResult(query="q", chunks=[], degraded_stages=["expansion", "rerank"])
    assert result.degraded_stages == ["expansion", "rerank"]
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest packages/atlas-knowledge/atlas_knowledge/tests/test_models_retrieval.py::test_retrieval_result_degraded_stages_default_empty -v`
Expected: FAIL — `RetrievalResult` has no `degraded_stages` attribute.

- [ ] **Step 3: Add `degraded_stages` to `RetrievalResult`**

Edit `packages/atlas-knowledge/atlas_knowledge/models/retrieval.py`:

```python
class RetrievalResult(AtlasModel):
    """Bundle returned by Retriever.retrieve()."""

    query: str
    chunks: list[ScoredChunk]
    degraded_stages: list[str] = Field(default_factory=list)
```

- [ ] **Step 4: Run the model tests to verify pass**

Run: `uv run pytest packages/atlas-knowledge/atlas_knowledge/tests/test_models_retrieval.py -v`
Expected: PASS for all tests in the file.

- [ ] **Step 5: Add `RetrievalConfig` to `atlas-core/config.py`**

Edit `packages/atlas-core/atlas_core/config.py`:

After `class GraphConfig(...)` and before `class AtlasConfig(...)` insert:

```python
class RetrievalConfig(BaseSettings):
    """Plan 4 hybrid retrieval configuration."""

    model_config = SettingsConfigDict(env_prefix="ATLAS_RETRIEVAL__", extra="ignore")

    mode: Literal["vector", "hybrid"] = "hybrid"
    reranker_model: str = "cross-encoder/ms-marco-MiniLM-L-6-v2"
```

Then in `class AtlasConfig`, add the field next to `graph`:

```python
    graph: GraphConfig = Field(default_factory=GraphConfig)
    retrieval: RetrievalConfig = Field(default_factory=RetrievalConfig)
```

- [ ] **Step 6: Add config test**

Append to `packages/atlas-core/atlas_core/tests/test_config.py` (or create the file if it doesn't exist):

```python
def test_retrieval_config_defaults(monkeypatch):
    from atlas_core.config import AtlasConfig

    # Required env vars for sibling configs
    monkeypatch.setenv("ATLAS_DB__DATABASE_URL", "postgresql://x:y@localhost/db")
    monkeypatch.setenv("ATLAS_GRAPH__PASSWORD", "test-password-1234")

    cfg = AtlasConfig()
    assert cfg.retrieval.mode == "hybrid"
    assert cfg.retrieval.reranker_model == "cross-encoder/ms-marco-MiniLM-L-6-v2"


def test_retrieval_config_env_override(monkeypatch):
    from atlas_core.config import AtlasConfig

    monkeypatch.setenv("ATLAS_DB__DATABASE_URL", "postgresql://x:y@localhost/db")
    monkeypatch.setenv("ATLAS_GRAPH__PASSWORD", "test-password-1234")
    monkeypatch.setenv("ATLAS_RETRIEVAL__MODE", "vector")
    monkeypatch.setenv("ATLAS_RETRIEVAL__RERANKER_MODEL", "custom/model-name")

    cfg = AtlasConfig()
    assert cfg.retrieval.mode == "vector"
    assert cfg.retrieval.reranker_model == "custom/model-name"
```

- [ ] **Step 7: Run config tests**

Run: `uv run pytest packages/atlas-core/atlas_core/tests/test_config.py -v`
Expected: PASS for the two new tests.

- [ ] **Step 8: Add new deps to `atlas-knowledge/pyproject.toml`**

Edit `packages/atlas-knowledge/pyproject.toml` `dependencies` array:

```toml
dependencies = [
    "atlas-core",
    "atlas-graph",
    "pydantic>=2.10",
    "sentence-transformers>=3.3",
    "chromadb>=0.5",
    "pymupdf>=1.25",
    "anyio>=4.6",
    "playwright>=1.45,<2",
    "trafilatura>=1.12,<2",
    "igraph>=0.11",
    "sqlalchemy>=2.0",
]
```

(`sqlalchemy` is needed by `bm25.py` and `hydrate.py` even though it was only transitively pulled before.)

- [ ] **Step 9: Sync the workspace**

Run: `uv sync`
Expected: resolves cleanly, installs `igraph` and adds `atlas-graph` as a workspace dep.

- [ ] **Step 10: Document new env vars in `.env.example`**

Append to `.env.example`:

```
# Plan 4: hybrid retrieval. Set ATLAS_RETRIEVAL__MODE=vector to roll back to Phase 1.
ATLAS_RETRIEVAL__MODE=hybrid
ATLAS_RETRIEVAL__RERANKER_MODEL=cross-encoder/ms-marco-MiniLM-L-6-v2
```

- [ ] **Step 11: Commit**

```bash
git add packages/atlas-core/atlas_core/config.py \
        packages/atlas-core/atlas_core/tests/test_config.py \
        packages/atlas-knowledge/atlas_knowledge/models/retrieval.py \
        packages/atlas-knowledge/atlas_knowledge/tests/test_models_retrieval.py \
        packages/atlas-knowledge/pyproject.toml \
        .env.example \
        uv.lock
git commit -m "feat(retrieval): add RetrievalConfig and degraded_stages plumbing for Plan 4"
```

---

## Task 2: Alembic migration `0005_chunks_fts`

Add the generated `tsvector` column and partial GIN index. `GENERATED ... STORED` populates existing rows during the `ALTER TABLE` so no separate backfill is needed.

**Files:**
- Create: `infra/alembic/versions/0005_chunks_fts.py`
- Test: `infra/alembic/tests/test_0005_chunks_fts.py` (create if dir does not exist)

- [ ] **Step 1: Write the migration**

Create `infra/alembic/versions/0005_chunks_fts.py`:

```python
"""add tsvector + partial GIN index on knowledge_nodes for BM25

Revision ID: 0005
Revises: 0004
Create Date: 2026-04-29
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE knowledge_nodes "
        "ADD COLUMN fts tsvector "
        "GENERATED ALWAYS AS (to_tsvector('english', coalesce(text, ''))) STORED"
    )
    op.execute(
        "CREATE INDEX knowledge_nodes_fts_chunk_idx "
        "ON knowledge_nodes USING GIN (fts) "
        "WHERE type = 'chunk'"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS knowledge_nodes_fts_chunk_idx")
    op.execute("ALTER TABLE knowledge_nodes DROP COLUMN IF EXISTS fts")
```

- [ ] **Step 2: Run upgrade against the dev Postgres**

Bring up the dev stack first if it isn't already:

```bash
cd infra && docker compose up -d postgres && cd ..
```

Then:

```bash
uv run alembic upgrade head
```

Expected: log lines show `Running upgrade 0004 -> 0005, add tsvector + partial GIN index ...` and exit code 0.

- [ ] **Step 3: Verify column + index exist**

Run:

```bash
docker compose -f infra/docker-compose.yml exec postgres \
  psql -U atlas -d atlas -c "\\d+ knowledge_nodes" | grep -E "fts|knowledge_nodes_fts_chunk_idx"
```

Expected: a `fts` column of type `tsvector` (generated stored) and a `knowledge_nodes_fts_chunk_idx` row.

- [ ] **Step 4: Verify a search uses the partial index**

Insert a chunk via psql or hit the existing ingestion path, then:

```bash
docker compose -f infra/docker-compose.yml exec postgres \
  psql -U atlas -d atlas -c \
  "EXPLAIN SELECT id FROM knowledge_nodes WHERE type = 'chunk' AND fts @@ websearch_to_tsquery('english', 'hello');"
```

Expected: plan mentions `knowledge_nodes_fts_chunk_idx`.

- [ ] **Step 5: Verify downgrade works**

```bash
uv run alembic downgrade 0004
```

Then check the column is gone:

```bash
docker compose -f infra/docker-compose.yml exec postgres \
  psql -U atlas -d atlas -c "\\d knowledge_nodes" | grep -c "fts"
```

Expected: 0.

Bring it back up before continuing:

```bash
uv run alembic upgrade head
```

- [ ] **Step 6: Commit**

```bash
git add infra/alembic/versions/0005_chunks_fts.py
git commit -m "feat(db): add tsvector column and partial GIN index on knowledge_nodes"
```

---

## Task 3: BM25 module (`bm25.py`)

Postgres FTS query using `websearch_to_tsquery` and `ts_rank_cd`, returning rank positions for RRF.

**Files:**
- Create: `packages/atlas-knowledge/atlas_knowledge/retrieval/hybrid/__init__.py`
- Create: `packages/atlas-knowledge/atlas_knowledge/retrieval/hybrid/bm25.py`
- Test: `packages/atlas-knowledge/atlas_knowledge/tests/test_hybrid_bm25_integration.py`

- [ ] **Step 1: Create the empty submodule**

Create `packages/atlas-knowledge/atlas_knowledge/retrieval/hybrid/__init__.py`:

```python
"""Hybrid retrieval components (Plan 4)."""
```

- [ ] **Step 2: Write the failing integration test**

Create `packages/atlas-knowledge/atlas_knowledge/tests/test_hybrid_bm25_integration.py`:

```python
"""bm25.search against a real Postgres with the 0005 migration applied."""
from __future__ import annotations

import os
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

pytestmark = pytest.mark.integration


def _enabled() -> bool:
    return os.getenv("ATLAS_RUN_POSTGRES_INTEGRATION") == "1"


@pytest_asyncio.fixture
async def real_pg_session():
    if not _enabled():
        pytest.skip("set ATLAS_RUN_POSTGRES_INTEGRATION=1 to enable")
    url = os.environ["ATLAS_DB__DATABASE_URL"].replace("postgresql://", "postgresql+asyncpg://")
    engine = create_async_engine(url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        yield s
    await engine.dispose()


@pytest_asyncio.fixture
async def isolated_project_id_pg(real_pg_session):
    """Create a project row, yield its id, teardown deletes its chunks."""
    pid = uuid4()
    await real_pg_session.execute(
        text(
            "INSERT INTO projects (id, user_id, name, status) "
            "VALUES (:id, 'matt', 'bm25-test', 'active')"
        ),
        {"id": pid},
    )
    await real_pg_session.commit()
    yield pid
    await real_pg_session.execute(
        text("DELETE FROM knowledge_nodes WHERE project_id = :pid"),
        {"pid": pid},
    )
    await real_pg_session.execute(
        text("DELETE FROM projects WHERE id = :pid"),
        {"pid": pid},
    )
    await real_pg_session.commit()


@pytest.mark.asyncio
async def test_bm25_returns_ranked_chunks(real_pg_session, isolated_project_id_pg):
    from atlas_knowledge.retrieval.hybrid.bm25 import search

    pid = isolated_project_id_pg
    chunk_a = uuid4()
    chunk_b = uuid4()
    chunk_c = uuid4()
    # chunk_a strongly matches "geo lift", chunk_b weakly, chunk_c not at all.
    rows = [
        (chunk_a, "geo lift methodology measures geo lift in geo lift studies", 0),
        (chunk_b, "we used incremental measurement once", 1),
        (chunk_c, "completely unrelated content about coffee", 2),
    ]
    for cid, content, pos in rows:
        await real_pg_session.execute(
            text(
                "INSERT INTO knowledge_nodes (id, user_id, project_id, type, text, metadata) "
                "VALUES (:id, 'matt', :pid, 'chunk', :text, '{}'::jsonb)"
            ),
            {"id": cid, "pid": pid, "text": content},
        )
    await real_pg_session.commit()

    results = await search(
        session=real_pg_session, project_id=pid, query="geo lift", top_k=10
    )

    assert len(results) == 2  # chunk_c does not match
    assert results[0][0] == chunk_a  # strongest match first
    assert results[0][1] == 1  # rank position 1
    assert results[1][0] == chunk_b
    assert results[1][1] == 2


@pytest.mark.asyncio
async def test_bm25_empty_query_returns_empty(real_pg_session, isolated_project_id_pg):
    from atlas_knowledge.retrieval.hybrid.bm25 import search

    results = await search(
        session=real_pg_session,
        project_id=isolated_project_id_pg,
        query="",  # websearch_to_tsquery treats this as empty
        top_k=10,
    )
    assert results == []


@pytest.mark.asyncio
async def test_bm25_filters_by_project(real_pg_session, isolated_project_id_pg):
    from atlas_knowledge.retrieval.hybrid.bm25 import search

    other_pid = uuid4()
    other_chunk = uuid4()
    await real_pg_session.execute(
        text(
            "INSERT INTO projects (id, user_id, name, status) "
            "VALUES (:id, 'matt', 'other', 'active')"
        ),
        {"id": other_pid},
    )
    await real_pg_session.execute(
        text(
            "INSERT INTO knowledge_nodes (id, user_id, project_id, type, text, metadata) "
            "VALUES (:id, 'matt', :pid, 'chunk', 'geo lift', '{}'::jsonb)"
        ),
        {"id": other_chunk, "pid": other_pid},
    )
    await real_pg_session.commit()
    try:
        results = await search(
            session=real_pg_session,
            project_id=isolated_project_id_pg,
            query="geo lift",
            top_k=10,
        )
        assert all(r[0] != other_chunk for r in results)
    finally:
        await real_pg_session.execute(
            text("DELETE FROM knowledge_nodes WHERE project_id = :pid"),
            {"pid": other_pid},
        )
        await real_pg_session.execute(
            text("DELETE FROM projects WHERE id = :pid"),
            {"pid": other_pid},
        )
        await real_pg_session.commit()
```

- [ ] **Step 3: Run the test to verify it fails**

Run: `ATLAS_RUN_POSTGRES_INTEGRATION=1 uv run pytest packages/atlas-knowledge/atlas_knowledge/tests/test_hybrid_bm25_integration.py -v`
Expected: FAIL — `bm25` module does not exist.

- [ ] **Step 4: Implement `bm25.search`**

Create `packages/atlas-knowledge/atlas_knowledge/retrieval/hybrid/bm25.py`:

```python
"""Postgres FTS BM25-flavored search via websearch_to_tsquery + ts_rank_cd."""
from __future__ import annotations

from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

# Returns rank positions (1..N), not raw scores, so RRF can merge across heterogeneous rankers.
_SQL = text(
    """
    SELECT id
    FROM knowledge_nodes
    WHERE type = 'chunk'
      AND project_id = :project_id
      AND fts @@ websearch_to_tsquery('english', :query)
    ORDER BY ts_rank_cd(fts, websearch_to_tsquery('english', :query)) DESC
    LIMIT :top_k
    """
)


async def search(
    session: AsyncSession,
    project_id: UUID,
    query: str,
    top_k: int = 20,
) -> list[tuple[UUID, int]]:
    """Return ``[(chunk_id, rank), ...]`` ordered by descending FTS relevance.

    ``rank`` is 1-indexed ordinal position. An empty match set returns ``[]``;
    callers should treat this as "BM25 found nothing", not an error.
    """
    if not query.strip():
        return []
    result = await session.execute(
        _SQL, {"project_id": project_id, "query": query, "top_k": top_k}
    )
    rows = result.all()
    return [(row[0], idx) for idx, row in enumerate(rows, start=1)]
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `ATLAS_RUN_POSTGRES_INTEGRATION=1 uv run pytest packages/atlas-knowledge/atlas_knowledge/tests/test_hybrid_bm25_integration.py -v`
Expected: PASS for all three tests.

- [ ] **Step 6: Commit**

```bash
git add packages/atlas-knowledge/atlas_knowledge/retrieval/hybrid/__init__.py \
        packages/atlas-knowledge/atlas_knowledge/retrieval/hybrid/bm25.py \
        packages/atlas-knowledge/atlas_knowledge/tests/test_hybrid_bm25_integration.py
git commit -m "feat(retrieval/hybrid): bm25 search via websearch_to_tsquery"
```

---

## Task 4: RRF module (`rrf.py`)

Pure function — Reciprocal Rank Fusion merge.

**Files:**
- Create: `packages/atlas-knowledge/atlas_knowledge/retrieval/hybrid/rrf.py`
- Test: `packages/atlas-knowledge/atlas_knowledge/tests/test_hybrid_rrf.py`

- [ ] **Step 1: Write the failing tests**

Create `packages/atlas-knowledge/atlas_knowledge/tests/test_hybrid_rrf.py`:

```python
"""Pure-function tests for Reciprocal Rank Fusion merge."""
from uuid import UUID, uuid4

from atlas_knowledge.retrieval.hybrid.rrf import merge


def test_merge_empty_input():
    assert merge([], k=60, top_k=20) == []


def test_merge_all_empty_lists():
    assert merge([[], []], k=60, top_k=20) == []


def test_merge_single_list_preserves_order():
    a, b, c = uuid4(), uuid4(), uuid4()
    out = merge([[(a, 1), (b, 2), (c, 3)]], k=60, top_k=10)
    ids = [t[0] for t in out]
    assert ids == [a, b, c]
    # Scores strictly decreasing
    scores = [t[1] for t in out]
    assert scores == sorted(scores, reverse=True)


def test_merge_two_lists_combines_scores():
    a, b, c = uuid4(), uuid4(), uuid4()
    # `a` ranks 1 in both lists -> highest score
    # `b` ranks 2 in both lists
    # `c` only appears in one list at rank 3
    out = merge(
        [[(a, 1), (b, 2), (c, 3)], [(a, 1), (b, 2)]], k=60, top_k=10
    )
    by_id = dict(out)
    assert by_id[a] > by_id[b] > by_id[c]
    # Score formula: a -> 2 * 1/(60+1); b -> 2 * 1/(60+2); c -> 1/(60+3)
    assert abs(by_id[a] - 2 / 61) < 1e-9
    assert abs(by_id[b] - 2 / 62) < 1e-9
    assert abs(by_id[c] - 1 / 63) < 1e-9


def test_merge_truncates_to_top_k():
    ids = [uuid4() for _ in range(50)]
    ranking = [(i, idx) for idx, i in enumerate(ids, start=1)]
    out = merge([ranking], k=60, top_k=20)
    assert len(out) == 20
    # First 20 ids preserved
    assert [t[0] for t in out] == ids[:20]


def test_merge_rank_position_starts_at_one():
    a = uuid4()
    out = merge([[(a, 1)]], k=60, top_k=1)
    assert out[0][0] == a
    assert abs(out[0][1] - 1 / 61) < 1e-9
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest packages/atlas-knowledge/atlas_knowledge/tests/test_hybrid_rrf.py -v`
Expected: FAIL — `rrf` module does not exist.

- [ ] **Step 3: Implement `rrf.merge`**

Create `packages/atlas-knowledge/atlas_knowledge/retrieval/hybrid/rrf.py`:

```python
"""Reciprocal Rank Fusion merge.

Operates on rank positions (1-indexed) so different rankers (BM25 and dense
vector) can be combined without normalizing their raw scores. Standard k=60.
"""
from __future__ import annotations

from collections import defaultdict
from uuid import UUID


def merge(
    rankings: list[list[tuple[UUID, int]]],
    k: int = 60,
    top_k: int = 20,
) -> list[tuple[UUID, float]]:
    """Reciprocal Rank Fusion of ``rankings``.

    For each item ``id``: ``score(id) = sum(1 / (k + rank_i))`` over every
    ranking that contains ``id``. Returns the top-``top_k`` items sorted by
    descending score.
    """
    scores: defaultdict[UUID, float] = defaultdict(float)
    for ranking in rankings:
        for chunk_id, rank in ranking:
            scores[chunk_id] += 1.0 / (k + rank)
    items = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    return items[:top_k]
```

- [ ] **Step 4: Run the tests to verify pass**

Run: `uv run pytest packages/atlas-knowledge/atlas_knowledge/tests/test_hybrid_rrf.py -v`
Expected: PASS for all six tests.

- [ ] **Step 5: Commit**

```bash
git add packages/atlas-knowledge/atlas_knowledge/retrieval/hybrid/rrf.py \
        packages/atlas-knowledge/atlas_knowledge/tests/test_hybrid_rrf.py
git commit -m "feat(retrieval/hybrid): reciprocal rank fusion merge"
```

---

## Task 5: `GraphStore.expand_chunks` and `ExpansionSubgraph`

New `GraphStore` method walking REFERENCES + SEMANTICALLY_NEAR with a separate-cap-per-edge-type budget. Lives in atlas-graph because the Cypher and Neo4j ops belong there.

**Files:**
- Create: `packages/atlas-graph/atlas_graph/expansion.py`
- Modify: `packages/atlas-graph/atlas_graph/store.py` (add `expand_chunks` method)
- Modify: `packages/atlas-graph/atlas_graph/__init__.py` (re-export `ExpansionSubgraph`)
- Test (unit): `packages/atlas-graph/atlas_graph/tests/test_expansion.py`
- Test (integration): `packages/atlas-graph/atlas_graph/tests/test_expansion_integration.py`

- [ ] **Step 1: Write the failing unit test (Cypher shape)**

Create `packages/atlas-graph/atlas_graph/tests/test_expansion.py`:

```python
"""Cypher-shape tests for GraphStore.expand_chunks via the fake driver."""
from __future__ import annotations

from uuid import uuid4

import pytest

from atlas_graph.store import GraphStore


@pytest.mark.asyncio
async def test_expand_chunks_runs_two_cypher_queries(fake_async_driver):
    """expand_chunks runs the SN walk and the REFERENCES walk."""
    store = GraphStore(fake_async_driver)  # type: ignore[arg-type]
    pid = uuid4()
    seeds = [uuid4(), uuid4()]
    sub = await store.expand_chunks(project_id=pid, seeds=seeds, cap=100)

    queries = [c.query for c in fake_async_driver.calls]
    # Two read queries: SEMANTICALLY_NEAR neighbors, REFERENCES neighbors.
    assert any("SEMANTICALLY_NEAR" in q for q in queries)
    assert any("REFERENCES" in q for q in queries)
    # Seeds always present with weight 0.0 by convention (pagerank_global is 0 if absent)
    assert all(seed in sub.nodes for seed in seeds)


@pytest.mark.asyncio
async def test_expand_chunks_empty_seeds_returns_empty(fake_async_driver):
    store = GraphStore(fake_async_driver)  # type: ignore[arg-type]
    sub = await store.expand_chunks(project_id=uuid4(), seeds=[], cap=100)
    assert sub.nodes == {}
    assert sub.edges == []
```

(The fake_async_driver fixture is defined in `tests/conftest.py`; we extend it in step 2 to support `execute_read`. The tests pass once we point the GraphStore at the fake driver and implement `expand_chunks` with `execute_read`.)

- [ ] **Step 2: Extend `_FakeAsyncDriver` to support read transactions returning fixed rows**

Edit `packages/atlas-graph/atlas_graph/tests/conftest.py` `_FakeSession.execute_read`:

```python
    async def execute_read(self, fn):
        tx = AsyncMock()

        async def _run(query, **kwargs):
            self._driver.calls.append(_Call(query=query, kwargs=kwargs))
            # Return an empty async iterator wrapped as result; tests assert on calls only.
            class _R:
                async def __aiter__(self):
                    if False:
                        yield  # pragma: no cover

                async def data(self):
                    return []

            return _R()

        tx.run = _run
        return await fn(tx)
```

- [ ] **Step 3: Create `ExpansionSubgraph` and Cypher constants**

Create `packages/atlas-graph/atlas_graph/expansion.py`:

```python
"""Plan 4 graph expansion contract.

Returned to atlas_knowledge.retrieval.hybrid for per-query graph walks. The
weights have heterogeneous scales (REFERENCES = shared-entity *count*,
SEMANTICALLY_NEAR = *cosine*); see store.expand_chunks for the budget split
that handles this.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from uuid import UUID


@dataclass
class ExpansionSubgraph:
    """Subgraph rooted at a list of seed chunks plus their 1-hop neighbors.

    ``nodes`` maps chunk_id -> pagerank_global score (0.0 if absent).
    ``edges`` is undirected; each tuple is (a, b, weight).
    """

    nodes: dict[UUID, float] = field(default_factory=dict)
    edges: list[tuple[UUID, UUID, float]] = field(default_factory=list)


# 1-hop SEMANTICALLY_NEAR neighbors with cosine on the relation.
EXPAND_SN_CYPHER = """
MATCH (c:Chunk) WHERE c.id IN $seeds AND c.project_id = $pid
MATCH (c)-[r:SEMANTICALLY_NEAR]-(n:Chunk)
WHERE n.project_id = $pid
RETURN c.id AS a, n.id AS b, r.cosine AS w,
       coalesce(c.pagerank_global, 0.0) AS pa,
       coalesce(n.pagerank_global, 0.0) AS pb
"""

# 1-hop REFERENCES-via-Entity neighbors with weight = COUNT(DISTINCT shared_entity).
EXPAND_REF_CYPHER = """
MATCH (c:Chunk) WHERE c.id IN $seeds AND c.project_id = $pid
MATCH (c)-[:REFERENCES]->(e:Entity)<-[:REFERENCES]-(n:Chunk)
WHERE n.project_id = $pid AND n.id <> c.id
WITH c, n, count(DISTINCT e) AS w
RETURN c.id AS a, n.id AS b, toFloat(w) AS w,
       coalesce(c.pagerank_global, 0.0) AS pa,
       coalesce(n.pagerank_global, 0.0) AS pb
"""

# Pagerank for the seeds themselves (so ExpansionSubgraph.nodes carries a value
# for every seed even when the seed has no neighbors).
SEEDS_PR_CYPHER = """
MATCH (c:Chunk) WHERE c.id IN $seeds AND c.project_id = $pid
RETURN c.id AS id, coalesce(c.pagerank_global, 0.0) AS pr
"""


def merge_neighbors_with_budget(
    seeds: list[UUID],
    sn_rows: list[tuple[UUID, UUID, float, float, float]],
    ref_rows: list[tuple[UUID, UUID, float, float, float]],
    seed_prs: dict[UUID, float],
    cap: int,
) -> ExpansionSubgraph:
    """Apply the per-edge-type cap split.

    Seeds are always retained. Of the remaining ``cap - len(seeds)`` budget,
    each edge type gets up to half (sorted by descending weight); surplus
    rolls over to the other side.
    """
    sub = ExpansionSubgraph()
    for s in seeds:
        sub.nodes[s] = seed_prs.get(s, 0.0)

    sn_neighbors: dict[UUID, tuple[float, float]] = {}  # node -> (best_weight, pr)
    sn_edges: list[tuple[UUID, UUID, float]] = []
    for a, b, w, _pa, pb in sn_rows:
        sn_edges.append((a, b, float(w)))
        prev = sn_neighbors.get(b)
        if prev is None or float(w) > prev[0]:
            sn_neighbors[b] = (float(w), float(pb))

    ref_neighbors: dict[UUID, tuple[float, float]] = {}
    ref_edges: list[tuple[UUID, UUID, float]] = []
    for a, b, w, _pa, pb in ref_rows:
        ref_edges.append((a, b, float(w)))
        prev = ref_neighbors.get(b)
        if prev is None or float(w) > prev[0]:
            ref_neighbors[b] = (float(w), float(pb))

    # Drop neighbors that are already seeds (they're already in sub.nodes).
    seed_set = set(seeds)
    sn_sorted = sorted(
        ((nid, w_pr) for nid, w_pr in sn_neighbors.items() if nid not in seed_set),
        key=lambda kv: kv[1][0],
        reverse=True,
    )
    ref_sorted = sorted(
        ((nid, w_pr) for nid, w_pr in ref_neighbors.items() if nid not in seed_set),
        key=lambda kv: kv[1][0],
        reverse=True,
    )

    remaining = max(0, cap - len(sub.nodes))
    sn_quota = remaining // 2
    ref_quota = remaining - sn_quota

    # Allocate, then roll surplus.
    sn_take = min(sn_quota, len(sn_sorted))
    ref_take = min(ref_quota, len(ref_sorted))
    sn_surplus = sn_quota - sn_take
    ref_surplus = ref_quota - ref_take
    if sn_surplus > 0 and ref_take < len(ref_sorted):
        extra = min(sn_surplus, len(ref_sorted) - ref_take)
        ref_take += extra
    if ref_surplus > 0 and sn_take < len(sn_sorted):
        extra = min(ref_surplus, len(sn_sorted) - sn_take)
        sn_take += extra

    for nid, (_w, pr) in sn_sorted[:sn_take]:
        sub.nodes[nid] = pr
    for nid, (_w, pr) in ref_sorted[:ref_take]:
        # Ref-side may already be present from SN; keep the existing pr (same node, same value).
        sub.nodes.setdefault(nid, pr)

    # Edges: keep only those whose endpoints both survived.
    surviving = set(sub.nodes.keys())
    for a, b, w in sn_edges + ref_edges:
        if a in surviving and b in surviving:
            sub.edges.append((a, b, w))

    return sub
```

- [ ] **Step 4: Add `expand_chunks` to `GraphStore`**

Edit `packages/atlas-graph/atlas_graph/store.py`. Add the import at the top (next to other imports inside the class context — keep it inline-imported to mirror the rest of the file's lazy imports):

```python
    async def expand_chunks(
        self,
        *,
        project_id: UUID,
        seeds: list[UUID],
        cap: int = 100,
    ) -> "ExpansionSubgraph":
        """Return the seeds + 1-hop neighbors via REFERENCES and SEMANTICALLY_NEAR.

        Uses a separate-cap-per-edge-type budget because the two weight scales
        (cosine vs shared-entity count) are not comparable.
        """
        from atlas_graph.expansion import (
            EXPAND_REF_CYPHER,
            EXPAND_SN_CYPHER,
            SEEDS_PR_CYPHER,
            ExpansionSubgraph,
            merge_neighbors_with_budget,
        )

        if not seeds:
            return ExpansionSubgraph()

        seed_strs = [str(s) for s in seeds]

        async def _read(tx):
            sn_result = await tx.run(EXPAND_SN_CYPHER, seeds=seed_strs, pid=str(project_id))
            sn_rows_raw = await sn_result.data()
            ref_result = await tx.run(
                EXPAND_REF_CYPHER, seeds=seed_strs, pid=str(project_id)
            )
            ref_rows_raw = await ref_result.data()
            seed_pr_result = await tx.run(
                SEEDS_PR_CYPHER, seeds=seed_strs, pid=str(project_id)
            )
            seed_pr_raw = await seed_pr_result.data()
            return sn_rows_raw, ref_rows_raw, seed_pr_raw

        async with self._session() as s:
            sn_raw, ref_raw, seed_pr_raw = await s.execute_read(_read)

        from uuid import UUID as _UUID

        sn_rows = [
            (_UUID(r["a"]), _UUID(r["b"]), float(r["w"]), float(r["pa"]), float(r["pb"]))
            for r in sn_raw
        ]
        ref_rows = [
            (_UUID(r["a"]), _UUID(r["b"]), float(r["w"]), float(r["pa"]), float(r["pb"]))
            for r in ref_raw
        ]
        seed_prs = {_UUID(r["id"]): float(r["pr"]) for r in seed_pr_raw}

        return merge_neighbors_with_budget(seeds, sn_rows, ref_rows, seed_prs, cap)
```

- [ ] **Step 5: Re-export `ExpansionSubgraph`**

Edit `packages/atlas-graph/atlas_graph/__init__.py`. Add to the existing exports:

```python
from atlas_graph.expansion import ExpansionSubgraph

__all__ = [
    # ... existing entries ...
    "ExpansionSubgraph",
]
```

(If the file does not currently use `__all__`, simply add the import line at the bottom.)

- [ ] **Step 6: Run unit tests to verify pass**

Run: `uv run pytest packages/atlas-graph/atlas_graph/tests/test_expansion.py -v`
Expected: PASS for both unit tests (the fake driver returns empty lists; the budget splitter correctly handles empty input).

- [ ] **Step 7: Add the budget-split unit test**

Append to `packages/atlas-graph/atlas_graph/tests/test_expansion.py`:

```python
from uuid import uuid4

from atlas_graph.expansion import merge_neighbors_with_budget


def test_budget_split_caps_at_total():
    seeds = [uuid4() for _ in range(2)]
    sn_rows = [(seeds[0], uuid4(), 0.9, 0.1, 0.1) for _ in range(40)]
    ref_rows = [(seeds[0], uuid4(), 5.0, 0.1, 0.1) for _ in range(40)]
    sub = merge_neighbors_with_budget(seeds, sn_rows, ref_rows, {}, cap=20)
    # 2 seeds + up to 18 neighbors total
    assert len(sub.nodes) <= 20
    assert all(s in sub.nodes for s in seeds)


def test_budget_split_rolls_over_surplus():
    seeds = [uuid4()]
    sn_rows = [(seeds[0], uuid4(), 0.9, 0.0, 0.0) for _ in range(2)]  # only 2 SN neighbors
    ref_rows = [(seeds[0], uuid4(), 5.0, 0.0, 0.0) for _ in range(20)]  # many REF neighbors
    sub = merge_neighbors_with_budget(seeds, sn_rows, ref_rows, {}, cap=11)
    # 1 seed + 10 budget. SN supplies 2; REF takes the surplus (5+5=10).
    assert len(sub.nodes) == 1 + 2 + (11 - 1 - 2)


def test_budget_split_dedupes_seeds_from_neighbors():
    seeds = [uuid4()]
    # SN edge points back to the seed itself (self-loop hypothetical) — should not duplicate.
    sn_rows = [(seeds[0], seeds[0], 0.9, 0.0, 0.0)]
    sub = merge_neighbors_with_budget(seeds, sn_rows, [], {}, cap=10)
    assert len(sub.nodes) == 1
```

- [ ] **Step 8: Run unit tests to verify pass**

Run: `uv run pytest packages/atlas-graph/atlas_graph/tests/test_expansion.py -v`
Expected: PASS for all 5 tests.

- [ ] **Step 9: Write the integration test against real Neo4j**

Create `packages/atlas-graph/atlas_graph/tests/test_expansion_integration.py`:

```python
"""GraphStore.expand_chunks against a real Neo4j (Plan 3 schema required)."""
from __future__ import annotations

from uuid import uuid4

import pytest

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_expand_chunks_walks_sn_and_references(
    real_graph_store, real_neo4j_driver, isolated_project_id,
):
    pid = isolated_project_id
    seed = uuid4()
    sn_neighbor = uuid4()
    ref_neighbor = uuid4()
    isolated = uuid4()  # has no edges; should not appear

    async with real_neo4j_driver.session() as s:
        # Seed + neighbors as Chunks
        for cid in (seed, sn_neighbor, ref_neighbor, isolated):
            await s.run(
                "MERGE (c:Chunk {id: $id}) SET c.project_id = $pid, c.pagerank_global = 0.1",
                id=str(cid), pid=str(pid),
            )
        # SEMANTICALLY_NEAR edge
        await s.run(
            "MATCH (a:Chunk {id: $a}), (b:Chunk {id: $b}) "
            "MERGE (a)-[r:SEMANTICALLY_NEAR]-(b) SET r.cosine = 0.91",
            a=str(seed), b=str(sn_neighbor),
        )
        # Shared Entity for REFERENCES
        eid = uuid4()
        await s.run(
            "MERGE (e:Entity {project_id: $pid, name: 'circlek', type: 'CLIENT'}) "
            "SET e.id = $eid",
            pid=str(pid), eid=str(eid),
        )
        await s.run(
            "MATCH (c:Chunk {id: $c}), (e:Entity {id: $eid}) "
            "MERGE (c)-[:REFERENCES]->(e)",
            c=str(seed), eid=str(eid),
        )
        await s.run(
            "MATCH (c:Chunk {id: $c}), (e:Entity {id: $eid}) "
            "MERGE (c)-[:REFERENCES]->(e)",
            c=str(ref_neighbor), eid=str(eid),
        )

    sub = await real_graph_store.expand_chunks(
        project_id=pid, seeds=[seed], cap=100
    )

    assert seed in sub.nodes
    assert sn_neighbor in sub.nodes
    assert ref_neighbor in sub.nodes
    assert isolated not in sub.nodes

    # SN edge weight = cosine
    sn_edges = [(a, b, w) for (a, b, w) in sub.edges if w == 0.91]
    assert len(sn_edges) >= 1
    # REF edge weight = shared-entity count = 1 (one shared entity)
    ref_edges = [(a, b, w) for (a, b, w) in sub.edges if w == 1.0]
    assert any(seed in (a, b) and ref_neighbor in (a, b) for a, b, _ in ref_edges)


@pytest.mark.asyncio
async def test_expand_chunks_respects_cap_with_split(
    real_graph_store, real_neo4j_driver, isolated_project_id,
):
    pid = isolated_project_id
    seed = uuid4()
    async with real_neo4j_driver.session() as s:
        await s.run(
            "MERGE (c:Chunk {id: $id}) SET c.project_id = $pid",
            id=str(seed), pid=str(pid),
        )
        eid = uuid4()
        await s.run(
            "MERGE (e:Entity {project_id: $pid, name: 'hub', type: 'CLIENT'}) "
            "SET e.id = $eid",
            pid=str(pid), eid=str(eid),
        )
        await s.run(
            "MATCH (c:Chunk {id: $c}), (e:Entity {id: $eid}) "
            "MERGE (c)-[:REFERENCES]->(e)",
            c=str(seed), eid=str(eid),
        )
        # 30 ref-neighbors all sharing the hub entity, 0 SN neighbors
        for _ in range(30):
            n = uuid4()
            await s.run(
                "MERGE (c:Chunk {id: $id}) SET c.project_id = $pid",
                id=str(n), pid=str(pid),
            )
            await s.run(
                "MATCH (c:Chunk {id: $c}), (e:Entity {id: $eid}) "
                "MERGE (c)-[:REFERENCES]->(e)",
                c=str(n), eid=str(eid),
            )

    sub = await real_graph_store.expand_chunks(
        project_id=pid, seeds=[seed], cap=10
    )
    # Seed + up to 9 neighbors. Since SN supplies 0, REF takes the full surplus.
    assert len(sub.nodes) == 10
```

- [ ] **Step 10: Run the integration test**

Bring up the dev stack:

```bash
cd infra && docker compose up -d neo4j && cd ..
```

Run:

```bash
ATLAS_RUN_NEO4J_INTEGRATION=1 \
ATLAS_GRAPH__URI=bolt://localhost:7687 \
ATLAS_GRAPH__PASSWORD=$(grep ATLAS_GRAPH__PASSWORD .env | cut -d= -f2) \
uv run pytest packages/atlas-graph/atlas_graph/tests/test_expansion_integration.py -v
```

Expected: PASS.

- [ ] **Step 11: Commit**

```bash
git add packages/atlas-graph/atlas_graph/expansion.py \
        packages/atlas-graph/atlas_graph/store.py \
        packages/atlas-graph/atlas_graph/__init__.py \
        packages/atlas-graph/atlas_graph/tests/test_expansion.py \
        packages/atlas-graph/atlas_graph/tests/test_expansion_integration.py \
        packages/atlas-graph/atlas_graph/tests/conftest.py
git commit -m "feat(graph): GraphStore.expand_chunks for 1-hop subgraph expansion"
```

---

## Task 6: `hydrate.py` — bulk fetch chunk text

Single SELECT pulling chunk text + parent_title for the union of candidate IDs from BM25/vector/expansion.

**Files:**
- Create: `packages/atlas-knowledge/atlas_knowledge/retrieval/hybrid/hydrate.py`
- Test: `packages/atlas-knowledge/atlas_knowledge/tests/test_hybrid_hydrate_integration.py`

- [ ] **Step 1: Write the failing integration test**

Create `packages/atlas-knowledge/atlas_knowledge/tests/test_hybrid_hydrate_integration.py`:

```python
from __future__ import annotations

import os
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

pytestmark = pytest.mark.integration


def _enabled() -> bool:
    return os.getenv("ATLAS_RUN_POSTGRES_INTEGRATION") == "1"


@pytest_asyncio.fixture
async def real_pg_session():
    if not _enabled():
        pytest.skip("set ATLAS_RUN_POSTGRES_INTEGRATION=1 to enable")
    url = os.environ["ATLAS_DB__DATABASE_URL"].replace("postgresql://", "postgresql+asyncpg://")
    engine = create_async_engine(url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        yield s
    await engine.dispose()


@pytest.mark.asyncio
async def test_hydrate_returns_text_and_title(real_pg_session):
    from atlas_knowledge.retrieval.hybrid.hydrate import hydrate

    pid = uuid4()
    doc_id = uuid4()
    chunk_a = uuid4()
    chunk_b = uuid4()

    await real_pg_session.execute(
        text(
            "INSERT INTO projects (id, user_id, name, status) "
            "VALUES (:id, 'matt', 'h', 'active')"
        ),
        {"id": pid},
    )
    await real_pg_session.execute(
        text(
            "INSERT INTO knowledge_nodes (id, user_id, project_id, type, title, text, metadata) "
            "VALUES (:id, 'matt', :pid, 'document', 'Doc Title', '', '{}'::jsonb)"
        ),
        {"id": doc_id, "pid": pid},
    )
    for cid, content in ((chunk_a, "alpha text"), (chunk_b, "beta text")):
        await real_pg_session.execute(
            text(
                "INSERT INTO knowledge_nodes (id, user_id, project_id, type, parent_id, text, metadata) "
                "VALUES (:id, 'matt', :pid, 'chunk', :doc, :text, '{}'::jsonb)"
            ),
            {"id": cid, "pid": pid, "doc": doc_id, "text": content},
        )
    await real_pg_session.commit()

    try:
        out = await hydrate(real_pg_session, [chunk_a, chunk_b, uuid4()])
        assert set(out.keys()) == {chunk_a, chunk_b}
        assert out[chunk_a].text == "alpha text"
        assert out[chunk_a].parent_title == "Doc Title"
        assert out[chunk_a].parent_id == doc_id
        assert out[chunk_a].user_id == "matt"
        assert out[chunk_a].created_at is not None
        assert out[chunk_b].text == "beta text"
    finally:
        await real_pg_session.execute(
            text("DELETE FROM knowledge_nodes WHERE project_id = :pid"),
            {"pid": pid},
        )
        await real_pg_session.execute(
            text("DELETE FROM projects WHERE id = :pid"),
            {"pid": pid},
        )
        await real_pg_session.commit()


@pytest.mark.asyncio
async def test_hydrate_empty_input(real_pg_session):
    from atlas_knowledge.retrieval.hybrid.hydrate import hydrate

    out = await hydrate(real_pg_session, [])
    assert out == {}
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `ATLAS_RUN_POSTGRES_INTEGRATION=1 uv run pytest packages/atlas-knowledge/atlas_knowledge/tests/test_hybrid_hydrate_integration.py -v`
Expected: FAIL — `hydrate` does not exist.

- [ ] **Step 3: Implement `hydrate`**

Create `packages/atlas-knowledge/atlas_knowledge/retrieval/hybrid/hydrate.py`:

```python
"""Bulk-fetch chunk text + parent_title from Postgres in one round-trip."""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


@dataclass(frozen=True)
class ChunkText:
    id: UUID
    user_id: str
    text: str
    parent_id: UUID
    parent_title: str | None
    created_at: datetime


_SQL = text(
    """
    SELECT c.id, c.user_id, c.text, c.parent_id, d.title AS parent_title, c.created_at
    FROM knowledge_nodes c
    LEFT JOIN knowledge_nodes d ON d.id = c.parent_id
    WHERE c.id = ANY(:ids) AND c.type = 'chunk'
    """
)


async def hydrate(
    session: AsyncSession,
    chunk_ids: Iterable[UUID],
) -> dict[UUID, ChunkText]:
    """Return ``{id: ChunkText}``. Missing or non-chunk IDs are silently dropped."""
    ids = list(chunk_ids)
    if not ids:
        return {}
    result = await session.execute(_SQL, {"ids": ids})
    out: dict[UUID, ChunkText] = {}
    for row in result.all():
        out[row[0]] = ChunkText(
            id=row[0],
            user_id=row[1],
            text=row[2] or "",
            parent_id=row[3],
            parent_title=row[4],
            created_at=row[5],
        )
    return out
```

- [ ] **Step 4: Run the test to verify pass**

Run: `ATLAS_RUN_POSTGRES_INTEGRATION=1 uv run pytest packages/atlas-knowledge/atlas_knowledge/tests/test_hybrid_hydrate_integration.py -v`
Expected: PASS for both tests.

- [ ] **Step 5: Commit**

```bash
git add packages/atlas-knowledge/atlas_knowledge/retrieval/hybrid/hydrate.py \
        packages/atlas-knowledge/atlas_knowledge/tests/test_hybrid_hydrate_integration.py
git commit -m "feat(retrieval/hybrid): bulk hydrate chunk text from postgres"
```

---

## Task 7: `rerank.py` — `Reranker` (lazy cross-encoder) and `FakeReranker`

Lazy-loaded cross-encoder for production; deterministic `FakeReranker` for unit tests so the rest of the pipeline can be exercised without loading the model.

**Files:**
- Create: `packages/atlas-knowledge/atlas_knowledge/retrieval/hybrid/rerank.py`
- Test: `packages/atlas-knowledge/atlas_knowledge/tests/test_hybrid_rerank.py`

- [ ] **Step 1: Write the failing tests**

Create `packages/atlas-knowledge/atlas_knowledge/tests/test_hybrid_rerank.py`:

```python
"""Unit tests for the rerank module — exercise FakeReranker only."""
from __future__ import annotations

from uuid import uuid4

import pytest

from atlas_knowledge.retrieval.hybrid.rerank import FakeReranker


@pytest.mark.asyncio
async def test_fake_reranker_preserves_order_with_explicit_scores():
    a, b, c = uuid4(), uuid4(), uuid4()
    rr = FakeReranker(scores={a: 0.9, b: 0.5, c: 0.1})
    out = await rr.rerank("q", [(a, "x"), (b, "y"), (c, "z")], top_k=10)
    ids = [t[0] for t in out]
    assert ids == [a, b, c]


@pytest.mark.asyncio
async def test_fake_reranker_default_score_is_zero():
    a = uuid4()
    rr = FakeReranker(scores={})
    out = await rr.rerank("q", [(a, "x")], top_k=10)
    assert out == [(a, 0.0)]


@pytest.mark.asyncio
async def test_fake_reranker_truncates_to_top_k():
    ids = [uuid4() for _ in range(5)]
    rr = FakeReranker(scores={i: float(idx) for idx, i in enumerate(ids)})
    out = await rr.rerank("q", [(i, "t") for i in ids], top_k=3)
    assert len(out) == 3
    # Highest scores first; FakeReranker sorts descending.
    assert [t[0] for t in out] == ids[::-1][:3]


@pytest.mark.asyncio
async def test_fake_reranker_empty_input():
    rr = FakeReranker(scores={})
    assert await rr.rerank("q", [], top_k=10) == []
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest packages/atlas-knowledge/atlas_knowledge/tests/test_hybrid_rerank.py -v`
Expected: FAIL — `rerank` module does not exist.

- [ ] **Step 3: Implement `Reranker` and `FakeReranker`**

Create `packages/atlas-knowledge/atlas_knowledge/retrieval/hybrid/rerank.py`:

```python
"""Cross-encoder reranker — lazy-loaded singleton plus FakeReranker for tests.

The real reranker uses ``sentence_transformers.CrossEncoder``. The tokenizer
silently truncates inputs longer than the model's max sequence length (512
tokens for ms-marco-MiniLM-L-6-v2); ATLAS chunks target ≤512 tokens by design.
"""
from __future__ import annotations

import asyncio
from typing import Protocol
from uuid import UUID


class RerankerProtocol(Protocol):
    async def rerank(
        self,
        query: str,
        candidates: list[tuple[UUID, str]],
        top_k: int = 30,
    ) -> list[tuple[UUID, float]]: ...


class Reranker:
    """Lazy-loaded sentence-transformers CrossEncoder.

    Constructed once in the API lifespan; the underlying model is downloaded
    and held in memory on the first ``rerank()`` call. Predict runs inside
    ``asyncio.to_thread`` to keep the event loop responsive.
    """

    def __init__(self, model_name: str = "cross-encoder/ms-marco-MiniLM-L-6-v2") -> None:
        self._model_name = model_name
        self._model = None  # type: ignore[var-annotated]

    def _ensure_loaded(self):
        if self._model is None:
            from sentence_transformers import CrossEncoder

            self._model = CrossEncoder(self._model_name)
        return self._model

    async def rerank(
        self,
        query: str,
        candidates: list[tuple[UUID, str]],
        top_k: int = 30,
    ) -> list[tuple[UUID, float]]:
        if not candidates:
            return []
        # Cap the input — rerank cost is O(n).
        capped = candidates[:top_k]
        model = self._ensure_loaded()
        pairs = [(query, txt) for _, txt in capped]

        def _predict() -> list[float]:
            return [float(s) for s in model.predict(pairs)]

        scores = await asyncio.to_thread(_predict)
        ids = [cid for cid, _ in capped]
        out = list(zip(ids, scores, strict=True))
        out.sort(key=lambda kv: kv[1], reverse=True)
        return out


class FakeReranker:
    """Deterministic reranker for unit tests.

    Returns scores from ``scores`` (default 0.0 for unknown ids), sorted descending.
    Caps at ``top_k``.
    """

    def __init__(self, scores: dict[UUID, float]) -> None:
        self._scores = scores

    async def rerank(
        self,
        query: str,
        candidates: list[tuple[UUID, str]],
        top_k: int = 30,
    ) -> list[tuple[UUID, float]]:
        if not candidates:
            return []
        out = [(cid, self._scores.get(cid, 0.0)) for cid, _ in candidates]
        out.sort(key=lambda kv: kv[1], reverse=True)
        return out[:top_k]
```

- [ ] **Step 4: Run the test to verify pass**

Run: `uv run pytest packages/atlas-knowledge/atlas_knowledge/tests/test_hybrid_rerank.py -v`
Expected: PASS for all four tests.

- [ ] **Step 5: Commit**

```bash
git add packages/atlas-knowledge/atlas_knowledge/retrieval/hybrid/rerank.py \
        packages/atlas-knowledge/atlas_knowledge/tests/test_hybrid_rerank.py
git commit -m "feat(retrieval/hybrid): Reranker (lazy cross-encoder) and FakeReranker"
```

---

## Task 8: `pagerank.py` — in-process igraph personalized PageRank

Pure function over `ExpansionSubgraph`. Builds an `igraph.Graph`, runs `personalized_pagerank` with reset vertices = seeds.

**Files:**
- Create: `packages/atlas-knowledge/atlas_knowledge/retrieval/hybrid/pagerank.py`
- Test: `packages/atlas-knowledge/atlas_knowledge/tests/test_hybrid_pagerank.py`

- [ ] **Step 1: Write the failing tests**

Create `packages/atlas-knowledge/atlas_knowledge/tests/test_hybrid_pagerank.py`:

```python
"""Unit tests for in-process personalized PageRank over ExpansionSubgraph."""
from __future__ import annotations

from uuid import uuid4

from atlas_graph.expansion import ExpansionSubgraph
from atlas_knowledge.retrieval.hybrid.pagerank import personalized


def test_empty_subgraph_returns_empty():
    assert personalized(ExpansionSubgraph(), [], damping=0.85) == {}


def test_empty_seeds_returns_empty():
    sub = ExpansionSubgraph(nodes={uuid4(): 0.0}, edges=[])
    assert personalized(sub, [], damping=0.85) == {}


def test_seed_outweighs_far_node():
    a, b, c = uuid4(), uuid4(), uuid4()
    # Chain a -- b -- c. Seed a; PPR should rank a > b > c.
    sub = ExpansionSubgraph(
        nodes={a: 0.0, b: 0.0, c: 0.0},
        edges=[(a, b, 1.0), (b, c, 1.0)],
    )
    out = personalized(sub, seeds=[a], damping=0.85)
    assert out[a] > out[b] > out[c]


def test_scores_normalized_to_sum_one():
    nodes = {uuid4(): 0.0 for _ in range(5)}
    ids = list(nodes.keys())
    edges = [(ids[i], ids[i + 1], 1.0) for i in range(4)]
    sub = ExpansionSubgraph(nodes=nodes, edges=edges)
    out = personalized(sub, seeds=[ids[0]], damping=0.85)
    assert abs(sum(out.values()) - 1.0) < 1e-6


def test_isolated_seed_returns_full_weight():
    a = uuid4()
    sub = ExpansionSubgraph(nodes={a: 0.0}, edges=[])
    out = personalized(sub, seeds=[a], damping=0.85)
    assert abs(out[a] - 1.0) < 1e-6
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest packages/atlas-knowledge/atlas_knowledge/tests/test_hybrid_pagerank.py -v`
Expected: FAIL — `pagerank` module does not exist.

- [ ] **Step 3: Implement `personalized`**

Create `packages/atlas-knowledge/atlas_knowledge/retrieval/hybrid/pagerank.py`:

```python
"""In-process personalized PageRank over an ExpansionSubgraph using igraph.

Subgraphs at retrieval time are small (≤100 nodes); building a fresh igraph.Graph
per query is sub-millisecond and avoids the cost of a Cypher-side gds projection.
"""
from __future__ import annotations

from uuid import UUID

import igraph as ig

from atlas_graph.expansion import ExpansionSubgraph


def personalized(
    subgraph: ExpansionSubgraph,
    seeds: list[UUID],
    damping: float = 0.85,
) -> dict[UUID, float]:
    """Return chunk_id -> personalized PageRank score (sums to 1.0).

    ``seeds`` are the reset vertices. Seed ids that are not present in
    ``subgraph.nodes`` are silently dropped. Empty subgraph or empty
    surviving-seed list returns ``{}``.
    """
    if not subgraph.nodes or not seeds:
        return {}
    surviving_seeds = [s for s in seeds if s in subgraph.nodes]
    if not surviving_seeds:
        return {}

    node_ids = list(subgraph.nodes.keys())
    index = {nid: i for i, nid in enumerate(node_ids)}

    g = ig.Graph(n=len(node_ids), directed=False)
    if subgraph.edges:
        edge_list = [(index[a], index[b]) for a, b, _ in subgraph.edges if a in index and b in index]
        weights = [w for a, b, w in subgraph.edges if a in index and b in index]
        g.add_edges(edge_list)
        g.es["weight"] = weights

    reset = [0.0] * len(node_ids)
    seed_weight = 1.0 / len(surviving_seeds)
    for s in surviving_seeds:
        reset[index[s]] = seed_weight

    weights_arg = g.es["weight"] if g.ecount() > 0 else None
    scores = g.personalized_pagerank(
        damping=damping, reset=reset, weights=weights_arg
    )

    return {nid: float(scores[index[nid]]) for nid in node_ids}
```

- [ ] **Step 4: Run the test to verify pass**

Run: `uv run pytest packages/atlas-knowledge/atlas_knowledge/tests/test_hybrid_pagerank.py -v`
Expected: PASS for all five tests.

- [ ] **Step 5: Commit**

```bash
git add packages/atlas-knowledge/atlas_knowledge/retrieval/hybrid/pagerank.py \
        packages/atlas-knowledge/atlas_knowledge/tests/test_hybrid_pagerank.py
git commit -m "feat(retrieval/hybrid): personalized PageRank via igraph"
```

---

## Task 9: `hybrid.py` — `HybridRetriever` orchestrator

Wire the seven-stage pipeline with per-stage graceful degradation. Use `FakeReranker` in tests so the model is never loaded.

**Files:**
- Create: `packages/atlas-knowledge/atlas_knowledge/retrieval/hybrid/expansion.py`
- Create: `packages/atlas-knowledge/atlas_knowledge/retrieval/hybrid/hybrid.py`
- Test (unit, mocked vector store + graph store): `packages/atlas-knowledge/atlas_knowledge/tests/test_hybrid_orchestrator.py`
- Test (integration, real Postgres + Neo4j + FakeReranker): `packages/atlas-knowledge/atlas_knowledge/tests/test_hybrid_pipeline_integration.py`

- [ ] **Step 1: Create the thin expansion wrapper**

Create `packages/atlas-knowledge/atlas_knowledge/retrieval/hybrid/expansion.py`:

```python
"""Thin atlas-knowledge wrapper around GraphStore.expand_chunks.

Lives here (not in atlas-graph) so that the hybrid orchestration imports
its dependencies from one package.
"""
from __future__ import annotations

from uuid import UUID

from atlas_graph import ExpansionSubgraph
from atlas_graph.store import GraphStore


async def expand(
    graph_store: GraphStore,
    project_id: UUID,
    seeds: list[UUID],
    cap: int = 100,
) -> ExpansionSubgraph:
    return await graph_store.expand_chunks(
        project_id=project_id, seeds=seeds, cap=cap
    )
```

- [ ] **Step 2: Write the failing orchestrator unit test**

Create `packages/atlas-knowledge/atlas_knowledge/tests/test_hybrid_orchestrator.py`:

```python
"""HybridRetriever orchestrator with mocked component edges.

Exercises pipeline wiring + per-stage degradation policy without real Postgres
or Neo4j. The integration test (test_hybrid_pipeline_integration.py) covers
the same pipeline against real infra.
"""
from __future__ import annotations

import math
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from atlas_graph.expansion import ExpansionSubgraph
from atlas_knowledge.models.nodes import KnowledgeNode
from atlas_knowledge.models.retrieval import RetrievalQuery, ScoredChunk
from atlas_knowledge.retrieval.hybrid.hybrid import HybridRetriever
from atlas_knowledge.retrieval.hybrid.hydrate import ChunkText
from atlas_knowledge.retrieval.hybrid.rerank import FakeReranker


def _node(cid, text="t") -> KnowledgeNode:
    from datetime import UTC, datetime
    return KnowledgeNode(
        id=cid,
        user_id="matt",
        project_id=uuid4(),
        type="chunk",
        text=text,
        title=None,
        metadata={},
        created_at=datetime.now(UTC),
    )


def _scored(cid, score=0.5) -> ScoredChunk:
    return ScoredChunk(chunk=_node(cid), score=score, parent_title="Doc")


@pytest.fixture
def hybrid_with_mocks(monkeypatch):
    """Build a HybridRetriever where every external dep is mocked."""
    embedder = AsyncMock()
    embedder.embed_query.return_value = [0.1] * 8
    vector_store = AsyncMock()
    graph_store = AsyncMock()
    session_factory = AsyncMock()

    return {
        "embedder": embedder,
        "vector_store": vector_store,
        "graph_store": graph_store,
        "session_factory": session_factory,
    }


@pytest.mark.asyncio
async def test_happy_path_returns_top_k_with_no_degradation(hybrid_with_mocks, monkeypatch):
    a, b, c = uuid4(), uuid4(), uuid4()
    # Vector returns [(a, 1), (b, 2)]
    hybrid_with_mocks["vector_store"].search.return_value = [_scored(a), _scored(b)]
    # BM25 mocked via monkeypatch on the bm25 module
    from atlas_knowledge.retrieval.hybrid import bm25 as bm25_mod
    from atlas_knowledge.retrieval.hybrid import hydrate as hydrate_mod

    async def _bm25(session, project_id, query, top_k):
        return [(a, 1), (c, 2)]
    monkeypatch.setattr(bm25_mod, "search", _bm25)

    async def _hydrate(session, ids):
        from datetime import UTC, datetime
        return {
            i: ChunkText(
                id=i,
                user_id="matt",
                text="hello",
                parent_id=uuid4(),
                parent_title="Doc",
                created_at=datetime.now(UTC),
            )
            for i in ids
        }
    monkeypatch.setattr(hydrate_mod, "hydrate", _hydrate)

    hybrid_with_mocks["graph_store"].expand_chunks.return_value = ExpansionSubgraph(
        nodes={a: 0.5, b: 0.3, c: 0.2}, edges=[(a, b, 1.0), (a, c, 1.0)]
    )

    rr = FakeReranker(scores={a: 0.95, b: 0.7, c: 0.4})

    # Patch session_scope so the with-block is a no-op.
    from contextlib import asynccontextmanager
    @asynccontextmanager
    async def _fake_session(*_args, **_kwargs):
        yield AsyncMock()
    monkeypatch.setattr(
        "atlas_knowledge.retrieval.hybrid.hybrid.session_scope", _fake_session
    )

    retr = HybridRetriever(
        embedder=hybrid_with_mocks["embedder"],
        vector_store=hybrid_with_mocks["vector_store"],
        graph_store=hybrid_with_mocks["graph_store"],
        reranker=rr,
        session_factory=hybrid_with_mocks["session_factory"],
    )
    result = await retr.retrieve(
        RetrievalQuery(project_id=uuid4(), text="q", top_k=2)
    )
    assert result.degraded_stages == []
    assert len(result.chunks) == 2
    # `a` should rank first (highest rerank * (log + small) * ppr).
    assert result.chunks[0].chunk.id == a


@pytest.mark.asyncio
async def test_expansion_failure_degrades_gracefully(hybrid_with_mocks, monkeypatch):
    a = uuid4()
    hybrid_with_mocks["vector_store"].search.return_value = [_scored(a)]
    from atlas_knowledge.retrieval.hybrid import bm25 as bm25_mod
    from atlas_knowledge.retrieval.hybrid import hydrate as hydrate_mod

    async def _bm25(*a, **kw):
        return [(uuid4(), 1)]
    monkeypatch.setattr(bm25_mod, "search", _bm25)

    async def _hydrate(session, ids):
        from datetime import UTC, datetime
        return {
            i: ChunkText(
                id=i,
                user_id="matt",
                text="x",
                parent_id=uuid4(),
                parent_title=None,
                created_at=datetime.now(UTC),
            )
            for i in ids
        }
    monkeypatch.setattr(hydrate_mod, "hydrate", _hydrate)

    hybrid_with_mocks["graph_store"].expand_chunks.side_effect = RuntimeError("neo4j down")

    from contextlib import asynccontextmanager
    @asynccontextmanager
    async def _fake_session(*_args, **_kwargs):
        yield AsyncMock()
    monkeypatch.setattr(
        "atlas_knowledge.retrieval.hybrid.hybrid.session_scope", _fake_session
    )

    rr = FakeReranker(scores={})
    retr = HybridRetriever(
        embedder=hybrid_with_mocks["embedder"],
        vector_store=hybrid_with_mocks["vector_store"],
        graph_store=hybrid_with_mocks["graph_store"],
        reranker=rr,
        session_factory=hybrid_with_mocks["session_factory"],
    )
    result = await retr.retrieve(
        RetrievalQuery(project_id=uuid4(), text="q", top_k=2)
    )
    assert "expansion" in result.degraded_stages
    assert "personalized_pagerank" in result.degraded_stages  # PPR drops because subgraph empty
    assert len(result.chunks) >= 1


@pytest.mark.asyncio
async def test_both_bm25_and_vector_fail_raises(hybrid_with_mocks, monkeypatch):
    hybrid_with_mocks["vector_store"].search.side_effect = RuntimeError("chroma down")
    from atlas_knowledge.retrieval.hybrid import bm25 as bm25_mod

    async def _bm25(*a, **kw):
        raise RuntimeError("postgres down")
    monkeypatch.setattr(bm25_mod, "search", _bm25)

    from contextlib import asynccontextmanager
    @asynccontextmanager
    async def _fake_session(*_args, **_kwargs):
        yield AsyncMock()
    monkeypatch.setattr(
        "atlas_knowledge.retrieval.hybrid.hybrid.session_scope", _fake_session
    )

    rr = FakeReranker(scores={})
    retr = HybridRetriever(
        embedder=hybrid_with_mocks["embedder"],
        vector_store=hybrid_with_mocks["vector_store"],
        graph_store=hybrid_with_mocks["graph_store"],
        reranker=rr,
        session_factory=hybrid_with_mocks["session_factory"],
    )
    with pytest.raises(RuntimeError):
        await retr.retrieve(
            RetrievalQuery(project_id=uuid4(), text="q", top_k=2)
        )
```

- [ ] **Step 3: Run the test to verify it fails**

Run: `uv run pytest packages/atlas-knowledge/atlas_knowledge/tests/test_hybrid_orchestrator.py -v`
Expected: FAIL — `HybridRetriever` does not exist.

- [ ] **Step 4: Implement `HybridRetriever`**

Create `packages/atlas-knowledge/atlas_knowledge/retrieval/hybrid/hybrid.py`:

```python
"""HybridRetriever — orchestrates BM25 + vector + graph + rerank + PPR pipeline.

Per-stage failures are caught and recorded into ``RetrievalResult.degraded_stages``.
The only hard-fail conditions are: (a) both BM25 and vector return zero candidates
or both raise, and (b) hydration fails (no text to rerank or cite).
"""
from __future__ import annotations

import asyncio
import math
import time
from typing import Protocol
from uuid import UUID

import structlog
from atlas_core.db.session import session_scope
from atlas_graph.store import GraphStore
from sqlalchemy.ext.asyncio import async_sessionmaker, AsyncSession

from atlas_knowledge.embeddings.service import EmbeddingService
from atlas_knowledge.models.nodes import KnowledgeNode
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
                        type="chunk",
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
```

- [ ] **Step 5: Run the orchestrator unit test**

Run: `uv run pytest packages/atlas-knowledge/atlas_knowledge/tests/test_hybrid_orchestrator.py -v`
Expected: PASS for all three tests.

- [ ] **Step 6: Write the full-pipeline integration test**

Create `packages/atlas-knowledge/atlas_knowledge/tests/test_hybrid_pipeline_integration.py`:

```python
"""Full hybrid pipeline against real Postgres + Neo4j with FakeReranker."""
from __future__ import annotations

import os
from uuid import uuid4

import pytest
import pytest_asyncio
from neo4j import AsyncGraphDatabase
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from atlas_graph.store import GraphStore

pytestmark = pytest.mark.integration


def _enabled() -> bool:
    return (
        os.getenv("ATLAS_RUN_POSTGRES_INTEGRATION") == "1"
        and os.getenv("ATLAS_RUN_NEO4J_INTEGRATION") == "1"
    )


@pytest_asyncio.fixture
async def real_engine_and_factory():
    if not _enabled():
        pytest.skip("set both PG and Neo4j integration env vars to run")
    url = os.environ["ATLAS_DB__DATABASE_URL"].replace("postgresql://", "postgresql+asyncpg://")
    engine = create_async_engine(url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    yield engine, factory
    await engine.dispose()


@pytest_asyncio.fixture
async def real_graph():
    if not _enabled():
        pytest.skip("set both PG and Neo4j integration env vars to run")
    driver = AsyncGraphDatabase.driver(
        os.environ["ATLAS_GRAPH__URI"],
        auth=("neo4j", os.environ["ATLAS_GRAPH__PASSWORD"]),
    )
    store = GraphStore(driver)
    yield store, driver
    await driver.close()


@pytest.mark.asyncio
async def test_hybrid_happy_path(real_engine_and_factory, real_graph, monkeypatch):
    """Two chunks, both retrievable; assert hybrid returns both with no degradation."""
    from atlas_knowledge.embeddings.service import EmbeddingService
    from atlas_knowledge.models.retrieval import RetrievalQuery
    from atlas_knowledge.retrieval.hybrid.hybrid import HybridRetriever
    from atlas_knowledge.retrieval.hybrid.rerank import FakeReranker
    from atlas_knowledge.vector.store import VectorStore

    _engine, factory = real_engine_and_factory
    graph_store, driver = real_graph
    pid = uuid4()
    doc_id = uuid4()
    chunk_a = uuid4()
    chunk_b = uuid4()

    # Seed Postgres
    async with factory() as s:
        await s.execute(
            text(
                "INSERT INTO projects (id, user_id, name, status) "
                "VALUES (:id, 'matt', 'pipeline-test', 'active')"
            ),
            {"id": pid},
        )
        await s.execute(
            text(
                "INSERT INTO knowledge_nodes (id, user_id, project_id, type, title, text, metadata) "
                "VALUES (:id, 'matt', :pid, 'document', 'Doc', '', '{}'::jsonb)"
            ),
            {"id": doc_id, "pid": pid},
        )
        for cid, content in (
            (chunk_a, "geo lift methodology drives measurement"),
            (chunk_b, "incremental measurement on convenience-store accounts"),
        ):
            await s.execute(
                text(
                    "INSERT INTO knowledge_nodes "
                    "(id, user_id, project_id, type, parent_id, text, metadata) "
                    "VALUES (:id, 'matt', :pid, 'chunk', :doc, :text, '{}'::jsonb)"
                ),
                {"id": cid, "pid": pid, "doc": doc_id, "text": content},
            )
        await s.commit()

    # Seed Neo4j Chunks (no edges; expansion returns just seeds)
    async with driver.session() as ns:
        for cid in (chunk_a, chunk_b):
            await ns.run(
                "MERGE (c:Chunk {id: $id}) SET c.project_id = $pid, c.pagerank_global = 0.1",
                id=str(cid), pid=str(pid),
            )

    # Fake embedder: returns a deterministic vector (vector store will be the
    # real Chroma — but we don't have chunks indexed there, so vector returns
    # empty. Hybrid should still work via BM25 only and degrade vector gracefully).
    class _Embedder:
        async def embed_query(self, text):
            return [0.0] * 384

    class _EmptyVector:
        async def search(self, **kwargs):
            return []

    rr = FakeReranker(scores={chunk_a: 0.9, chunk_b: 0.4})
    retr = HybridRetriever(
        embedder=_Embedder(),  # type: ignore[arg-type]
        vector_store=_EmptyVector(),  # type: ignore[arg-type]
        graph_store=graph_store,
        reranker=rr,
        session_factory=factory,
    )

    try:
        result = await retr.retrieve(
            RetrievalQuery(project_id=pid, text="geo lift", top_k=5)
        )
        # BM25 returns chunk_a (matches "geo lift"); chunk_b doesn't match.
        # Vector returned empty (no embeddings indexed) so degrades gracefully.
        assert "vector" not in result.degraded_stages  # empty result is not an error
        ids = [c.chunk.id for c in result.chunks]
        assert chunk_a in ids
    finally:
        async with factory() as s:
            await s.execute(
                text("DELETE FROM knowledge_nodes WHERE project_id = :pid"),
                {"pid": pid},
            )
            await s.execute(
                text("DELETE FROM projects WHERE id = :pid"),
                {"pid": pid},
            )
            await s.commit()
        async with driver.session() as ns:
            await ns.run(
                "MATCH (n) WHERE n.project_id = $pid DETACH DELETE n", pid=str(pid)
            )
```

- [ ] **Step 7: Run the integration test**

```bash
ATLAS_RUN_POSTGRES_INTEGRATION=1 \
ATLAS_RUN_NEO4J_INTEGRATION=1 \
ATLAS_GRAPH__URI=bolt://localhost:7687 \
ATLAS_GRAPH__PASSWORD=$(grep ATLAS_GRAPH__PASSWORD .env | cut -d= -f2) \
ATLAS_DB__DATABASE_URL=$(grep ATLAS_DB__DATABASE_URL .env | cut -d= -f2-) \
uv run pytest packages/atlas-knowledge/atlas_knowledge/tests/test_hybrid_pipeline_integration.py -v
```

Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add packages/atlas-knowledge/atlas_knowledge/retrieval/hybrid/expansion.py \
        packages/atlas-knowledge/atlas_knowledge/retrieval/hybrid/hybrid.py \
        packages/atlas-knowledge/atlas_knowledge/tests/test_hybrid_orchestrator.py \
        packages/atlas-knowledge/atlas_knowledge/tests/test_hybrid_pipeline_integration.py
git commit -m "feat(retrieval/hybrid): HybridRetriever orchestrator with graceful degradation"
```

---

## Task 10: API wiring — `RetrieverProtocol`, lifespan, mode flag

Define the abstract retriever protocol, branch on `ATLAS_RETRIEVAL__MODE` in the lifespan, construct `Reranker` once and inject into `HybridRetriever`. WS chat tests parametrize over both modes.

**Files:**
- Modify: `packages/atlas-knowledge/atlas_knowledge/retrieval/__init__.py`
- Modify: `apps/api/atlas_api/main.py`
- Modify: `apps/api/atlas_api/deps.py`
- Modify: `apps/api/atlas_api/tests/test_ws_chat_rag.py`

- [ ] **Step 1: Add `RetrieverProtocol` to atlas-knowledge**

Edit `packages/atlas-knowledge/atlas_knowledge/retrieval/__init__.py`:

```python
"""Retrieval package — vector (Phase 1) and hybrid (Plan 4) retrievers."""

from typing import Protocol

from atlas_knowledge.models.retrieval import RetrievalQuery, RetrievalResult
from atlas_knowledge.retrieval.builder import build_rag_context
from atlas_knowledge.retrieval.retriever import Retriever


class RetrieverProtocol(Protocol):
    async def retrieve(self, query: RetrievalQuery) -> RetrievalResult: ...


__all__ = ["Retriever", "RetrieverProtocol", "build_rag_context"]
```

- [ ] **Step 2: Update `deps.py` to use the protocol**

Edit `apps/api/atlas_api/deps.py`:

Replace:

```python
from atlas_knowledge.retrieval.retriever import Retriever
```

with:

```python
from atlas_knowledge.retrieval import RetrieverProtocol
```

And:

```python
def get_retriever(connection: HTTPConnection) -> Retriever:
    return connection.app.state.retriever
```

with:

```python
def get_retriever(connection: HTTPConnection) -> RetrieverProtocol:
    return connection.app.state.retriever
```

- [ ] **Step 3: Wire `HybridRetriever` into the lifespan**

Edit `apps/api/atlas_api/main.py`. Add the imports near the top (next to existing imports):

```python
from atlas_knowledge.retrieval.hybrid.hybrid import HybridRetriever
from atlas_knowledge.retrieval.hybrid.rerank import Reranker
```

Replace the line:

```python
    app.state.retriever = Retriever(embedder=embedder, vector_store=vector_store)
```

with:

```python
    if config.retrieval.mode == "hybrid":
        reranker = Reranker(model_name=config.retrieval.reranker_model)
        app.state.reranker = reranker
        app.state.retriever = HybridRetriever(
            embedder=embedder,
            vector_store=vector_store,
            graph_store=graph_store,
            reranker=reranker,
            session_factory=app.state.session_factory,
        )
        log.info("retriever.mode", mode="hybrid")
    else:
        app.state.retriever = Retriever(embedder=embedder, vector_store=vector_store)
        log.info("retriever.mode", mode="vector")
```

- [ ] **Step 4: Add a hybrid-mode WS test alongside the existing vector-mode tests**

The existing `apps/api/atlas_api/tests/test_ws_chat_rag.py` builds a `Retriever` and injects it via `set_overrides`. We add one parallel test that wires a `HybridRetriever` (with a stub graph_store and a `FakeReranker`) through the same WS flow and asserts the same `rag.context` event behavior. Append to the file:

```python
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock

from atlas_graph.expansion import ExpansionSubgraph
from atlas_knowledge.retrieval.hybrid.hybrid import HybridRetriever
from atlas_knowledge.retrieval.hybrid.rerank import FakeReranker


@pytest_asyncio.fixture
async def hybrid_retriever(vector_store, fake_embedder, db_session):
    """HybridRetriever that reuses the test's FakeEmbedder + tmp Chroma + db_session.

    Uses a stub GraphStore returning an empty ExpansionSubgraph so the pipeline
    runs without Neo4j. Expansion and PPR will degrade gracefully — the WS-level
    behavior (rag.context event + citations) should match the vector path.
    """
    graph_store = AsyncMock()

    async def _expand(*, project_id, seeds, cap):
        return ExpansionSubgraph(nodes={s: 0.0 for s in seeds}, edges=[])

    graph_store.expand_chunks.side_effect = _expand

    @asynccontextmanager
    async def _session_cm():
        yield db_session

    class _SessionFactory:
        def __call__(self):
            return _session_cm()

    return HybridRetriever(
        embedder=fake_embedder,
        vector_store=vector_store,
        graph_store=graph_store,
        reranker=FakeReranker(scores={}),
        session_factory=_SessionFactory(),  # type: ignore[arg-type]
    )


@pytest_asyncio.fixture
async def set_overrides_hybrid(db_session, fake_router, fake_settings, hybrid_retriever):
    """Same as set_overrides but injects HybridRetriever instead of Retriever."""

    async def _override_session():
        yield db_session

    app.dependency_overrides[get_session] = _override_session
    app.dependency_overrides[get_model_router] = lambda: fake_router
    app.dependency_overrides[get_settings] = lambda: fake_settings
    app.dependency_overrides[get_retriever] = lambda: hybrid_retriever
    yield
    for dep in (get_session, get_model_router, get_settings, get_retriever):
        app.dependency_overrides.pop(dep, None)


@pytest.mark.asyncio
async def test_rag_happy_path_works_under_hybrid_mode(
    set_overrides_hybrid, db_session, vector_store, fake_embedder
):
    """Same scenario as test_rag_happy_path_emits_event_and_persists_citations,
    but with HybridRetriever. Asserts the rag.context event still arrives with
    citations sourced from the hybrid pipeline (BM25 + vector + degraded graph)."""
    project_id = uuid4()
    chunks = await _seed_project_and_chunks(
        db_session,
        vector_store,
        fake_embedder,
        project_id,
        texts=["alpha context body", "beta context body"],
    )
    chunk_ids = {str(c.id) for c in chunks}

    session_id = uuid4()
    async with _ws_client() as http, aconnect_ws(f"/api/v1/ws/{session_id}", http) as ws:
        await ws.send_json(
            {
                "type": "chat.message",
                "payload": {
                    "text": "alpha",
                    "project_id": str(project_id),
                    "rag_enabled": True,
                    "top_k_context": 5,
                },
            }
        )
        events = await _drain_events_until_done(ws)

    types = [e["type"] for e in events]
    assert "rag.context" in types
    rag_evt = events[types.index("rag.context")]
    citations = rag_evt["payload"]["citations"]
    assert len(citations) >= 1
    for cite in citations:
        assert cite["chunk_id"] in chunk_ids
```

- [ ] **Step 5: Run the API test suite**

```bash
uv run pytest apps/api/atlas_api/tests/ -v
```

Expected: all existing tests pass; the new parametrized test passes for both modes.

- [ ] **Step 6: Smoke test the lifespan in dev**

```bash
cd infra && docker compose up -d postgres neo4j chroma && cd ..
uv run alembic upgrade head
ATLAS_RETRIEVAL__MODE=hybrid uv run uvicorn atlas_api.main:app --reload --port 8000
```

Expected: log line `retriever.mode mode=hybrid` and the API starts. Hit `/health` to confirm.

Stop the server. Restart with `ATLAS_RETRIEVAL__MODE=vector` and confirm the log line shows `mode=vector`.

- [ ] **Step 7: Commit**

```bash
git add packages/atlas-knowledge/atlas_knowledge/retrieval/__init__.py \
        apps/api/atlas_api/main.py \
        apps/api/atlas_api/deps.py \
        apps/api/atlas_api/tests/test_ws_chat_rag.py
git commit -m "feat(api): wire HybridRetriever into lifespan; ATLAS_RETRIEVAL__MODE flag"
```

---

## Task 11: Acceptance test — indirectly-connected concept

The Plan-4 spec's Definition of Done: a query whose answer chunk shares no surface keywords with the query but is connected through an Entity should be retrieved by hybrid and missed by vector-only.

**Files:**
- Test: `packages/atlas-knowledge/atlas_knowledge/tests/test_hybrid_indirect_concept.py`

- [ ] **Step 1: Write the acceptance test**

Create `packages/atlas-knowledge/atlas_knowledge/tests/test_hybrid_indirect_concept.py`:

```python
"""Plan 4 Definition-of-Done: hybrid finds an indirectly-connected chunk that vector misses.

Two chunks share an Entity (e.g., 'CircleK') but no surface keywords. A query
that names the Entity must surface both chunks under hybrid, only one under
vector-only.
"""
from __future__ import annotations

import os
from uuid import uuid4

import pytest
import pytest_asyncio
from neo4j import AsyncGraphDatabase
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from atlas_graph.store import GraphStore

pytestmark = pytest.mark.integration


def _enabled() -> bool:
    return (
        os.getenv("ATLAS_RUN_POSTGRES_INTEGRATION") == "1"
        and os.getenv("ATLAS_RUN_NEO4J_INTEGRATION") == "1"
    )


@pytest_asyncio.fixture
async def stack():
    if not _enabled():
        pytest.skip("set both integration env vars to run")
    pg_url = os.environ["ATLAS_DB__DATABASE_URL"].replace("postgresql://", "postgresql+asyncpg://")
    engine = create_async_engine(pg_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    driver = AsyncGraphDatabase.driver(
        os.environ["ATLAS_GRAPH__URI"],
        auth=("neo4j", os.environ["ATLAS_GRAPH__PASSWORD"]),
    )
    yield engine, factory, driver
    await engine.dispose()
    await driver.close()


@pytest.mark.asyncio
async def test_indirect_concept_recovered_by_hybrid_only(stack):
    from atlas_knowledge.models.retrieval import RetrievalQuery
    from atlas_knowledge.retrieval.hybrid.hybrid import HybridRetriever
    from atlas_knowledge.retrieval.hybrid.rerank import FakeReranker

    _engine, factory, driver = stack
    pid = uuid4()
    doc_id = uuid4()
    chunk_query_match = uuid4()  # Mentions CircleK by name -> matches both keyword and entity
    chunk_indirect = uuid4()      # Same Entity, no keyword overlap with query
    eid = uuid4()

    async with factory() as s:
        await s.execute(
            text(
                "INSERT INTO projects (id, user_id, name, status) "
                "VALUES (:id, 'matt', 'circlek', 'active')"
            ),
            {"id": pid},
        )
        await s.execute(
            text(
                "INSERT INTO knowledge_nodes (id, user_id, project_id, type, title, text, metadata) "
                "VALUES (:id, 'matt', :pid, 'document', 'Doc', '', '{}'::jsonb)"
            ),
            {"id": doc_id, "pid": pid},
        )
        await s.execute(
            text(
                "INSERT INTO knowledge_nodes (id, user_id, project_id, type, parent_id, text, metadata) "
                "VALUES (:id, 'matt', :pid, 'chunk', :doc, "
                ":text, '{}'::jsonb)"
            ),
            {
                "id": chunk_query_match, "pid": pid, "doc": doc_id,
                "text": "CircleK proposal scoping notes",
            },
        )
        await s.execute(
            text(
                "INSERT INTO knowledge_nodes (id, user_id, project_id, type, parent_id, text, metadata) "
                "VALUES (:id, 'matt', :pid, 'chunk', :doc, :text, '{}'::jsonb)"
            ),
            {
                "id": chunk_indirect, "pid": pid, "doc": doc_id,
                "text": "convenience store geo-lift methodology summary",
            },
        )
        await s.commit()

    async with driver.session() as ns:
        for cid in (chunk_query_match, chunk_indirect):
            await ns.run(
                "MERGE (c:Chunk {id: $id}) SET c.project_id = $pid, c.pagerank_global = 0.1",
                id=str(cid), pid=str(pid),
            )
        await ns.run(
            "MERGE (e:Entity {project_id: $pid, name: 'circlek', type: 'CLIENT'}) "
            "SET e.id = $eid",
            pid=str(pid), eid=str(eid),
        )
        for cid in (chunk_query_match, chunk_indirect):
            await ns.run(
                "MATCH (c:Chunk {id: $c}), (e:Entity {id: $eid}) "
                "MERGE (c)-[:REFERENCES]->(e)",
                c=str(cid), eid=str(eid),
            )

    class _Embedder:
        async def embed_query(self, t):
            return [0.0] * 384

    class _EmptyVector:
        async def search(self, **kw):
            return []

    rr = FakeReranker(scores={chunk_query_match: 0.9, chunk_indirect: 0.7})
    retr = HybridRetriever(
        embedder=_Embedder(),  # type: ignore[arg-type]
        vector_store=_EmptyVector(),  # type: ignore[arg-type]
        graph_store=GraphStore(driver),
        reranker=rr,
        session_factory=factory,
    )

    try:
        result = await retr.retrieve(
            RetrievalQuery(project_id=pid, text="CircleK", top_k=5)
        )
        ids = [c.chunk.id for c in result.chunks]
        # Hybrid retrieves both: BM25 finds the keyword match; expansion via
        # the shared Entity surfaces the indirect chunk.
        assert chunk_query_match in ids
        assert chunk_indirect in ids
    finally:
        async with factory() as s:
            await s.execute(
                text("DELETE FROM knowledge_nodes WHERE project_id = :pid"),
                {"pid": pid},
            )
            await s.execute(
                text("DELETE FROM projects WHERE id = :pid"),
                {"pid": pid},
            )
            await s.commit()
        async with driver.session() as ns:
            await ns.run(
                "MATCH (n) WHERE n.project_id = $pid DETACH DELETE n", pid=str(pid)
            )
```

- [ ] **Step 2: Run the test**

```bash
ATLAS_RUN_POSTGRES_INTEGRATION=1 \
ATLAS_RUN_NEO4J_INTEGRATION=1 \
ATLAS_GRAPH__URI=bolt://localhost:7687 \
ATLAS_GRAPH__PASSWORD=$(grep ATLAS_GRAPH__PASSWORD .env | cut -d= -f2) \
ATLAS_DB__DATABASE_URL=$(grep ATLAS_DB__DATABASE_URL .env | cut -d= -f2-) \
uv run pytest packages/atlas-knowledge/atlas_knowledge/tests/test_hybrid_indirect_concept.py -v
```

Expected: PASS — both `chunk_query_match` and `chunk_indirect` appear in `ids`.

- [ ] **Step 3: Commit**

```bash
git add packages/atlas-knowledge/atlas_knowledge/tests/test_hybrid_indirect_concept.py
git commit -m "test(retrieval/hybrid): Plan 4 acceptance — indirect concept recovered via graph"
```

---

## Task 12: Reranker smoke test (slow, opt-in)

Loads the actual `ms-marco-MiniLM-L-6-v2` model, reranks 10 candidates, verifies the model name resolves on HF Hub. Skipped by default; runs only when `ATLAS_RUN_SLOW_TESTS=1`.

**Files:**
- Test: `packages/atlas-knowledge/atlas_knowledge/tests/test_hybrid_rerank_real.py`

- [ ] **Step 1: Write the test**

Create `packages/atlas-knowledge/atlas_knowledge/tests/test_hybrid_rerank_real.py`:

```python
"""Slow smoke test: actually load and run the cross-encoder."""
from __future__ import annotations

import os
from uuid import uuid4

import pytest

pytestmark = pytest.mark.slow


def _enabled() -> bool:
    return os.getenv("ATLAS_RUN_SLOW_TESTS") == "1"


@pytest.mark.asyncio
async def test_real_reranker_orders_candidates():
    if not _enabled():
        pytest.skip("set ATLAS_RUN_SLOW_TESTS=1 to enable")
    from atlas_knowledge.retrieval.hybrid.rerank import Reranker

    rr = Reranker()  # downloads model on first call
    a, b, c = uuid4(), uuid4(), uuid4()
    candidates = [
        (a, "geo lift methodology measures incremental ad effects via geographic experiments"),
        (b, "we picked up coffee on the way to the meeting"),
        (c, "incremental measurement and geo-experiments are core to lift testing"),
    ]
    out = await rr.rerank("how do you measure geo lift", candidates, top_k=3)
    assert len(out) == 3
    # Topical chunks (a, c) should outrank coffee (b).
    ranked_ids = [t[0] for t in out]
    assert ranked_ids[-1] == b


@pytest.mark.asyncio
async def test_real_reranker_handles_long_input():
    """Sanity-check that a near-512-token input doesn't crash."""
    if not _enabled():
        pytest.skip("set ATLAS_RUN_SLOW_TESTS=1 to enable")
    from atlas_knowledge.retrieval.hybrid.rerank import Reranker

    rr = Reranker()
    long_text = ("token " * 600).strip()  # well past the 512-token cap
    a = uuid4()
    out = await rr.rerank("query", [(a, long_text)], top_k=1)
    assert len(out) == 1
    assert out[0][0] == a
```

- [ ] **Step 2: Run the test**

```bash
ATLAS_RUN_SLOW_TESTS=1 uv run pytest packages/atlas-knowledge/atlas_knowledge/tests/test_hybrid_rerank_real.py -v
```

Expected: PASS. First run downloads ~23 MB.

- [ ] **Step 3: Commit**

```bash
git add packages/atlas-knowledge/atlas_knowledge/tests/test_hybrid_rerank_real.py
git commit -m "test(retrieval/hybrid): real cross-encoder smoke test (opt-in slow)"
```

---

## Definition of Done

After all tasks complete:

1. `uv run alembic upgrade head` runs cleanly; `\d knowledge_nodes` shows the `fts` generated column and the partial GIN index.
2. `uv run pytest packages/ apps/` (unit only — no integration env vars set) passes.
3. With `ATLAS_RUN_POSTGRES_INTEGRATION=1`, all Postgres-flavored tests pass.
4. With `ATLAS_RUN_NEO4J_INTEGRATION=1`, all Neo4j-flavored tests pass.
5. With both, the full pipeline integration test and the indirect-concept acceptance test both pass.
6. `ATLAS_RETRIEVAL__MODE=hybrid uvicorn ...` boots and logs `retriever.mode mode=hybrid`.
7. `ATLAS_RETRIEVAL__MODE=vector uvicorn ...` boots and logs `retriever.mode mode=vector`; chat WS works against Phase 1 retriever unchanged.
8. The reranker smoke test passes when `ATLAS_RUN_SLOW_TESTS=1`.
9. All Phase 1 + Phase 2 Plan 1-3 tests still pass — no regressions.
