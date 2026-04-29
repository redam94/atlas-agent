# ATLAS Phase 2 — Plan 6: Note Editor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship `/projects/:id/notes` — a TipTap-based note editor where notes are first-class graph nodes (chunked, embedded, indexed), with explicit `@`-mentions creating distinct `TAGGED_WITH` edges to entities.

**Architecture:** Notes reuse `knowledge_nodes` (`type='note'`) and `(:Document)` Neo4j nodes — zero changes to chunker/embedder/Plan 4 retriever/Plan 5 explorer. A thin `notes` Postgres table holds editor metadata (`body_markdown`, `mention_entity_ids`, `indexed_at`). Two-state save UX: cheap PATCH (debounced 2s, Postgres only) and explicit Save & Index (full pipeline).

**Tech Stack:** FastAPI + SQLAlchemy + Alembic (existing), `atlas-graph` (existing), `atlas-knowledge.IngestionService` (existing), React 19 + Vite + Tailwind + Radix + Zustand + React Query (existing), TipTap + tippy.js + turndown + marked (new).

**Spec:** `docs/superpowers/specs/2026-04-29-atlas-phase-2-plan-6-note-editor-design.md`

---

## File Map

**Backend (create):**
- `infra/alembic/versions/0006_create_notes_table.py`
- `packages/atlas-core/atlas_core/models/notes.py`
- `apps/api/atlas_api/routers/notes.py`
- `apps/api/atlas_api/tests/test_notes_router.py`
- `apps/api/atlas_api/tests/test_knowledge_entities_endpoint.py`
- `packages/atlas-graph/atlas_graph/tests/test_tag_note.py`
- `packages/atlas-graph/atlas_graph/tests/test_list_entities.py`

**Backend (modify):**
- `packages/atlas-core/atlas_core/db/orm.py` — add `NoteORM`.
- `packages/atlas-knowledge/atlas_knowledge/models/nodes.py` — add `KnowledgeNodeType.NOTE`.
- `packages/atlas-knowledge/atlas_knowledge/ingestion/service.py` — add `IngestionResult` dataclass, return it from `ingest`, add `cleanup_document` unified helper.
- `apps/api/atlas_api/routers/knowledge.py` — update existing ingest handlers for new return shape; add `/knowledge/entities` endpoint; rewrite `delete_node` to use the new `cleanup_document` helper.
- `packages/atlas-graph/atlas_graph/store.py` — add `tag_note`, `list_entities` methods + Cypher constants.
- `apps/api/atlas_api/main.py` — register notes router.
- `packages/atlas-graph/atlas_graph/tests/test_subgraph_integration.py` — add note-acceptance test.

**Frontend (create):**
- `apps/web/src/lib/api/notes.ts`
- `apps/web/src/lib/api/entities.ts`
- `apps/web/src/stores/notes-store.ts` + `.test.ts`
- `apps/web/src/components/notes/use-notes.ts`
- `apps/web/src/components/notes/note-empty.tsx`
- `apps/web/src/components/notes/note-list-item.tsx`
- `apps/web/src/components/notes/note-list-rail.tsx` + `.test.tsx`
- `apps/web/src/components/notes/note-mention-extension.ts` + `.test.ts`
- `apps/web/src/components/notes/note-mention-list.tsx`
- `apps/web/src/components/notes/note-editor.tsx` + `.test.tsx`
- `apps/web/src/routes/notes.tsx`

**Frontend (modify):**
- `apps/web/package.json` — add TipTap + helpers.
- `apps/web/src/main.tsx` — nested notes routes.
- `apps/web/src/components/sidebar/project-tabs.tsx` — add Notes tab.

---

## Phase A — Backend

### Task 1: Migration 0006 — notes table

**Files:**
- Create: `infra/alembic/versions/0006_create_notes_table.py`

- [ ] **Step 1: Write the migration**

```python
"""create notes table

Revision ID: 0006
Revises: 0005
Create Date: 2026-04-29
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "notes",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("user_id", sa.Text(), nullable=False),
        sa.Column(
            "project_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "knowledge_node_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("knowledge_nodes.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("title", sa.Text(), nullable=False, server_default="Untitled"),
        sa.Column("body_markdown", sa.Text(), nullable=False, server_default=""),
        sa.Column(
            "mention_entity_ids",
            postgresql.ARRAY(postgresql.UUID(as_uuid=True)),
            nullable=False,
            server_default="{}",
        ),
        sa.Column("indexed_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index(
        "notes_project_id_updated_at",
        "notes",
        ["project_id", sa.text("updated_at DESC")],
    )


def downgrade() -> None:
    op.drop_index("notes_project_id_updated_at", table_name="notes")
    op.drop_table("notes")
```

- [ ] **Step 2: Apply locally and confirm shape**

```bash
uv run alembic upgrade head
docker exec atlas-postgres psql -U atlas -d atlas -c "\d notes"
```

Expected: table `notes` exists with the listed columns; index `notes_project_id_updated_at` present.

- [ ] **Step 3: Commit**

```bash
git add infra/alembic/versions/0006_create_notes_table.py
git commit -m "feat(db): add notes table (Plan 6)"
```

---

### Task 2: NoteORM + Pydantic models + KnowledgeNodeType.NOTE

**Files:**
- Modify: `packages/atlas-core/atlas_core/db/orm.py`
- Modify: `packages/atlas-knowledge/atlas_knowledge/models/nodes.py`
- Create: `packages/atlas-core/atlas_core/models/notes.py`

- [ ] **Step 1: Extend `KnowledgeNodeType`**

In `packages/atlas-knowledge/atlas_knowledge/models/nodes.py`, locate the `KnowledgeNodeType` enum and add:

```python
class KnowledgeNodeType(StrEnum):
    DOCUMENT = "document"
    CHUNK = "chunk"
    NOTE = "note"
```

- [ ] **Step 2: Add `NoteORM`**

Append to `packages/atlas-core/atlas_core/db/orm.py` (after the last existing ORM class):

```python
class NoteORM(Base):
    """Maps to the `notes` table — editor metadata for user notes (Plan 6)."""

    __tablename__ = "notes"

    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
        server_default=func.gen_random_uuid(),
    )
    user_id: Mapped[str] = mapped_column(Text, nullable=False)
    project_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
    )
    knowledge_node_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("knowledge_nodes.id", ondelete="SET NULL"),
        nullable=True,
    )
    title: Mapped[str] = mapped_column(Text, nullable=False, server_default="Untitled")
    body_markdown: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    mention_entity_ids: Mapped[list[UUID]] = mapped_column(
        ARRAY(PGUUID(as_uuid=True)),
        nullable=False,
        server_default="{}",
    )
    indexed_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
```

Ensure `from sqlalchemy.dialects.postgresql import ARRAY` is in the imports at the top of the file (alongside the existing `JSONB`, `UUID as PGUUID` imports).

- [ ] **Step 3: Add Pydantic models**

Create `packages/atlas-core/atlas_core/models/notes.py`:

```python
"""Pydantic models for the notes API (Plan 6)."""
from __future__ import annotations

from datetime import datetime
from uuid import UUID

from atlas_core.models.base import AtlasModel
from pydantic import Field


class Note(AtlasModel):
    """Full note row returned by GET / POST / PATCH / index endpoints."""
    id: UUID
    user_id: str
    project_id: UUID
    knowledge_node_id: UUID | None = None
    title: str
    body_markdown: str
    mention_entity_ids: list[UUID] = Field(default_factory=list)
    indexed_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class NoteListItem(AtlasModel):
    """Row in GET /api/v1/notes list response — light fields only."""
    id: UUID
    title: str
    updated_at: datetime
    indexed_at: datetime | None = None


class CreateNoteRequest(AtlasModel):
    project_id: UUID
    title: str = "Untitled"
    body_markdown: str = ""


class PatchNoteRequest(AtlasModel):
    title: str | None = None
    body_markdown: str | None = None
    mention_entity_ids: list[UUID] | None = None
```

- [ ] **Step 4: Smoke import**

```bash
uv run python -c "from atlas_core.db.orm import NoteORM; from atlas_core.models.notes import Note, NoteListItem, CreateNoteRequest, PatchNoteRequest; from atlas_knowledge.models.nodes import KnowledgeNodeType; assert KnowledgeNodeType.NOTE.value == 'note'; print('ok')"
```

Expected: `ok`.

- [ ] **Step 5: Run all backend tests to confirm no regressions**

```bash
uv run pytest apps/api packages -q 2>&1 | tail -3
```

Expected: all previously-passing tests still pass.

- [ ] **Step 6: Commit**

```bash
git add packages/atlas-core/atlas_core/db/orm.py packages/atlas-core/atlas_core/models/notes.py packages/atlas-knowledge/atlas_knowledge/models/nodes.py
git commit -m "feat(core/notes): NoteORM + Pydantic models + KnowledgeNodeType.NOTE"
```

---

### Task 3: IngestionResult dataclass + update ingest signature

**Files:**
- Modify: `packages/atlas-knowledge/atlas_knowledge/ingestion/service.py`
- Modify: `apps/api/atlas_api/routers/knowledge.py`

- [ ] **Step 1: Add `IngestionResult` dataclass**

Add to the top of `packages/atlas-knowledge/atlas_knowledge/ingestion/service.py` (after the existing imports, before the `IngestionService` class):

```python
from dataclasses import dataclass


@dataclass(frozen=True)
class IngestionResult:
    """What `IngestionService.ingest` returns.

    `document_id` is None only on the empty-text path where no Document row is
    created (extremely rare; happens when the parser returns an empty body).
    """
    job_id: UUID
    document_id: UUID | None
```

- [ ] **Step 2: Update `ingest` to return `IngestionResult`**

Locate the three `return job.id` statements in `IngestionService.ingest` and update each. Search for `return job.id` and replace with `return IngestionResult(job_id=job.id, document_id=doc_row.id if doc_row is not None else None)`.

(There are three return points: empty-chunks early return, success path, and the except path. All three need the update; the except path's `doc_row` is bound earlier so the conditional handles cases where the exception fires before doc_row is assigned.)

Update the function's return type annotation:

```python
async def ingest(
    self,
    *,
    db: AsyncSession,
    user_id: str,
    project_id: UUID,
    parsed: ParsedDocument,
    source_type: str,
    source_filename: str | None,
) -> IngestionResult:
```

- [ ] **Step 3: Update existing callers**

In `apps/api/atlas_api/routers/knowledge.py`, find each call site of `service.ingest(...)` (there are three: markdown ingest, PDF ingest, URL ingest). Each currently does:

```python
job_id = await service.ingest(...)
job_row = await db.get(IngestionJobORM, job_id)
```

Update each to:

```python
result = await service.ingest(...)
job_row = await db.get(IngestionJobORM, result.job_id)
```

- [ ] **Step 4: Run the full test suite**

```bash
uv run pytest apps/api packages -q 2>&1 | tail -5
```

Expected: all tests pass. The previously-passing ingestion tests will exercise the new return shape via the existing callers.

- [ ] **Step 5: Commit**

```bash
git add packages/atlas-knowledge/atlas_knowledge/ingestion/service.py apps/api/atlas_api/routers/knowledge.py
git commit -m "refactor(knowledge/ingest): IngestionService.ingest returns IngestionResult"
```

---

### Task 4: IngestionService.cleanup_document unified helper

**Files:**
- Modify: `packages/atlas-knowledge/atlas_knowledge/ingestion/service.py`
- Modify: `apps/api/atlas_api/routers/knowledge.py` — rewrite the existing `delete_node` to use the new helper.

- [ ] **Step 1: Add `IngestionService.cleanup_document`**

In `packages/atlas-knowledge/atlas_knowledge/ingestion/service.py`, append to the `IngestionService` class:

```python
async def cleanup_document(
    self,
    *,
    db: AsyncSession,
    project_id: UUID,
    document_id: UUID,
) -> None:
    """Cascade delete a Document across Postgres + Chroma + Neo4j.

    - Postgres: deletes the parent KnowledgeNodeORM; chunks cascade via
      parent_id FK ON DELETE CASCADE.
    - Chroma: deletes by metadata filter project_id + parent_id.
    - Neo4j: delegates to GraphStore.cleanup_document if a graph writer
      is configured; no-op otherwise.
    """
    # Chroma — delete chunk vectors before we lose the parent_id FK.
    self._vector_store.delete_by_parent(project_id=project_id, parent_id=document_id)

    # Postgres — cascade deletes chunks via parent_id FK.
    doc_row = await db.get(KnowledgeNodeORM, document_id)
    if doc_row is not None:
        await db.delete(doc_row)
        await db.flush()

    # Neo4j — delegate.
    if self._graph_writer is not None:
        await self._graph_writer.cleanup_document(
            project_id=project_id, document_id=document_id
        )
```

Add the import at the top of the file if not already present:

```python
from atlas_core.db.orm import KnowledgeNodeORM, IngestionJobORM, ProjectORM
```

(`ProjectORM` and `IngestionJobORM` are already imported; this adds `KnowledgeNodeORM` if it's missing.)

- [ ] **Step 2: Add `delete_by_parent` to the vector store interface**

In `packages/atlas-knowledge/atlas_knowledge/vector/chroma.py`, append to the `ChromaVectorStore` class:

```python
def delete_by_parent(self, *, project_id: UUID, parent_id: UUID) -> None:
    """Delete all chunk vectors whose metadata.parent_id matches."""
    self._collection.delete(
        where={"$and": [
            {"project_id": str(project_id)},
            {"parent_id": str(parent_id)},
        ]}
    )
```

If the existing protocol file (search for `class VectorStoreProtocol` if it exists; otherwise the duck-type is fine) has a typed protocol, add the method signature there as well.

- [ ] **Step 3: Rewrite `/knowledge/nodes/{id}` DELETE to use the helper**

Replace the existing `delete_node` handler in `apps/api/atlas_api/routers/knowledge.py`:

```python
@router.delete("/knowledge/nodes/{node_id}", status_code=204)
async def delete_node(
    node_id: UUID,
    db: AsyncSession = Depends(get_session),
    service: IngestionService = Depends(get_ingestion_service),
) -> None:
    row = await db.get(KnowledgeNodeORM, node_id)
    if row is None:
        raise HTTPException(status_code=404, detail="node not found")
    if row.type == "document":
        await service.cleanup_document(
            db=db, project_id=row.project_id, document_id=row.id
        )
    else:
        await db.delete(row)
        await db.flush()
```

Documents now properly cascade across all three stores; chunks (deleted directly) only remove the Postgres row, matching prior behavior.

- [ ] **Step 4: Run the full test suite**

```bash
uv run pytest apps/api packages -q 2>&1 | tail -5
```

Expected: all tests pass. The existing `test_delete_unknown_node_returns_404` still passes; if any test exercises the document-delete path, it now goes through the helper.

- [ ] **Step 5: Commit**

```bash
git add packages/atlas-knowledge/atlas_knowledge/ingestion/service.py packages/atlas-knowledge/atlas_knowledge/vector/chroma.py apps/api/atlas_api/routers/knowledge.py
git commit -m "feat(knowledge/ingest): cleanup_document unified helper across PG/Chroma/Neo4j"
```

---

### Task 5: GraphStore.tag_note

**Files:**
- Modify: `packages/atlas-graph/atlas_graph/store.py`
- Create: `packages/atlas-graph/atlas_graph/tests/test_tag_note.py`

- [ ] **Step 1: Write the failing tests**

Create `packages/atlas-graph/atlas_graph/tests/test_tag_note.py`:

```python
"""Cypher-shape tests for GraphStore.tag_note."""
from __future__ import annotations

from uuid import uuid4

import pytest

from atlas_graph.store import GraphStore


@pytest.mark.asyncio
async def test_tag_note_runs_one_write_with_note_id_and_entity_ids(fake_async_driver):
    store = GraphStore(fake_async_driver)  # type: ignore[arg-type]
    note_id = uuid4()
    entity_ids = [uuid4(), uuid4()]

    await store.tag_note(note_id=note_id, entity_ids=entity_ids)

    assert any(c.kwargs.get("note_id") == str(note_id) for c in fake_async_driver.calls)
    assert any(
        c.kwargs.get("entity_ids") == [str(e) for e in entity_ids]
        for c in fake_async_driver.calls
    )
    assert any("TAGGED_WITH" in c.query for c in fake_async_driver.calls)


@pytest.mark.asyncio
async def test_tag_note_empty_entity_ids_short_circuits(fake_async_driver):
    store = GraphStore(fake_async_driver)  # type: ignore[arg-type]
    await store.tag_note(note_id=uuid4(), entity_ids=[])
    assert fake_async_driver.calls == []
```

- [ ] **Step 2: Run the tests (fail)**

```bash
uv run pytest packages/atlas-graph/atlas_graph/tests/test_tag_note.py -v
```

Expected: both fail with `AttributeError: 'GraphStore' object has no attribute 'tag_note'`.

- [ ] **Step 3: Add Cypher constant + method**

In `packages/atlas-graph/atlas_graph/store.py`, add this Cypher constant near the other Plan 5/6 constants:

```python
# Plan 6 — explicit @-mention edges from a note's (:Document) to (:Entity) nodes.
TAG_NOTE_CYPHER = """
UNWIND $entity_ids AS eid
MATCH (n:Document {id: $note_id}), (e:Entity {id: eid})
MERGE (n)-[:TAGGED_WITH]->(e)
"""
```

Add this method to the `GraphStore` class (place it after `fetch_subgraph_by_seeds`):

```python
async def tag_note(
    self,
    *,
    note_id: UUID,
    entity_ids: list[UUID],
) -> None:
    """Create (:Document {id:note_id})-[:TAGGED_WITH]->(:Entity {id:eid}) edges.

    Idempotent via MERGE; safe to re-call on every Save & Index.
    """
    if not entity_ids:
        return

    async def _do(tx: AsyncTransaction) -> None:
        await tx.run(
            TAG_NOTE_CYPHER,
            note_id=str(note_id),
            entity_ids=[str(e) for e in entity_ids],
        )

    await self._with_retry(_do)
```

- [ ] **Step 4: Run the tests (pass)**

```bash
uv run pytest packages/atlas-graph/atlas_graph/tests/test_tag_note.py -v
```

Expected: both PASS. Then run the full graph suite:

```bash
uv run pytest packages/atlas-graph -q 2>&1 | tail -3
```

Expected: no regressions.

- [ ] **Step 5: Commit**

```bash
git add packages/atlas-graph/atlas_graph/store.py packages/atlas-graph/atlas_graph/tests/test_tag_note.py
git commit -m "feat(graph): GraphStore.tag_note for Plan 6 explicit mentions"
```

---

### Task 6: GraphStore.list_entities

**Files:**
- Modify: `packages/atlas-graph/atlas_graph/store.py`
- Create: `packages/atlas-graph/atlas_graph/tests/test_list_entities.py`

- [ ] **Step 1: Write the failing tests**

Create `packages/atlas-graph/atlas_graph/tests/test_list_entities.py`:

```python
"""Cypher-shape tests for GraphStore.list_entities."""
from __future__ import annotations

from uuid import uuid4

import pytest

from atlas_graph.store import GraphStore


@pytest.mark.asyncio
async def test_list_entities_runs_read_with_pid_prefix_limit(fake_async_driver):
    store = GraphStore(fake_async_driver)  # type: ignore[arg-type]
    pid = uuid4()
    await store.list_entities(project_id=pid, prefix="Llam", limit=5)

    assert any(c.kwargs.get("pid") == str(pid) for c in fake_async_driver.calls)
    assert any(c.kwargs.get("prefix") == "Llam" for c in fake_async_driver.calls)
    assert any(c.kwargs.get("limit") == 5 for c in fake_async_driver.calls)
    assert any("Entity" in c.query for c in fake_async_driver.calls)


@pytest.mark.asyncio
async def test_list_entities_empty_prefix_passes_empty_string(fake_async_driver):
    store = GraphStore(fake_async_driver)  # type: ignore[arg-type]
    await store.list_entities(project_id=uuid4(), prefix="", limit=10)
    assert any(c.kwargs.get("prefix") == "" for c in fake_async_driver.calls)


@pytest.mark.asyncio
async def test_list_entities_returns_list_shape(fake_async_driver):
    store = GraphStore(fake_async_driver)  # type: ignore[arg-type]
    rows = await store.list_entities(project_id=uuid4(), prefix="x", limit=10)
    assert isinstance(rows, list)
```

- [ ] **Step 2: Run the tests (fail)**

```bash
uv run pytest packages/atlas-graph/atlas_graph/tests/test_list_entities.py -v
```

Expected: all three FAIL with attribute error.

- [ ] **Step 3: Add Cypher + method**

In `packages/atlas-graph/atlas_graph/store.py`, add this constant near the other Plan 5/6 constants:

```python
# Plan 6 — entity prefix lookup for the @-mention autocomplete dropdown.
LIST_ENTITIES_CYPHER = """
MATCH (e:Entity {project_id: $pid})
WHERE toLower(coalesce(e.name, '')) STARTS WITH toLower($prefix)
RETURN e.id AS id, e.name AS name, e.type AS entity_type,
       coalesce(e.pagerank_global, 0.0) AS pagerank
ORDER BY pagerank DESC
LIMIT $limit
"""
```

Add this method to `GraphStore` (after `tag_note`):

```python
async def list_entities(
    self,
    *,
    project_id: UUID,
    prefix: str,
    limit: int = 10,
) -> list[dict]:
    """Prefix-match entities for the @-mention autocomplete.

    Returns dicts with keys ``id, name, entity_type, pagerank`` ordered
    by PageRank DESC. Empty prefix returns top-N entities for the project.
    """
    async def _read(tx):
        result = await tx.run(
            LIST_ENTITIES_CYPHER,
            pid=str(project_id),
            prefix=prefix,
            limit=int(limit),
        )
        return await result.data()

    async with self._session() as s:
        rows = await s.execute_read(_read)

    return [
        {
            "id": r["id"],
            "name": r["name"],
            "entity_type": r["entity_type"],
            "pagerank": float(r["pagerank"]),
        }
        for r in rows
    ]
```

- [ ] **Step 4: Run the tests (pass)**

```bash
uv run pytest packages/atlas-graph/atlas_graph/tests/test_list_entities.py packages/atlas-graph -q 2>&1 | tail -3
```

Expected: all PASS, no regressions.

- [ ] **Step 5: Commit**

```bash
git add packages/atlas-graph/atlas_graph/store.py packages/atlas-graph/atlas_graph/tests/test_list_entities.py
git commit -m "feat(graph): GraphStore.list_entities for Plan 6 mention autocomplete"
```

---

### Task 7: GET /api/v1/knowledge/entities endpoint

**Files:**
- Modify: `apps/api/atlas_api/routers/knowledge.py`
- Modify: `packages/atlas-knowledge/atlas_knowledge/models/graph.py` — add `EntitySuggestion`.
- Create: `apps/api/atlas_api/tests/test_knowledge_entities_endpoint.py`

- [ ] **Step 1: Add `EntitySuggestion` Pydantic model**

In `packages/atlas-knowledge/atlas_knowledge/models/graph.py`, append:

```python
class EntitySuggestion(AtlasModel):
    """One row in the @-mention autocomplete dropdown."""
    id: UUID
    name: str
    entity_type: str | None = None
    pagerank: float = 0.0
```

- [ ] **Step 2: Write the failing tests**

Create `apps/api/atlas_api/tests/test_knowledge_entities_endpoint.py`:

```python
"""Integration tests for GET /api/v1/knowledge/entities (Plan 6)."""

from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from atlas_core.db.orm import ProjectORM
from atlas_graph.errors import GraphUnavailableError

from atlas_api.deps import get_graph_store
from atlas_api.main import app


@pytest.fixture
def fake_graph_store():
    store = AsyncMock()
    store.list_entities.return_value = [
        {"id": "11111111-1111-1111-1111-111111111111", "name": "Llama 3",
         "entity_type": "PRODUCT", "pagerank": 0.5},
    ]
    return store


@pytest.fixture
def app_with_graph_overrides(app_client, fake_graph_store):
    app.dependency_overrides[get_graph_store] = lambda: fake_graph_store
    yield app_client
    app.dependency_overrides.pop(get_graph_store, None)


@pytest.mark.asyncio
async def test_list_entities_happy_path(app_with_graph_overrides, db_session, fake_graph_store):
    project = ProjectORM(user_id="matt", name="P", default_model="claude-sonnet-4-6")
    db_session.add(project)
    await db_session.flush()

    resp = await app_with_graph_overrides.get(
        "/api/v1/knowledge/entities",
        params={"project_id": str(project.id), "prefix": "Lla", "limit": 5},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["name"] == "Llama 3"
    args, kwargs = fake_graph_store.list_entities.call_args
    assert kwargs["prefix"] == "Lla"
    assert kwargs["limit"] == 5


@pytest.mark.asyncio
async def test_list_entities_empty_prefix_default(app_with_graph_overrides, db_session, fake_graph_store):
    project = ProjectORM(user_id="matt", name="P", default_model="claude-sonnet-4-6")
    db_session.add(project)
    await db_session.flush()

    resp = await app_with_graph_overrides.get(
        "/api/v1/knowledge/entities",
        params={"project_id": str(project.id)},
    )
    assert resp.status_code == 200
    args, kwargs = fake_graph_store.list_entities.call_args
    assert kwargs["prefix"] == ""
    assert kwargs["limit"] == 10  # default


@pytest.mark.asyncio
async def test_list_entities_unknown_project_404(app_with_graph_overrides):
    resp = await app_with_graph_overrides.get(
        "/api/v1/knowledge/entities",
        params={"project_id": str(uuid4())},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_list_entities_503_when_graph_unavailable(
    app_with_graph_overrides, db_session, fake_graph_store
):
    project = ProjectORM(user_id="matt", name="P", default_model="claude-sonnet-4-6")
    db_session.add(project)
    await db_session.flush()
    fake_graph_store.list_entities.side_effect = GraphUnavailableError("down")

    resp = await app_with_graph_overrides.get(
        "/api/v1/knowledge/entities",
        params={"project_id": str(project.id)},
    )
    assert resp.status_code == 503
    assert resp.json()["detail"] == "graph_unavailable"
```

- [ ] **Step 3: Run the tests (fail)**

```bash
uv run pytest apps/api/atlas_api/tests/test_knowledge_entities_endpoint.py -v
```

Expected: all 4 tests fail (endpoint doesn't exist yet).

- [ ] **Step 4: Add the endpoint**

In `apps/api/atlas_api/routers/knowledge.py`, add the import:

```python
from atlas_knowledge.models.graph import (
    EntitySuggestion,
    GraphEdge,
    GraphMeta,
    GraphNode,
    GraphResponse,
)
```

(The other graph imports are already present; just add `EntitySuggestion`.)

Append to the file (after the `# --- Graph (explorer) ---` section, near the bottom):

```python
# --- Entities (mention autocomplete) -------------------------------------


@router.get("/knowledge/entities", response_model=list[EntitySuggestion])
async def list_entities(
    project_id: UUID,
    prefix: str = "",
    limit: int = 10,
    db: AsyncSession = Depends(get_session),
    graph_store: GraphStore = Depends(get_graph_store),
) -> list[EntitySuggestion]:
    """Prefix-match entities for the @-mention dropdown.

    Empty prefix returns top-N entities by PageRank.
    """
    project = await db.get(ProjectORM, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="project not found")
    try:
        rows = await graph_store.list_entities(
            project_id=project_id, prefix=prefix, limit=min(limit, 50)
        )
    except GraphUnavailableError as e:
        raise HTTPException(status_code=503, detail="graph_unavailable") from e
    return [
        EntitySuggestion(
            id=UUID(r["id"]),
            name=r["name"] or "",
            entity_type=r.get("entity_type"),
            pagerank=float(r.get("pagerank") or 0.0),
        )
        for r in rows
    ]
```

- [ ] **Step 5: Run the tests (pass)**

```bash
uv run pytest apps/api/atlas_api/tests/test_knowledge_entities_endpoint.py -v
uv run ruff check apps/api/atlas_api/routers/knowledge.py packages/atlas-knowledge/atlas_knowledge/models/graph.py
```

Expected: all 4 PASS, ruff clean.

- [ ] **Step 6: Commit**

```bash
git add apps/api/atlas_api/routers/knowledge.py packages/atlas-knowledge/atlas_knowledge/models/graph.py apps/api/atlas_api/tests/test_knowledge_entities_endpoint.py
git commit -m "feat(api/knowledge): GET /knowledge/entities for mention autocomplete"
```

---

### Task 8: /api/v1/notes — CRUD endpoints (list, create, get, patch, delete)

**Files:**
- Create: `apps/api/atlas_api/routers/notes.py`
- Create: `apps/api/atlas_api/tests/test_notes_router.py`

- [ ] **Step 1: Write the failing tests**

Create `apps/api/atlas_api/tests/test_notes_router.py`:

```python
"""Integration tests for /api/v1/notes (Plan 6)."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from atlas_core.db.orm import NoteORM, ProjectORM
from sqlalchemy import select


@pytest.mark.asyncio
async def test_create_note_default_fields(app_client, db_session):
    project = ProjectORM(user_id="matt", name="P", default_model="claude-sonnet-4-6")
    db_session.add(project)
    await db_session.flush()

    resp = await app_client.post(
        "/api/v1/notes",
        json={"project_id": str(project.id)},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["title"] == "Untitled"
    assert body["body_markdown"] == ""
    assert body["mention_entity_ids"] == []
    assert body["knowledge_node_id"] is None
    assert body["indexed_at"] is None


@pytest.mark.asyncio
async def test_list_notes_orders_by_updated_at_desc(app_client, db_session):
    project = ProjectORM(user_id="matt", name="P", default_model="claude-sonnet-4-6")
    db_session.add(project)
    await db_session.flush()
    older = NoteORM(user_id="matt", project_id=project.id, title="A",
                    updated_at=datetime(2026, 4, 1, tzinfo=UTC))
    newer = NoteORM(user_id="matt", project_id=project.id, title="B",
                    updated_at=datetime(2026, 4, 28, tzinfo=UTC))
    db_session.add_all([older, newer])
    await db_session.flush()

    resp = await app_client.get(f"/api/v1/notes?project_id={project.id}")
    assert resp.status_code == 200
    titles = [n["title"] for n in resp.json()]
    assert titles == ["B", "A"]


@pytest.mark.asyncio
async def test_get_note_returns_full_row(app_client, db_session):
    project = ProjectORM(user_id="matt", name="P", default_model="claude-sonnet-4-6")
    db_session.add(project)
    await db_session.flush()
    note = NoteORM(user_id="matt", project_id=project.id, title="Test",
                   body_markdown="hello")
    db_session.add(note)
    await db_session.flush()

    resp = await app_client.get(f"/api/v1/notes/{note.id}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["title"] == "Test"
    assert body["body_markdown"] == "hello"


@pytest.mark.asyncio
async def test_get_unknown_note_404(app_client):
    resp = await app_client.get(f"/api/v1/notes/{uuid4()}")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_patch_note_updates_fields(app_client, db_session):
    project = ProjectORM(user_id="matt", name="P", default_model="claude-sonnet-4-6")
    db_session.add(project)
    await db_session.flush()
    note = NoteORM(user_id="matt", project_id=project.id, title="Old")
    db_session.add(note)
    await db_session.flush()
    eid_a, eid_b = uuid4(), uuid4()

    resp = await app_client.patch(
        f"/api/v1/notes/{note.id}",
        json={"title": "New", "body_markdown": "body",
              "mention_entity_ids": [str(eid_a), str(eid_b)]},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["title"] == "New"
    assert body["body_markdown"] == "body"
    assert sorted(body["mention_entity_ids"]) == sorted([str(eid_a), str(eid_b)])


@pytest.mark.asyncio
async def test_patch_unknown_note_404(app_client):
    resp = await app_client.patch(f"/api/v1/notes/{uuid4()}", json={"title": "x"})
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_delete_note_removes_row(app_client, db_session):
    project = ProjectORM(user_id="matt", name="P", default_model="claude-sonnet-4-6")
    db_session.add(project)
    await db_session.flush()
    note = NoteORM(user_id="matt", project_id=project.id, title="Doomed")
    db_session.add(note)
    await db_session.flush()
    note_id = note.id

    resp = await app_client.delete(f"/api/v1/notes/{note_id}")
    assert resp.status_code == 204

    rows = (await db_session.execute(select(NoteORM).where(NoteORM.id == note_id))).all()
    assert rows == []


@pytest.mark.asyncio
async def test_delete_unknown_note_404(app_client):
    resp = await app_client.delete(f"/api/v1/notes/{uuid4()}")
    assert resp.status_code == 404
```

- [ ] **Step 2: Run the tests (fail)**

```bash
uv run pytest apps/api/atlas_api/tests/test_notes_router.py -v 2>&1 | tail -15
```

Expected: all 8 tests fail with 404 (endpoints don't exist).

- [ ] **Step 3: Create the router**

Create `apps/api/atlas_api/routers/notes.py`:

```python
"""Notes REST endpoints (Plan 6).

POST   /api/v1/notes              Create a draft note (no ingestion).
GET    /api/v1/notes              List notes for a project.
GET    /api/v1/notes/{id}         Get a note.
PATCH  /api/v1/notes/{id}         Update title/body/mentions (no ingestion).
DELETE /api/v1/notes/{id}         Delete + cleanup chunks across all stores.
POST   /api/v1/notes/{id}/index   Run the heavy ingestion pipeline.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from atlas_core.db.orm import IngestionJobORM, NoteORM, ProjectORM
from atlas_core.models.notes import (
    CreateNoteRequest,
    Note,
    NoteListItem,
    PatchNoteRequest,
)
from atlas_graph import GraphStore
from atlas_knowledge.ingestion.service import IngestionService
from atlas_knowledge.models.ingestion import IngestionJob
from atlas_knowledge.parsers.markdown import parse_markdown
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from atlas_api.deps import get_graph_store, get_ingestion_service, get_session

router = APIRouter(tags=["notes"])


def _note_from_orm(row: NoteORM) -> Note:
    return Note(
        id=row.id,
        user_id=row.user_id,
        project_id=row.project_id,
        knowledge_node_id=row.knowledge_node_id,
        title=row.title,
        body_markdown=row.body_markdown,
        mention_entity_ids=list(row.mention_entity_ids or []),
        indexed_at=row.indexed_at,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


@router.post("/notes", response_model=Note, status_code=201)
async def create_note(
    payload: CreateNoteRequest,
    db: AsyncSession = Depends(get_session),
) -> Note:
    project = await db.get(ProjectORM, payload.project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="project not found")
    row = NoteORM(
        user_id=project.user_id,
        project_id=payload.project_id,
        title=payload.title,
        body_markdown=payload.body_markdown,
    )
    db.add(row)
    await db.flush()
    return _note_from_orm(row)


@router.get("/notes", response_model=list[NoteListItem])
async def list_notes(
    project_id: UUID,
    db: AsyncSession = Depends(get_session),
) -> list[NoteListItem]:
    result = await db.execute(
        select(NoteORM)
        .where(NoteORM.project_id == project_id)
        .order_by(NoteORM.updated_at.desc())
    )
    return [
        NoteListItem(
            id=r.id, title=r.title, updated_at=r.updated_at, indexed_at=r.indexed_at
        )
        for r in result.scalars().all()
    ]


@router.get("/notes/{note_id}", response_model=Note)
async def get_note(
    note_id: UUID,
    db: AsyncSession = Depends(get_session),
) -> Note:
    row = await db.get(NoteORM, note_id)
    if row is None:
        raise HTTPException(status_code=404, detail="note not found")
    return _note_from_orm(row)


@router.patch("/notes/{note_id}", response_model=Note)
async def patch_note(
    note_id: UUID,
    payload: PatchNoteRequest,
    db: AsyncSession = Depends(get_session),
) -> Note:
    row = await db.get(NoteORM, note_id)
    if row is None:
        raise HTTPException(status_code=404, detail="note not found")
    if payload.title is not None:
        row.title = payload.title
    if payload.body_markdown is not None:
        row.body_markdown = payload.body_markdown
    if payload.mention_entity_ids is not None:
        row.mention_entity_ids = list(payload.mention_entity_ids)
    row.updated_at = datetime.now(UTC)
    await db.flush()
    return _note_from_orm(row)


@router.delete("/notes/{note_id}", status_code=204)
async def delete_note(
    note_id: UUID,
    db: AsyncSession = Depends(get_session),
    service: IngestionService = Depends(get_ingestion_service),
) -> None:
    row = await db.get(NoteORM, note_id)
    if row is None:
        raise HTTPException(status_code=404, detail="note not found")
    if row.knowledge_node_id is not None:
        await service.cleanup_document(
            db=db, project_id=row.project_id, document_id=row.knowledge_node_id
        )
    await db.delete(row)
    await db.flush()
```

- [ ] **Step 4: Wire the router into main.py**

In `apps/api/atlas_api/main.py`:

```python
from atlas_api.routers import notes as notes_router  # add to imports
# ...
app.include_router(notes_router.router, prefix="/api/v1")  # add to includes
```

- [ ] **Step 5: Run the tests (pass)**

```bash
uv run pytest apps/api/atlas_api/tests/test_notes_router.py -v 2>&1 | tail -15
uv run ruff check apps/api/atlas_api/routers/notes.py
```

Expected: 8 PASS, ruff clean.

- [ ] **Step 6: Commit**

```bash
git add apps/api/atlas_api/routers/notes.py apps/api/atlas_api/tests/test_notes_router.py apps/api/atlas_api/main.py
git commit -m "feat(api/notes): notes CRUD endpoints (list/create/get/patch/delete)"
```

---

### Task 9: /api/v1/notes/{id}/index endpoint

**Files:**
- Modify: `apps/api/atlas_api/routers/notes.py`
- Modify: `apps/api/atlas_api/tests/test_notes_router.py`

- [ ] **Step 1: Write the failing tests**

Append to `apps/api/atlas_api/tests/test_notes_router.py`:

```python
@pytest.mark.asyncio
async def test_index_first_time_runs_ingest_and_tags(app_client, db_session):
    """First-time index: cleanup NOT called; ingest called; tag_note called; row updated."""
    project = ProjectORM(user_id="matt", name="P", default_model="claude-sonnet-4-6")
    db_session.add(project)
    await db_session.flush()

    eid = uuid4()
    note = NoteORM(
        user_id="matt", project_id=project.id, title="t", body_markdown="hello",
        mention_entity_ids=[eid],
    )
    db_session.add(note)
    await db_session.flush()
    note_id = note.id

    fake_service = AsyncMock()
    new_doc_id = uuid4()
    fake_job_id = uuid4()
    from atlas_knowledge.ingestion.service import IngestionResult
    fake_service.ingest.return_value = IngestionResult(
        job_id=fake_job_id, document_id=new_doc_id
    )

    fake_graph_store = AsyncMock()

    job_row = IngestionJobORM(
        id=fake_job_id, user_id="matt", project_id=project.id,
        source_type="note", source_filename=None, status="completed",
    )
    db_session.add(job_row)
    await db_session.flush()

    from atlas_api.deps import get_graph_store, get_ingestion_service
    from atlas_api.main import app
    app.dependency_overrides[get_ingestion_service] = lambda: fake_service
    app.dependency_overrides[get_graph_store] = lambda: fake_graph_store
    try:
        resp = await app_client.post(f"/api/v1/notes/{note_id}/index")
    finally:
        app.dependency_overrides.pop(get_ingestion_service, None)
        app.dependency_overrides.pop(get_graph_store, None)

    assert resp.status_code == 200

    fake_service.cleanup_document.assert_not_called()
    fake_service.ingest.assert_awaited_once()
    args, kwargs = fake_service.ingest.call_args
    assert kwargs["source_type"] == "note"

    fake_graph_store.tag_note.assert_awaited_once()
    args, kwargs = fake_graph_store.tag_note.call_args
    assert kwargs["note_id"] == new_doc_id
    assert kwargs["entity_ids"] == [eid]

    refreshed = await db_session.get(NoteORM, note_id)
    await db_session.refresh(refreshed)
    assert refreshed.knowledge_node_id == new_doc_id
    assert refreshed.indexed_at is not None


@pytest.mark.asyncio
async def test_index_reindex_calls_cleanup_first(app_client, db_session):
    """Re-index: cleanup_document called with the previous knowledge_node_id."""
    project = ProjectORM(user_id="matt", name="P", default_model="claude-sonnet-4-6")
    db_session.add(project)
    await db_session.flush()

    prev_doc_id = uuid4()
    note = NoteORM(
        user_id="matt", project_id=project.id, title="t", body_markdown="hello",
        knowledge_node_id=prev_doc_id,
    )
    db_session.add(note)
    await db_session.flush()
    note_id = note.id

    fake_service = AsyncMock()
    new_doc_id = uuid4()
    fake_job_id = uuid4()
    from atlas_knowledge.ingestion.service import IngestionResult
    fake_service.ingest.return_value = IngestionResult(
        job_id=fake_job_id, document_id=new_doc_id
    )

    fake_graph_store = AsyncMock()

    job_row = IngestionJobORM(
        id=fake_job_id, user_id="matt", project_id=project.id,
        source_type="note", source_filename=None, status="completed",
    )
    db_session.add(job_row)
    await db_session.flush()

    from atlas_api.deps import get_graph_store, get_ingestion_service
    from atlas_api.main import app
    app.dependency_overrides[get_ingestion_service] = lambda: fake_service
    app.dependency_overrides[get_graph_store] = lambda: fake_graph_store
    try:
        resp = await app_client.post(f"/api/v1/notes/{note_id}/index")
    finally:
        app.dependency_overrides.pop(get_ingestion_service, None)
        app.dependency_overrides.pop(get_graph_store, None)

    assert resp.status_code == 200
    fake_service.cleanup_document.assert_awaited_once()
    args, kwargs = fake_service.cleanup_document.call_args
    assert kwargs["document_id"] == prev_doc_id


@pytest.mark.asyncio
async def test_index_no_mentions_skips_tag_note(app_client, db_session):
    project = ProjectORM(user_id="matt", name="P", default_model="claude-sonnet-4-6")
    db_session.add(project)
    await db_session.flush()
    note = NoteORM(
        user_id="matt", project_id=project.id, title="t", body_markdown="hi",
        mention_entity_ids=[],
    )
    db_session.add(note)
    await db_session.flush()
    note_id = note.id

    fake_service = AsyncMock()
    fake_job_id = uuid4()
    from atlas_knowledge.ingestion.service import IngestionResult
    fake_service.ingest.return_value = IngestionResult(
        job_id=fake_job_id, document_id=uuid4()
    )
    fake_graph_store = AsyncMock()
    job_row = IngestionJobORM(
        id=fake_job_id, user_id="matt", project_id=project.id,
        source_type="note", source_filename=None, status="completed",
    )
    db_session.add(job_row)
    await db_session.flush()

    from atlas_api.deps import get_graph_store, get_ingestion_service
    from atlas_api.main import app
    app.dependency_overrides[get_ingestion_service] = lambda: fake_service
    app.dependency_overrides[get_graph_store] = lambda: fake_graph_store
    try:
        resp = await app_client.post(f"/api/v1/notes/{note_id}/index")
    finally:
        app.dependency_overrides.pop(get_ingestion_service, None)
        app.dependency_overrides.pop(get_graph_store, None)

    assert resp.status_code == 200
    fake_graph_store.tag_note.assert_not_called()


@pytest.mark.asyncio
async def test_index_unknown_note_404(app_client):
    resp = await app_client.post(f"/api/v1/notes/{uuid4()}/index")
    assert resp.status_code == 404
```

- [ ] **Step 2: Run the tests (fail)**

```bash
uv run pytest apps/api/atlas_api/tests/test_notes_router.py -v 2>&1 | tail -10
```

Expected: 4 new tests fail (endpoint doesn't exist).

- [ ] **Step 3: Add the index endpoint**

In `apps/api/atlas_api/routers/notes.py`, append:

```python
@router.post("/notes/{note_id}/index", response_model=IngestionJob)
async def index_note(
    note_id: UUID,
    db: AsyncSession = Depends(get_session),
    service: IngestionService = Depends(get_ingestion_service),
    graph_store: GraphStore = Depends(get_graph_store),
) -> IngestionJob:
    """Run the full ingestion pipeline (chunker + embedder + NER + graph) on the note's body."""
    note = await db.get(NoteORM, note_id)
    if note is None:
        raise HTTPException(status_code=404, detail="note not found")

    if note.knowledge_node_id is not None:
        await service.cleanup_document(
            db=db, project_id=note.project_id, document_id=note.knowledge_node_id
        )

    parsed = parse_markdown(note.body_markdown, title=note.title)
    result = await service.ingest(
        db=db,
        user_id=note.user_id,
        project_id=note.project_id,
        parsed=parsed,
        source_type="note",
        source_filename=None,
    )

    if result.document_id is not None and note.mention_entity_ids:
        await graph_store.tag_note(
            note_id=result.document_id,
            entity_ids=list(note.mention_entity_ids),
        )

    note.knowledge_node_id = result.document_id
    note.indexed_at = datetime.now(UTC)
    await db.flush()

    job_row = await db.get(IngestionJobORM, result.job_id)
    if job_row is None:
        raise HTTPException(status_code=500, detail="ingest produced no job row")
    from atlas_core.db.converters import ingestion_job_from_orm
    return ingestion_job_from_orm(job_row)
```

- [ ] **Step 4: Run the tests (pass)**

```bash
uv run pytest apps/api/atlas_api/tests/test_notes_router.py -v 2>&1 | tail -15
uv run ruff check apps/api/atlas_api/routers/notes.py
```

Expected: 12 PASS (8 from Task 8 + 4 new), ruff clean.

- [ ] **Step 5: Commit**

```bash
git add apps/api/atlas_api/routers/notes.py apps/api/atlas_api/tests/test_notes_router.py
git commit -m "feat(api/notes): POST /notes/{id}/index runs full ingestion pipeline"
```

---

### Task 10: Real-Neo4j acceptance test for note indexing

**Files:**
- Modify: `packages/atlas-graph/atlas_graph/tests/test_subgraph_integration.py`

- [ ] **Step 1: Append the new test**

```python
@pytest.mark.asyncio
@pytest.mark.slow
async def test_tag_note_creates_tagged_with_edges(
    real_graph_store, isolated_project_id
):
    pid = isolated_project_id
    note_doc_id = uuid4()
    ent1, ent2 = uuid4(), uuid4()

    async with real_graph_store._driver.session() as s:
        await s.run(
            """
            CREATE (n:Document {id: $note, project_id: $pid, title: "My note", type: "note"})
            CREATE (e1:Entity {id: $e1, project_id: $pid, name: "X", type: "PERSON"})
            CREATE (e2:Entity {id: $e2, project_id: $pid, name: "Y", type: "ORG"})
            """,
            note=str(note_doc_id), pid=str(pid),
            e1=str(ent1), e2=str(ent2),
        )

    await real_graph_store.tag_note(note_id=note_doc_id, entity_ids=[ent1, ent2])

    # Idempotent — calling twice doesn't duplicate edges.
    await real_graph_store.tag_note(note_id=note_doc_id, entity_ids=[ent1, ent2])

    async with real_graph_store._driver.session() as s:
        result = await s.run(
            """
            MATCH (n:Document {id: $note})-[r:TAGGED_WITH]->(e:Entity)
            RETURN count(r) AS count
            """,
            note=str(note_doc_id),
        )
        records = await result.data()

    assert records[0]["count"] == 2


@pytest.mark.asyncio
@pytest.mark.slow
async def test_list_entities_prefix_match(real_graph_store, isolated_project_id):
    pid = isolated_project_id
    async with real_graph_store._driver.session() as s:
        await s.run(
            """
            CREATE (e1:Entity {id: $e1, project_id: $pid, name: "Llama 3", type: "PRODUCT", pagerank_global: 0.9})
            CREATE (e2:Entity {id: $e2, project_id: $pid, name: "Llama 2", type: "PRODUCT", pagerank_global: 0.5})
            CREATE (e3:Entity {id: $e3, project_id: $pid, name: "Mistral", type: "PRODUCT", pagerank_global: 0.7})
            """,
            pid=str(pid),
            e1=str(uuid4()), e2=str(uuid4()), e3=str(uuid4()),
        )
    rows = await real_graph_store.list_entities(project_id=pid, prefix="Lla", limit=10)
    names = [r["name"] for r in rows]
    assert names == ["Llama 3", "Llama 2"]  # ordered by pagerank DESC

    # Empty prefix returns all 3, ordered by pagerank.
    rows = await real_graph_store.list_entities(project_id=pid, prefix="", limit=10)
    assert [r["name"] for r in rows] == ["Llama 3", "Mistral", "Llama 2"]
```

- [ ] **Step 2: Verify it skips without env**

```bash
uv run pytest packages/atlas-graph/atlas_graph/tests/test_subgraph_integration.py -v 2>&1 | tail -5
```

Expected: all marked SKIPPED.

- [ ] **Step 3: Commit**

```bash
git add packages/atlas-graph/atlas_graph/tests/test_subgraph_integration.py
git commit -m "test(graph): real-Neo4j acceptance for tag_note + list_entities"
```

---

## Phase B — Frontend

### Task 11: Frontend deps + API clients

**Files:**
- Modify: `apps/web/package.json`
- Create: `apps/web/src/lib/api/notes.ts`
- Create: `apps/web/src/lib/api/entities.ts`

- [ ] **Step 1: Install deps**

```bash
cd apps/web && pnpm add @tiptap/react @tiptap/starter-kit @tiptap/extension-mention @tiptap/suggestion tippy.js turndown marked && pnpm add -D @types/turndown @types/marked
```

- [ ] **Step 2: Create the notes API client**

Create `apps/web/src/lib/api/notes.ts`:

```ts
export interface Note {
  id: string;
  user_id: string;
  project_id: string;
  knowledge_node_id: string | null;
  title: string;
  body_markdown: string;
  mention_entity_ids: string[];
  indexed_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface NoteListItem {
  id: string;
  title: string;
  updated_at: string;
  indexed_at: string | null;
}

export interface CreateNoteArgs {
  project_id: string;
  title?: string;
  body_markdown?: string;
}

export interface PatchNoteArgs {
  title?: string;
  body_markdown?: string;
  mention_entity_ids?: string[];
}

export interface IndexNoteResult {
  id: string;
  status: string;
  source_type: string;
  pagerank_status: string;
}

export class NotesApiError extends Error {
  constructor(message: string, public readonly status: number) {
    super(message);
    this.name = "NotesApiError";
  }
}

async function ok<T>(resp: Response): Promise<T> {
  if (!resp.ok) {
    const body = await resp.text();
    throw new NotesApiError(body || resp.statusText, resp.status);
  }
  if (resp.status === 204) return undefined as T;
  return resp.json();
}

export async function fetchNotes(projectId: string): Promise<NoteListItem[]> {
  const r = await fetch(`/api/v1/notes?project_id=${encodeURIComponent(projectId)}`);
  return ok<NoteListItem[]>(r);
}

export async function fetchNote(noteId: string): Promise<Note> {
  return ok<Note>(await fetch(`/api/v1/notes/${noteId}`));
}

export async function createNote(args: CreateNoteArgs): Promise<Note> {
  return ok<Note>(
    await fetch("/api/v1/notes", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(args),
    }),
  );
}

export async function patchNote(noteId: string, args: PatchNoteArgs): Promise<Note> {
  return ok<Note>(
    await fetch(`/api/v1/notes/${noteId}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(args),
    }),
  );
}

export async function indexNote(noteId: string): Promise<IndexNoteResult> {
  return ok<IndexNoteResult>(
    await fetch(`/api/v1/notes/${noteId}/index`, { method: "POST" }),
  );
}

export async function deleteNote(noteId: string): Promise<void> {
  await ok<void>(await fetch(`/api/v1/notes/${noteId}`, { method: "DELETE" }));
}
```

- [ ] **Step 3: Create the entities API client**

Create `apps/web/src/lib/api/entities.ts`:

```ts
export interface Entity {
  id: string;
  name: string;
  entity_type: string | null;
  pagerank: number;
}

export async function fetchEntities(
  projectId: string,
  prefix: string,
  limit = 10,
): Promise<Entity[]> {
  const params = new URLSearchParams({
    project_id: projectId,
    prefix,
    limit: String(limit),
  });
  const r = await fetch(`/api/v1/knowledge/entities?${params}`);
  if (!r.ok) {
    if (r.status === 503) return [];  // graph offline → no suggestions
    throw new Error(await r.text());
  }
  return r.json();
}
```

- [ ] **Step 4: Type-check + lint**

```bash
cd apps/web && pnpm typecheck && pnpm lint
```

Expected: 0 type errors, 0 new lint errors.

- [ ] **Step 5: Commit**

```bash
git add apps/web/package.json apps/web/pnpm-lock.yaml apps/web/src/lib/api/notes.ts apps/web/src/lib/api/entities.ts
git commit -m "feat(web/notes): add tiptap deps and notes/entities API clients"
```

---

### Task 12: Zustand notes-store

**Files:**
- Create: `apps/web/src/stores/notes-store.ts`
- Create: `apps/web/src/stores/notes-store.test.ts`

- [ ] **Step 1: Write the failing tests**

Create `apps/web/src/stores/notes-store.test.ts`:

```ts
import { describe, expect, it, beforeEach } from "vitest";
import { useNotesStore } from "./notes-store";

describe("notes-store", () => {
  beforeEach(() => useNotesStore.getState().reset());

  it("setDraft updates fields and marks dirty", () => {
    useNotesStore.getState().setDraft("body", "Title", new Set(["e1"]));
    const s = useNotesStore.getState();
    expect(s.draftBody).toBe("body");
    expect(s.draftTitle).toBe("Title");
    expect(s.draftMentionIds).toEqual(new Set(["e1"]));
    expect(s.dirty).toBe(true);
  });

  it("markSaved clears dirty flag", () => {
    useNotesStore.getState().setDraft("body", "Title", new Set());
    expect(useNotesStore.getState().dirty).toBe(true);
    useNotesStore.getState().markSaved();
    expect(useNotesStore.getState().dirty).toBe(false);
  });

  it("reset returns to initial state", () => {
    useNotesStore.getState().setDraft("body", "Title", new Set(["e1"]));
    useNotesStore.getState().reset();
    const s = useNotesStore.getState();
    expect(s.draftBody).toBe("");
    expect(s.draftTitle).toBe("");
    expect(s.draftMentionIds.size).toBe(0);
    expect(s.dirty).toBe(false);
  });
});
```

- [ ] **Step 2: Run the tests (fail)**

```bash
cd apps/web && pnpm test src/stores/notes-store.test.ts
```

Expected: tests fail (file doesn't exist).

- [ ] **Step 3: Implement the store**

Create `apps/web/src/stores/notes-store.ts`:

```ts
import { create } from "zustand";

interface NotesState {
  draftBody: string;
  draftTitle: string;
  draftMentionIds: Set<string>;
  dirty: boolean;

  setDraft: (body: string, title: string, mentions: Set<string>) => void;
  markSaved: () => void;
  reset: () => void;
}

const INITIAL: Pick<NotesState, "draftBody" | "draftTitle" | "draftMentionIds" | "dirty"> = {
  draftBody: "",
  draftTitle: "",
  draftMentionIds: new Set(),
  dirty: false,
};

export const useNotesStore = create<NotesState>((set) => ({
  ...INITIAL,

  setDraft: (body, title, mentions) =>
    set({ draftBody: body, draftTitle: title, draftMentionIds: mentions, dirty: true }),

  markSaved: () => set({ dirty: false }),

  reset: () => set({ ...INITIAL, draftMentionIds: new Set() }),
}));
```

- [ ] **Step 4: Run the tests (pass)**

```bash
cd apps/web && pnpm test src/stores/notes-store.test.ts
```

Expected: 3 PASS.

- [ ] **Step 5: Commit**

```bash
git add apps/web/src/stores/notes-store.ts apps/web/src/stores/notes-store.test.ts
git commit -m "feat(web/notes): zustand notes-store for editor draft state"
```

---

### Task 13: react-query hooks (use-notes)

**Files:**
- Create: `apps/web/src/components/notes/use-notes.ts`

- [ ] **Step 1: Implement the hooks**

Create `apps/web/src/components/notes/use-notes.ts`:

```ts
import {
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import {
  createNote,
  deleteNote,
  fetchNote,
  fetchNotes,
  indexNote,
  patchNote,
  type CreateNoteArgs,
  type Note,
  type NoteListItem,
  type PatchNoteArgs,
} from "@/lib/api/notes";
import { fetchEntities, type Entity } from "@/lib/api/entities";

export function useNotesQuery(projectId: string) {
  return useQuery<NoteListItem[]>({
    queryKey: ["notes", projectId],
    queryFn: () => fetchNotes(projectId),
    staleTime: 30_000,
  });
}

export function useNoteQuery(noteId: string | undefined) {
  return useQuery<Note>({
    queryKey: ["notes", "detail", noteId],
    enabled: !!noteId,
    queryFn: () => fetchNote(noteId!),
  });
}

export function useEntitiesQuery(projectId: string, prefix: string) {
  return useQuery<Entity[]>({
    queryKey: ["entities", projectId, prefix],
    queryFn: () => fetchEntities(projectId, prefix, 10),
    staleTime: 60_000,
    enabled: !!projectId,
  });
}

export function useCreateNote(projectId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (args: CreateNoteArgs) => createNote(args),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["notes", projectId] }),
  });
}

export function usePatchNote(projectId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ noteId, args }: { noteId: string; args: PatchNoteArgs }) =>
      patchNote(noteId, args),
    onSuccess: (note) => {
      qc.setQueryData<Note>(["notes", "detail", note.id], note);
      qc.invalidateQueries({ queryKey: ["notes", projectId] });
    },
  });
}

export function useIndexNote(projectId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (noteId: string) => indexNote(noteId),
    onSuccess: (_, noteId) => {
      qc.invalidateQueries({ queryKey: ["notes", "detail", noteId] });
      qc.invalidateQueries({ queryKey: ["notes", projectId] });
    },
  });
}

export function useDeleteNote(projectId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (noteId: string) => deleteNote(noteId),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["notes", projectId] }),
  });
}
```

- [ ] **Step 2: Type-check**

```bash
cd apps/web && pnpm typecheck
```

Expected: 0 errors.

- [ ] **Step 3: Commit**

```bash
git add apps/web/src/components/notes/use-notes.ts
git commit -m "feat(web/notes): react-query hooks for notes CRUD + entities autocomplete"
```

---

### Task 14: Note rail components (empty + list-item + list-rail)

**Files:**
- Create: `apps/web/src/components/notes/note-empty.tsx`
- Create: `apps/web/src/components/notes/note-list-item.tsx`
- Create: `apps/web/src/components/notes/note-list-rail.tsx`
- Create: `apps/web/src/components/notes/note-list-rail.test.tsx`

- [ ] **Step 1: Empty state**

Create `apps/web/src/components/notes/note-empty.tsx`:

```tsx
import { StickyNote } from "lucide-react";

export function NoteEmpty() {
  return (
    <div className="flex h-full flex-col items-center justify-center gap-2 text-muted-foreground">
      <StickyNote className="h-8 w-8" />
      <div className="text-sm">Select a note or create a new one.</div>
    </div>
  );
}
```

- [ ] **Step 2: List item**

Create `apps/web/src/components/notes/note-list-item.tsx`:

```tsx
import { Link, useParams } from "react-router-dom";
import { cn } from "@/lib/cn";
import type { NoteListItem as NoteListItemType } from "@/lib/api/notes";

interface Props {
  projectId: string;
  note: NoteListItemType;
}

export function NoteListItem({ projectId, note }: Props) {
  const { noteId: activeId } = useParams<{ noteId: string }>();
  const isStale =
    note.indexed_at === null ||
    new Date(note.updated_at).getTime() > new Date(note.indexed_at).getTime();
  const updated = new Date(note.updated_at).toLocaleDateString();
  return (
    <Link
      to={`/projects/${projectId}/notes/${note.id}`}
      className={cn(
        "flex flex-col gap-1 rounded-md px-2 py-2 text-sm hover:bg-accent",
        activeId === note.id && "bg-accent font-medium",
      )}
    >
      <div className="flex items-center gap-2">
        <span className="truncate flex-1">{note.title || "Untitled"}</span>
        {isStale && (
          <span
            aria-label="Index out of date"
            className="h-2 w-2 rounded-full bg-amber-500"
          />
        )}
      </div>
      <div className="text-xs text-muted-foreground">{updated}</div>
    </Link>
  );
}
```

- [ ] **Step 3: List rail**

Create `apps/web/src/components/notes/note-list-rail.tsx`:

```tsx
import { useNavigate, useParams } from "react-router-dom";
import { Plus } from "lucide-react";
import { Button } from "@/components/ui/button";
import { useCreateNote, useNotesQuery } from "./use-notes";
import { NoteListItem } from "./note-list-item";

export function NoteListRail() {
  const { id: projectId } = useParams<{ id: string }>();
  const { data, isPending, error } = useNotesQuery(projectId!);
  const createMutation = useCreateNote(projectId!);
  const navigate = useNavigate();

  const handleCreate = async () => {
    const note = await createMutation.mutateAsync({ project_id: projectId! });
    navigate(`/projects/${projectId}/notes/${note.id}`);
  };

  return (
    <aside className="flex w-64 flex-col border-r bg-muted/20">
      <div className="border-b p-2">
        <Button
          size="sm"
          className="w-full"
          onClick={handleCreate}
          disabled={createMutation.isPending}
        >
          <Plus className="mr-1 h-4 w-4" /> New note
        </Button>
      </div>
      <div className="flex-1 overflow-y-auto p-2">
        {isPending && (
          <div className="text-xs text-muted-foreground">Loading…</div>
        )}
        {error && (
          <div className="text-xs text-destructive">Failed to load notes.</div>
        )}
        {data && data.length === 0 && (
          <div className="text-xs text-muted-foreground">
            No notes yet — click "+ New note" to start.
          </div>
        )}
        {data && data.length > 0 && (
          <ul className="space-y-1">
            {data.map((n) => (
              <li key={n.id}>
                <NoteListItem projectId={projectId!} note={n} />
              </li>
            ))}
          </ul>
        )}
      </div>
    </aside>
  );
}
```

- [ ] **Step 4: List rail test**

Create `apps/web/src/components/notes/note-list-rail.test.tsx`:

```tsx
import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { NoteListRail } from "./note-list-rail";

function renderRail(notes: unknown[]) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  vi.stubGlobal("fetch", vi.fn(() =>
    Promise.resolve({
      ok: true,
      status: 200,
      json: () => Promise.resolve(notes),
    } as unknown as Response),
  ));
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={["/projects/p1/notes"]}>
        <Routes>
          <Route path="/projects/:id/notes" element={<NoteListRail />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("NoteListRail", () => {
  it("renders empty state when no notes", async () => {
    renderRail([]);
    expect(await screen.findByText(/no notes yet/i)).toBeInTheDocument();
  });

  it("renders note titles when notes exist", async () => {
    renderRail([
      { id: "n1", title: "First", updated_at: "2026-04-29T10:00:00Z", indexed_at: null },
      { id: "n2", title: "Second", updated_at: "2026-04-28T10:00:00Z", indexed_at: "2026-04-28T11:00:00Z" },
    ]);
    expect(await screen.findByText("First")).toBeInTheDocument();
    expect(await screen.findByText("Second")).toBeInTheDocument();
  });

  it("shows stale dot when updated_at > indexed_at", async () => {
    renderRail([
      { id: "n1", title: "Stale", updated_at: "2026-04-29T11:00:00Z", indexed_at: "2026-04-29T10:00:00Z" },
    ]);
    expect(await screen.findByLabelText(/index out of date/i)).toBeInTheDocument();
  });
});
```

- [ ] **Step 5: Run tests, typecheck, lint**

```bash
cd apps/web && pnpm test src/components/notes/note-list-rail.test.tsx && pnpm typecheck && pnpm lint
```

Expected: 3 PASS, typecheck clean, no new lint errors.

- [ ] **Step 6: Commit**

```bash
git add apps/web/src/components/notes/note-empty.tsx apps/web/src/components/notes/note-list-item.tsx apps/web/src/components/notes/note-list-rail.tsx apps/web/src/components/notes/note-list-rail.test.tsx
git commit -m "feat(web/notes): note rail (list + empty + item) with stale-index indicator"
```

---

### Task 15: Mention extension + dropdown

**Files:**
- Create: `apps/web/src/components/notes/note-mention-extension.ts`
- Create: `apps/web/src/components/notes/note-mention-extension.test.ts`
- Create: `apps/web/src/components/notes/note-mention-list.tsx`

- [ ] **Step 1: Write the failing test**

Create `apps/web/src/components/notes/note-mention-extension.test.ts`:

```ts
import { describe, expect, it } from "vitest";
import { extractMentionIds } from "./note-mention-extension";

describe("extractMentionIds", () => {
  it("returns empty set on empty doc", () => {
    expect(extractMentionIds({ type: "doc", content: [] })).toEqual(new Set());
  });

  it("collects ids from mention nodes", () => {
    const doc = {
      type: "doc",
      content: [
        {
          type: "paragraph",
          content: [
            { type: "text", text: "Hi " },
            { type: "mention", attrs: { id: "e1", label: "Llama" } },
            { type: "text", text: " and " },
            { type: "mention", attrs: { id: "e2", label: "Sky" } },
          ],
        },
      ],
    };
    expect(extractMentionIds(doc)).toEqual(new Set(["e1", "e2"]));
  });

  it("dedupes repeated mentions of same id", () => {
    const doc = {
      type: "doc",
      content: [
        { type: "mention", attrs: { id: "e1", label: "X" } },
        { type: "mention", attrs: { id: "e1", label: "X" } },
      ],
    };
    expect(extractMentionIds(doc)).toEqual(new Set(["e1"]));
  });
});
```

- [ ] **Step 2: Run the test (fail)**

```bash
cd apps/web && pnpm test src/components/notes/note-mention-extension.test.ts
```

Expected: file doesn't exist → fail.

- [ ] **Step 3: Implement the extension + extractor**

Create `apps/web/src/components/notes/note-mention-extension.ts`:

```ts
import Mention from "@tiptap/extension-mention";
import type { SuggestionOptions } from "@tiptap/suggestion";
import { fetchEntities } from "@/lib/api/entities";
import { renderMentionSuggestion } from "./note-mention-list";

interface DocNode {
  type: string;
  content?: DocNode[];
  attrs?: { id?: string; label?: string };
}

export function extractMentionIds(doc: DocNode): Set<string> {
  const out = new Set<string>();
  const walk = (n: DocNode) => {
    if (n.type === "mention" && n.attrs?.id) out.add(n.attrs.id);
    if (n.content) n.content.forEach(walk);
  };
  walk(doc);
  return out;
}

export const buildMention = (projectId: string) =>
  Mention.configure({
    HTMLAttributes: { class: "mention-chip" },
    suggestion: {
      char: "@",
      items: async ({ query }: { query: string }) =>
        fetchEntities(projectId, query, 10),
      render: renderMentionSuggestion,
    } as Partial<SuggestionOptions>,
  });
```

Create `apps/web/src/components/notes/note-mention-list.tsx`:

```tsx
import { useEffect, useImperativeHandle, useState, forwardRef } from "react";
import tippy, { type Instance } from "tippy.js";
import { ReactRenderer } from "@tiptap/react";
import type { Entity } from "@/lib/api/entities";

interface MentionListProps {
  items: Entity[];
  command: (item: { id: string; label: string }) => void;
}

const MentionList = forwardRef<{ onKeyDown: (e: KeyboardEvent) => boolean }, MentionListProps>(
  ({ items, command }, ref) => {
    const [active, setActive] = useState(0);
    useEffect(() => setActive(0), [items]);

    useImperativeHandle(ref, () => ({
      onKeyDown: (event: KeyboardEvent) => {
        if (event.key === "ArrowUp") {
          setActive((i) => (i + items.length - 1) % items.length);
          return true;
        }
        if (event.key === "ArrowDown") {
          setActive((i) => (i + 1) % items.length);
          return true;
        }
        if (event.key === "Enter") {
          const item = items[active];
          if (item) command({ id: item.id, label: item.name });
          return true;
        }
        return false;
      },
    }));

    if (items.length === 0) {
      return (
        <div className="rounded-md border bg-popover p-2 text-xs text-muted-foreground shadow-md">
          No matching entities
        </div>
      );
    }

    return (
      <div className="rounded-md border bg-popover p-1 shadow-md">
        {items.map((it, i) => (
          <button
            key={it.id}
            type="button"
            onClick={() => command({ id: it.id, label: it.name })}
            className={`flex w-full items-center justify-between rounded px-2 py-1 text-left text-sm ${
              i === active ? "bg-accent" : ""
            }`}
          >
            <span className="truncate">{it.name}</span>
            {it.entity_type && (
              <span className="ml-2 text-xs text-muted-foreground">
                {it.entity_type}
              </span>
            )}
          </button>
        ))}
      </div>
    );
  },
);
MentionList.displayName = "MentionList";

export function renderMentionSuggestion() {
  let component: ReactRenderer<{ onKeyDown: (e: KeyboardEvent) => boolean }> | null = null;
  let popup: Instance[] | null = null;

  return {
    onStart: (props: any) => {
      component = new ReactRenderer(MentionList, { props, editor: props.editor });
      if (!props.clientRect) return;
      popup = tippy("body", {
        getReferenceClientRect: props.clientRect,
        appendTo: () => document.body,
        content: component.element,
        showOnCreate: true,
        interactive: true,
        trigger: "manual",
        placement: "bottom-start",
      });
    },
    onUpdate(props: any) {
      component?.updateProps(props);
      if (props.clientRect && popup?.[0]) {
        popup[0].setProps({ getReferenceClientRect: props.clientRect });
      }
    },
    onKeyDown(props: any) {
      if (props.event.key === "Escape") {
        popup?.[0]?.hide();
        return true;
      }
      return component?.ref?.onKeyDown(props.event) ?? false;
    },
    onExit() {
      popup?.[0]?.destroy();
      component?.destroy();
    },
  };
}
```

- [ ] **Step 4: Run the test, typecheck, lint**

```bash
cd apps/web && pnpm test src/components/notes/note-mention-extension.test.ts && pnpm typecheck && pnpm lint
```

Expected: 3 PASS, typecheck clean, no new lint errors.

- [ ] **Step 5: Commit**

```bash
git add apps/web/src/components/notes/note-mention-extension.ts apps/web/src/components/notes/note-mention-extension.test.ts apps/web/src/components/notes/note-mention-list.tsx
git commit -m "feat(web/notes): tiptap mention extension + popover dropdown"
```

---

### Task 16: Note editor (title + TipTap + save bar + delete)

**Files:**
- Create: `apps/web/src/components/notes/note-editor.tsx`
- Create: `apps/web/src/components/notes/note-editor.test.tsx`

- [ ] **Step 1: Write the failing tests**

Create `apps/web/src/components/notes/note-editor.test.tsx`:

```tsx
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi, afterEach } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { NoteEditor } from "./note-editor";

function makeNote(overrides = {}) {
  return {
    id: "n1",
    user_id: "matt",
    project_id: "p1",
    knowledge_node_id: null,
    title: "Test",
    body_markdown: "hello",
    mention_entity_ids: [],
    indexed_at: null,
    created_at: "2026-04-29T10:00:00Z",
    updated_at: "2026-04-29T10:00:00Z",
    ...overrides,
  };
}

function renderEditor(note: object) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={["/projects/p1/notes/n1"]}>
        <Routes>
          <Route path="/projects/:id/notes/:noteId" element={<NoteEditor />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

afterEach(() => vi.unstubAllGlobals());

describe("NoteEditor", () => {
  it("shows the title in the input", async () => {
    vi.stubGlobal("fetch", vi.fn(() =>
      Promise.resolve({
        ok: true, status: 200,
        json: () => Promise.resolve(makeNote({ title: "Hello" })),
      } as unknown as Response)));
    renderEditor(makeNote());
    const input = await screen.findByDisplayValue("Hello");
    expect(input).toBeInTheDocument();
  });

  it("shows 'Saved' badge when note is loaded and not dirty", async () => {
    vi.stubGlobal("fetch", vi.fn(() =>
      Promise.resolve({
        ok: true, status: 200,
        json: () => Promise.resolve(makeNote()),
      } as unknown as Response)));
    renderEditor(makeNote());
    expect(await screen.findByText(/saved/i)).toBeInTheDocument();
  });

  it("shows 'Indexed' when indexed_at >= updated_at", async () => {
    const note = makeNote({
      updated_at: "2026-04-29T10:00:00Z",
      indexed_at: "2026-04-29T10:00:01Z",
    });
    vi.stubGlobal("fetch", vi.fn(() =>
      Promise.resolve({
        ok: true, status: 200,
        json: () => Promise.resolve(note),
      } as unknown as Response)));
    renderEditor(note);
    expect(await screen.findByText(/^indexed$/i)).toBeInTheDocument();
  });

  it("shows 'Indexed (stale)' when updated_at > indexed_at", async () => {
    const note = makeNote({
      updated_at: "2026-04-29T11:00:00Z",
      indexed_at: "2026-04-29T10:00:00Z",
    });
    vi.stubGlobal("fetch", vi.fn(() =>
      Promise.resolve({
        ok: true, status: 200,
        json: () => Promise.resolve(note),
      } as unknown as Response)));
    renderEditor(note);
    expect(await screen.findByText(/indexed \(stale\)/i)).toBeInTheDocument();
  });

  it("Save & Index button appears", async () => {
    vi.stubGlobal("fetch", vi.fn(() =>
      Promise.resolve({
        ok: true, status: 200,
        json: () => Promise.resolve(makeNote()),
      } as unknown as Response)));
    renderEditor(makeNote());
    expect(await screen.findByRole("button", { name: /save & index/i })).toBeInTheDocument();
  });

  it("delete button confirms before firing", async () => {
    const calls: string[] = [];
    vi.stubGlobal("fetch", vi.fn((url: string, init?: RequestInit) => {
      calls.push(`${init?.method ?? "GET"} ${url}`);
      return Promise.resolve({
        ok: true, status: init?.method === "DELETE" ? 204 : 200,
        text: () => Promise.resolve(""),
        json: () => Promise.resolve(makeNote()),
      } as unknown as Response);
    }));
    vi.stubGlobal("confirm", vi.fn(() => false));
    renderEditor(makeNote());
    const del = await screen.findByRole("button", { name: /delete/i });
    fireEvent.click(del);
    await waitFor(() => expect(calls.filter((c) => c.startsWith("DELETE")).length).toBe(0));
  });
});
```

- [ ] **Step 2: Run the tests (fail)**

```bash
cd apps/web && pnpm test src/components/notes/note-editor.test.tsx
```

Expected: file doesn't exist → fail.

- [ ] **Step 3: Implement the editor**

Create `apps/web/src/components/notes/note-editor.tsx`:

```tsx
import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { EditorContent, useEditor } from "@tiptap/react";
import StarterKit from "@tiptap/starter-kit";
import { marked } from "marked";
import TurndownService from "turndown";
import { Trash2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { cn } from "@/lib/cn";
import { useNotesStore } from "@/stores/notes-store";
import {
  useDeleteNote,
  useIndexNote,
  useNoteQuery,
  usePatchNote,
} from "./use-notes";
import { buildMention, extractMentionIds } from "./note-mention-extension";

const turndown = new TurndownService({ headingStyle: "atx" });

type SaveStatus = "Loading" | "Unsaved" | "Saving…" | "Saved" | "Indexed" | "Indexed (stale)";

function deriveStatus(opts: {
  loading: boolean;
  dirty: boolean;
  patching: boolean;
  indexed_at: string | null;
  updated_at: string;
}): SaveStatus {
  if (opts.loading) return "Loading";
  if (opts.patching) return "Saving…";
  if (opts.dirty) return "Unsaved";
  if (opts.indexed_at) {
    return new Date(opts.updated_at).getTime() > new Date(opts.indexed_at).getTime()
      ? "Indexed (stale)"
      : "Indexed";
  }
  return "Saved";
}

export function NoteEditor() {
  const { id: projectId, noteId } = useParams<{ id: string; noteId: string }>();
  const navigate = useNavigate();
  const noteQuery = useNoteQuery(noteId);
  const patchMutation = usePatchNote(projectId!);
  const indexMutation = useIndexNote(projectId!);
  const deleteMutation = useDeleteNote(projectId!);

  const draftBody = useNotesStore((s) => s.draftBody);
  const draftTitle = useNotesStore((s) => s.draftTitle);
  const draftMentionIds = useNotesStore((s) => s.draftMentionIds);
  const dirty = useNotesStore((s) => s.dirty);
  const setDraft = useNotesStore((s) => s.setDraft);
  const markSaved = useNotesStore((s) => s.markSaved);
  const reset = useNotesStore((s) => s.reset);

  const debounceTimer = useRef<number | null>(null);

  const mention = useMemo(
    () => (projectId ? buildMention(projectId) : null),
    [projectId],
  );

  const editor = useEditor({
    extensions: mention ? [StarterKit, mention] : [StarterKit],
    content: "",
    onUpdate: ({ editor }) => {
      const html = editor.getHTML();
      const md = turndown.turndown(html);
      const mentions = extractMentionIds(editor.getJSON() as never);
      setDraft(md, draftTitle, mentions);
    },
  });

  // Hydrate the editor when the note arrives.
  useEffect(() => {
    reset();
    if (!noteQuery.data || !editor) return;
    const html = marked.parse(noteQuery.data.body_markdown ?? "", { async: false }) as string;
    editor.commands.setContent(html);
    setDraft(noteQuery.data.body_markdown, noteQuery.data.title, new Set(noteQuery.data.mention_entity_ids));
    markSaved();  // freshly loaded = clean
  }, [noteQuery.data?.id]);  // eslint-disable-line react-hooks/exhaustive-deps

  // Debounced auto-save.
  useEffect(() => {
    if (!noteId || !dirty) return;
    if (debounceTimer.current) window.clearTimeout(debounceTimer.current);
    debounceTimer.current = window.setTimeout(() => {
      patchMutation.mutate(
        {
          noteId,
          args: {
            title: draftTitle,
            body_markdown: draftBody,
            mention_entity_ids: [...draftMentionIds],
          },
        },
        { onSuccess: () => markSaved() },
      );
    }, 2000);
    return () => {
      if (debounceTimer.current) window.clearTimeout(debounceTimer.current);
    };
  }, [draftBody, draftTitle, draftMentionIds, dirty, noteId]);  // eslint-disable-line react-hooks/exhaustive-deps

  const handleSaveAndIndex = async () => {
    if (!noteId) return;
    if (dirty) {
      await patchMutation.mutateAsync({
        noteId,
        args: {
          title: draftTitle,
          body_markdown: draftBody,
          mention_entity_ids: [...draftMentionIds],
        },
      });
      markSaved();
    }
    await indexMutation.mutateAsync(noteId);
  };

  const handleDelete = async () => {
    if (!noteId) return;
    if (!window.confirm("Delete this note? This will also remove its chunks from search.")) {
      return;
    }
    await deleteMutation.mutateAsync(noteId);
    navigate(`/projects/${projectId}/notes`);
  };

  if (!noteQuery.data && noteQuery.isPending) {
    return <div className="p-4 text-sm text-muted-foreground">Loading…</div>;
  }
  if (noteQuery.error) {
    return <div className="p-4 text-sm text-destructive">Failed to load note.</div>;
  }
  if (!noteQuery.data) return null;

  const status = deriveStatus({
    loading: noteQuery.isPending,
    dirty,
    patching: patchMutation.isPending,
    indexed_at: noteQuery.data.indexed_at,
    updated_at: noteQuery.data.updated_at,
  });
  const indexing = indexMutation.isPending;

  return (
    <div className="flex h-full flex-col">
      <div className="flex items-center gap-3 border-b p-3">
        <Input
          value={draftTitle}
          onChange={(e) => setDraft(draftBody, e.target.value, draftMentionIds)}
          placeholder="Untitled"
          className="border-0 text-base font-semibold focus-visible:ring-0"
        />
        <span
          className={cn(
            "rounded px-2 py-0.5 text-xs",
            status === "Indexed" && "bg-blue-100 text-blue-800",
            status === "Indexed (stale)" && "bg-amber-100 text-amber-900",
            status === "Saving…" && "bg-muted text-muted-foreground animate-pulse",
            status === "Saved" && "bg-muted text-muted-foreground",
            status === "Unsaved" && "bg-amber-50 text-amber-800",
          )}
        >
          {indexing ? "Indexing…" : status}
        </span>
        <Button onClick={handleSaveAndIndex} disabled={indexing}>
          Save & Index
        </Button>
        <Button variant="ghost" size="icon" onClick={handleDelete} aria-label="Delete">
          <Trash2 className="h-4 w-4" />
        </Button>
      </div>
      <div className="flex-1 overflow-y-auto p-6">
        <div className="mx-auto max-w-3xl">
          <EditorContent editor={editor} />
        </div>
      </div>
    </div>
  );
}
```

- [ ] **Step 4: Run the tests, typecheck, lint**

```bash
cd apps/web && pnpm test src/components/notes/note-editor.test.tsx && pnpm typecheck && pnpm lint
```

Expected: 6 PASS, typecheck clean, no new lint errors.

- [ ] **Step 5: Commit**

```bash
git add apps/web/src/components/notes/note-editor.tsx apps/web/src/components/notes/note-editor.test.tsx
git commit -m "feat(web/notes): note editor with TipTap + auto-save + Save & Index"
```

---

### Task 17: NotesRoute + ProjectTabs + main.tsx routes

**Files:**
- Create: `apps/web/src/routes/notes.tsx`
- Modify: `apps/web/src/components/sidebar/project-tabs.tsx`
- Modify: `apps/web/src/main.tsx`

- [ ] **Step 1: Create the route shell**

Create `apps/web/src/routes/notes.tsx`:

```tsx
import { Outlet } from "react-router-dom";
import { NoteListRail } from "@/components/notes/note-list-rail";
import { NoteEmpty } from "@/components/notes/note-empty";

export function NotesRoute() {
  return (
    <div className="flex h-full">
      <NoteListRail />
      <div className="flex-1 overflow-hidden">
        <Outlet />
      </div>
    </div>
  );
}

export function NotesIndex() {
  return <NoteEmpty />;
}
```

- [ ] **Step 2: Add Notes tab to ProjectTabs**

Edit `apps/web/src/components/sidebar/project-tabs.tsx` — add the third NavLink at the bottom of the existing nav:

```tsx
import { MessageSquare, Network, StickyNote } from "lucide-react";
// ...
// after the existing Explorer NavLink, add:
<NavLink
  to={`/projects/${projectId}/notes`}
  className={({ isActive }) =>
    cn(
      "flex items-center gap-1.5 rounded-md px-3 py-1 text-sm transition",
      isActive ? "bg-accent font-medium" : "text-muted-foreground hover:bg-accent/50",
    )
  }
>
  <StickyNote className="h-4 w-4" />
  Notes
</NavLink>
```

- [ ] **Step 3: Wire the nested route**

Edit `apps/web/src/main.tsx` — find the `projects/:id` route children and add the notes branch:

```tsx
import { NoteEditor } from "./components/notes/note-editor";
import { NotesIndex, NotesRoute } from "./routes/notes";

// inside the createBrowserRouter projects/:id children, append:
{
  path: "notes",
  element: <NotesRoute />,
  children: [
    { index: true, element: <NotesIndex /> },
    { path: ":noteId", element: <NoteEditor /> },
  ],
},
```

- [ ] **Step 4: Run typecheck + lint + tests**

```bash
cd apps/web && pnpm typecheck && pnpm lint && pnpm test
```

Expected: typecheck clean, no new lint errors, all tests pass (existing 38 + Plan 6's new tests).

- [ ] **Step 5: Commit**

```bash
git add apps/web/src/routes/notes.tsx apps/web/src/components/sidebar/project-tabs.tsx apps/web/src/main.tsx
git commit -m "feat(web): nested /projects/:id/notes route + Notes tab in ProjectTabs"
```

---

### Task 18: Manual smoke test + acceptance checklist

This task is a verification gate, not a code change.

- [ ] **Step 1: Bring up the stack**

```bash
docker compose -f infra/docker-compose.yml up -d
docker compose -f infra/docker-compose.yml build api web
docker compose -f infra/docker-compose.yml up -d api web
```

- [ ] **Step 2: Apply the new alembic migration on the running database**

```bash
uv run alembic upgrade head
```

- [ ] **Step 3: Verify the 7 acceptance criteria from the spec §7**

Open `http://localhost:3000/projects/<id>/notes` for an existing project (e.g. the one from Plan 5 smoke):

1. ☐ Notes tab in header navigates to `/notes`. Empty rail shows "No notes yet".
2. ☐ Click "+ New note" → row created, editor opens with title "Untitled".
3. ☐ Type 30 characters into the editor → wait 2 s → header badge changes to "Saving…" then "Saved". `GET /api/v1/notes/<id>` returns the body verbatim.
4. ☐ Type `@` in the editor → dropdown appears with existing entities sorted by PageRank. Type a few chars → dropdown filters. Pick one → green chip appears.
5. ☐ Click "Save & Index" → spinner → header transitions to "Indexed". `GET /api/v1/knowledge/search?project_id=...&query=<some-text-from-note>` returns the note's chunks.
6. ☐ Open `/projects/<id>/explorer` → search for the entity you tagged → its 1-hop subgraph includes a node whose `metadata.text_preview` matches the note body, and an edge of type `TAGGED_WITH` between the note's Document node and the entity.
7. ☐ Edit the note (add a sentence) → header → "Indexed (stale)". Click Save & Index again → re-runs cleanup + ingest cleanly. Old chunks removed (verify by checking `KnowledgeNodeORM` count for `parent_id` of previous knowledge_node_id is now 0).
8. ☐ Click delete → confirm dialog → row deleted, navigates back to `/notes`. Search for the same text in chat search → no results.

- [ ] **Step 4: Commit smoke results**

If all pass:

```bash
git commit --allow-empty -m "test(plan-6): manual smoke — all 8 acceptance criteria pass"
```

If any fail, file a follow-up task for each before claiming done.

---

## Self-review

Spec coverage check (against `2026-04-29-atlas-phase-2-plan-6-note-editor-design.md`):

- §3.1 two-state save → Tasks 8, 9, 16 (auto-save debounce + index button + status derivation).
- §3.2 schema reuse → Tasks 1, 2 (notes table + KnowledgeNodeType.NOTE).
- §4.1 notes table → Task 1.
- §4.2 KnowledgeNodeType.NOTE → Task 2.
- §4.3 REST endpoints → Tasks 8 (CRUD), 9 (index).
- §4.4 index logic + IngestionResult → Tasks 3 (return shape), 9 (handler).
- §4.5 entities autocomplete → Tasks 6 (GraphStore), 7 (router).
- §4.6 GraphStore.tag_note → Task 5.
- §4.7 Pydantic models → Task 2.
- §5.1 routes → Task 17.
- §5.2 component tree → Tasks 14, 15, 16, 17.
- §5.3 library → Task 11.
- §5.4 store → Task 12.
- §5.5 react-query hooks → Task 13.
- §5.6 save flow → Task 16 (the editor's effects).
- §5.7 mention extension → Task 15.
- §5.8 markdown round-trip → Task 16 (uses marked + turndown).
- §5.9 visuals → Task 16 (badge styling, mention chip class).
- §5.10 types → Task 11 (notes.ts, entities.ts).
- §6 testing strategy → covered across all tasks; Cytoscape-style canvas-test gap noted at §6.2 of the spec is mirrored here for TipTap (Task 16 unit-tests the React component but not the contentEditable behavior).
- §7 acceptance → Task 18.
- §8 risks — informational, no task needed.

**No spec gap.**

Naming consistency check:
- `IngestionResult` / `result.job_id` / `result.document_id` — used consistently across Tasks 3, 9.
- `cleanup_document` — same name on both `IngestionService` (Task 4) and `GraphStore` (existing Plan 3); not ambiguous in context.
- `tag_note(note_id, entity_ids)` — same kwargs everywhere (Tasks 5, 9).
- `mention_entity_ids` — same name in DB column, ORM, Pydantic, TS types, react-query keys.

Placeholder scan: no TBDs, no "implement later" stubs, every step has complete code.
