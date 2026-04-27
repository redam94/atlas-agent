# ATLAS Phase 1 — Plan 4: Knowledge Layer (embeddings, vector store, ingestion, retrieval)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Implements:** `docs/superpowers/specs/2026-04-26-atlas-phase-1-foundation-design.md` — §6 (Knowledge & RAG: ingestion pipeline, embedding service, retrieval, VectorStore interface), §9 (`/api/v1/knowledge/*` endpoints), §10 (`ingestion_jobs`, `knowledge_nodes` tables).

**Goal:** A working knowledge layer. POST a markdown file or text via `/api/v1/knowledge/ingest`, watch the ingestion job complete, then GET `/api/v1/knowledge/search?project_id=...&query=...` and see ranked chunks come back. RAG is **not** wired into chat in this plan — that's Plan 5.

**Architecture:** A new `atlas-knowledge` package (skeleton already exists from Plan 1) flowers into a layered pipeline: `Parser` → `SemanticChunker` → `EmbeddingService` → `VectorStore`. The `IngestionService` orchestrates the pipeline and persists `KnowledgeNodeORM` rows + an `IngestionJobORM` row. A `Retriever` reverses the flow for queries. ChromaDB runs in-process (embedded mode); `BAAI/bge-small-en-v1.5` (~130MB, 384-dim) loads lazily into the SentenceTransformers cache on first use. Both sentence-transformers and Chroma are sync libraries — wrap their hot calls in `anyio.to_thread.run_sync` to keep the FastAPI event loop responsive.

**Tech Stack:** `sentence-transformers>=3.3` (BGE-small) · `chromadb>=0.5` (in-process) · `pymupdf>=1.25` (PDF parsing) · `anyio` (already transitive) · async SQLAlchemy + FastAPI `BackgroundTasks` for ingestion.

---

## File Structure

```
atlas-agent/
├── apps/api/
│   └── atlas_api/
│       ├── routers/
│       │   └── knowledge.py                                # NEW (5 endpoints)
│       ├── tests/
│       │   └── test_knowledge_router.py                    # NEW
│       └── main.py                                         # MODIFIED (lifespan builds embedder + vector store; include knowledge router)
├── packages/atlas-core/
│   └── atlas_core/
│       ├── db/
│       │   ├── orm.py                                      # MODIFIED (append KnowledgeNodeORM, IngestionJobORM)
│       │   └── converters.py                               # MODIFIED (append knowledge_node_from_orm, ingestion_job_from_orm)
│       └── models/
│           └── __init__.py                                 # MODIFIED (re-export new symbols if added there — not required since these live in atlas-knowledge)
├── packages/atlas-knowledge/
│   ├── pyproject.toml                                      # MODIFIED (add deps)
│   └── atlas_knowledge/
│       ├── __init__.py                                     # MODIFIED (re-exports)
│       ├── models/
│       │   ├── __init__.py                                 # NEW
│       │   ├── nodes.py                                    # NEW (KnowledgeNode, KnowledgeNodeType, ChunkMetadata)
│       │   ├── embeddings.py                               # NEW (EmbeddingRequest, EmbeddingResult)
│       │   ├── retrieval.py                                # NEW (RetrievalQuery, ScoredChunk, RetrievalResult, RagContext)
│       │   └── ingestion.py                                # NEW (IngestRequest, IngestionJob, IngestionStatus, SourceType)
│       ├── embeddings/
│       │   ├── __init__.py                                 # NEW
│       │   ├── service.py                                  # NEW (EmbeddingService ABC)
│       │   ├── providers/
│       │   │   ├── __init__.py                             # NEW
│       │   │   ├── local.py                                # NEW (SentenceTransformersEmbedder, BGE-small)
│       │   │   └── _fake.py                                # NEW (FakeEmbedder for tests — deterministic hash-based vectors)
│       ├── vector/
│       │   ├── __init__.py                                 # NEW
│       │   ├── store.py                                    # NEW (VectorStore ABC)
│       │   └── chroma.py                                   # NEW (ChromaVectorStore)
│       ├── chunking/
│       │   ├── __init__.py                                 # NEW
│       │   └── semantic.py                                 # NEW (SemanticChunker)
│       ├── parsers/
│       │   ├── __init__.py                                 # NEW
│       │   ├── markdown.py                                 # NEW (passthrough)
│       │   └── pdf.py                                      # NEW (PyMuPDF)
│       ├── ingestion/
│       │   ├── __init__.py                                 # NEW
│       │   └── service.py                                  # NEW (IngestionService — orchestration)
│       ├── retrieval/
│       │   ├── __init__.py                                 # NEW
│       │   └── retriever.py                                # NEW (Retriever)
│       └── tests/
│           ├── test_models_nodes.py                        # NEW
│           ├── test_models_retrieval.py                    # NEW
│           ├── test_embeddings_fake.py                     # NEW
│           ├── test_vector_chroma.py                       # NEW (uses tmp_path-backed Chroma)
│           ├── test_chunking_semantic.py                   # NEW
│           ├── test_parsers_markdown.py                    # NEW
│           ├── test_parsers_pdf.py                         # NEW (uses small generated PDF fixture)
│           ├── test_ingestion_service.py                   # NEW (FakeEmbedder + tmp Chroma)
│           └── test_retriever.py                           # NEW
└── infra/alembic/versions/
    └── 0003_add_knowledge_nodes_and_ingestion_jobs.py      # NEW
```

**Responsibility per new module:**

- `models/nodes.py` — `KnowledgeNodeType` enum (`document`/`chunk`), `KnowledgeNode` Pydantic model (covers both shapes; `parent_id` populated only on chunks). `ChunkMetadata` shape carried in Chroma metadata column.
- `models/embeddings.py` — request/response shapes for the embedding service.
- `models/retrieval.py` — query and result shapes; `ScoredChunk` bundles a `KnowledgeNode` with similarity score; `RagContext` is the system-prompt-friendly bundle assembled by the `Retriever`.
- `models/ingestion.py` — REST input shape (`IngestRequest`) + persisted job shape (`IngestionJob`) + `IngestionStatus` enum + `SourceType` enum.
- `embeddings/service.py` — `EmbeddingService` ABC: `embed_documents(list[str]) -> list[list[float]]`, `embed_query(str) -> list[float]`. Both async.
- `embeddings/providers/local.py` — `SentenceTransformersEmbedder` lazily loads BGE-small into a process-wide cache. Sync model calls wrapped via `anyio.to_thread.run_sync`.
- `embeddings/providers/_fake.py` — `FakeEmbedder` for tests: deterministic hash-based 16-dim vectors (no network, no model download).
- `vector/store.py` — `VectorStore` ABC (`upsert`, `search`, `delete`).
- `vector/chroma.py` — `ChromaVectorStore` wrapping `chromadb.PersistentClient` in embedded mode. One collection per user.
- `chunking/semantic.py` — `SemanticChunker` splits a document into ~512-token chunks with 128 overlap, respecting paragraph and heading boundaries.
- `parsers/markdown.py` — `parse_markdown(text) -> ParsedDocument` (passthrough; just wraps text with a `ParsedDocument` dataclass).
- `parsers/pdf.py` — `parse_pdf(bytes) -> ParsedDocument` using PyMuPDF.
- `ingestion/service.py` — `IngestionService` runs the full pipeline; updates the job status as it goes; persists `KnowledgeNodeORM` rows for document + chunks; pushes embeddings to the vector store.
- `retrieval/retriever.py` — `Retriever.retrieve(query)` embeds query, calls `VectorStore.search`, hydrates `ScoredChunk[]` (joining back to `KnowledgeNodeORM` for full text + parent doc title).
- `routers/knowledge.py` — five FastAPI handlers (POST /ingest, GET /jobs/{id}, GET /nodes, DELETE /nodes/{id}, GET /search).
- ORM models (`KnowledgeNodeORM`, `IngestionJobORM`) live in `atlas-core/db/orm.py` for consistency with the other tables (Plan 2/3 pattern). The `atlas-knowledge` package only handles Pydantic models, embedding/vector/chunking/parsing/orchestration logic.

---

## Task 1: Add `sentence-transformers`, `chromadb`, `pymupdf` deps to `atlas-knowledge`

**Files:**
- Modify: `packages/atlas-knowledge/pyproject.toml`

- [ ] **Step 1: Edit pyproject.toml**

Replace the `dependencies` list:
```toml
dependencies = [
    "atlas-core",
    "pydantic>=2.10",
    "sentence-transformers>=3.3",
    "chromadb>=0.5",
    "pymupdf>=1.25",
    "anyio>=4.6",
]
```

- [ ] **Step 2: Sync**

Run: `uv sync --all-packages`
Expected: success — sentence-transformers + chromadb + pymupdf added (large, takes ~30s on first sync because torch is pulled).

- [ ] **Step 3: Smoke import**

Run:
```bash
uv run python -c "import sentence_transformers, chromadb, fitz; print('ok')"
```
Expected: `ok`. (`fitz` is PyMuPDF's import name.)

- [ ] **Step 4: Commit**

```bash
git add packages/atlas-knowledge/pyproject.toml uv.lock
git commit -m "chore(atlas-knowledge): add sentence-transformers, chromadb, pymupdf deps"
```

---

## Task 2: Add migration 0003 for `knowledge_nodes` + `ingestion_jobs`

**Files:**
- Create: `infra/alembic/versions/0003_add_knowledge_nodes_and_ingestion_jobs.py`

- [ ] **Step 1: Write the migration**

`infra/alembic/versions/0003_add_knowledge_nodes_and_ingestion_jobs.py`:
```python
"""add knowledge_nodes + ingestion_jobs

Revision ID: 0003
Revises: 0002
Create Date: 2026-04-27
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "knowledge_nodes",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("user_id", sa.Text(), nullable=False),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("type", sa.Text(), nullable=False),  # document | chunk
        sa.Column(
            "parent_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("knowledge_nodes.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("metadata_", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("embedding_id", sa.Text(), nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("NOW()")),
    )
    op.create_index("knowledge_nodes_project_type_idx", "knowledge_nodes", ["project_id", "type"])
    op.create_index("knowledge_nodes_parent_idx", "knowledge_nodes", ["parent_id"])

    op.create_table(
        "ingestion_jobs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("user_id", sa.Text(), nullable=False),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("source_type", sa.Text(), nullable=False),  # pdf | markdown
        sa.Column("source_filename", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False, server_default=sa.text("'pending'")),
        sa.Column("node_ids", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.Column("completed_at", sa.TIMESTAMP(timezone=True), nullable=True),
    )
    op.create_index("ingestion_jobs_project_idx", "ingestion_jobs", ["project_id", "created_at"])


def downgrade() -> None:
    op.drop_index("ingestion_jobs_project_idx", table_name="ingestion_jobs")
    op.drop_table("ingestion_jobs")
    op.drop_index("knowledge_nodes_parent_idx", table_name="knowledge_nodes")
    op.drop_index("knowledge_nodes_project_type_idx", table_name="knowledge_nodes")
    op.drop_table("knowledge_nodes")
```

(Note: column is named `metadata_` in Python because `metadata` is a reserved attribute on SQLAlchemy `DeclarativeBase`. The actual SQL column name is set explicitly to `metadata` via `sa.Column("metadata_", ...)` — wait, no: `op.create_table` uses the Python name as the column name. Use `sa.Column("metadata", ...)` here in Alembic since this is SQL DDL only, not the ORM. The ORM in Task 3 handles the rename via `Mapped[dict] ... = mapped_column("metadata", ...)`.)

Actually, replace the `metadata_` entry with:
```python
sa.Column("metadata", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
```

The SQL column is `metadata`. In Task 3 the ORM model uses a Python-side rename.

- [ ] **Step 2: Apply migration to dev DB**

```bash
ATLAS_DB__DATABASE_URL=postgresql://atlas:atlas@localhost:5432/atlas \
  uv run alembic upgrade head
docker exec atlas-postgres psql -U atlas -d atlas -c "\dt"
```
Expected: lists `projects`, `sessions`, `messages`, `model_usage`, `knowledge_nodes`, `ingestion_jobs`, `alembic_version`.

- [ ] **Step 3: Test downgrade roundtrip**

```bash
ATLAS_DB__DATABASE_URL=postgresql://atlas:atlas@localhost:5432/atlas \
  uv run alembic downgrade -1
docker exec atlas-postgres psql -U atlas -d atlas -c "\dt"
```
Expected: `knowledge_nodes` and `ingestion_jobs` are gone; `projects`/`sessions`/`messages`/`model_usage` remain.

```bash
ATLAS_DB__DATABASE_URL=postgresql://atlas:atlas@localhost:5432/atlas \
  uv run alembic upgrade head
```

- [ ] **Step 4: Commit**

```bash
git add infra/alembic/versions/0003_add_knowledge_nodes_and_ingestion_jobs.py
git commit -m "feat(db): add knowledge_nodes + ingestion_jobs tables"
```

---

## Task 3: Add `KnowledgeNodeORM` and `IngestionJobORM`

**Files:**
- Modify: `packages/atlas-core/atlas_core/db/orm.py` (append)
- Create: `packages/atlas-core/atlas_core/tests/test_db_orm_knowledge.py`

- [ ] **Step 1: Write failing tests**

`packages/atlas-core/atlas_core/tests/test_db_orm_knowledge.py`:
```python
"""Smoke tests for KnowledgeNodeORM + IngestionJobORM round-trips."""
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import select

from atlas_core.db.orm import (
    IngestionJobORM,
    KnowledgeNodeORM,
    ProjectORM,
)


@pytest.mark.asyncio
async def test_knowledge_node_round_trip(db_session):
    project = ProjectORM(
        user_id="matt",
        name="P",
        default_model="claude-sonnet-4-6",
    )
    db_session.add(project)
    await db_session.flush()

    doc = KnowledgeNodeORM(
        user_id="matt",
        project_id=project.id,
        type="document",
        title="Doc One",
        text="full document text",
        metadata={"source": "test"},
    )
    db_session.add(doc)
    await db_session.flush()

    chunk = KnowledgeNodeORM(
        user_id="matt",
        project_id=project.id,
        type="chunk",
        parent_id=doc.id,
        text="a chunk of the document",
        metadata={"index": 0},
        embedding_id=str(doc.id),
    )
    db_session.add(chunk)
    await db_session.flush()

    rows = (await db_session.execute(select(KnowledgeNodeORM))).scalars().all()
    assert len(rows) == 2
    chunks = [r for r in rows if r.type == "chunk"]
    assert chunks[0].parent_id == doc.id
    assert chunks[0].metadata == {"index": 0}


@pytest.mark.asyncio
async def test_ingestion_job_round_trip(db_session):
    project = ProjectORM(user_id="matt", name="P", default_model="claude-sonnet-4-6")
    db_session.add(project)
    await db_session.flush()

    job = IngestionJobORM(
        user_id="matt",
        project_id=project.id,
        source_type="markdown",
        source_filename="notes.md",
        status="pending",
    )
    db_session.add(job)
    await db_session.flush()

    job.status = "completed"
    job.completed_at = datetime.now(UTC)
    job.node_ids = [str(uuid4()), str(uuid4())]
    await db_session.flush()

    fetched = (await db_session.execute(select(IngestionJobORM))).scalar_one()
    assert fetched.status == "completed"
    assert len(fetched.node_ids) == 2
```

Run: `uv run pytest packages/atlas-core/atlas_core/tests/test_db_orm_knowledge.py -v`
Expected: ImportError on `KnowledgeNodeORM`/`IngestionJobORM`.

- [ ] **Step 2: Append ORM models**

In `packages/atlas-core/atlas_core/db/orm.py`, append at the end (preserve existing classes):

```python
class KnowledgeNodeORM(Base):
    __tablename__ = "knowledge_nodes"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=sa.text("gen_random_uuid()"),
    )
    user_id: Mapped[str] = mapped_column(sa.Text, nullable=False)
    project_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        sa.ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
    )
    type: Mapped[str] = mapped_column(sa.Text, nullable=False)  # document | chunk
    parent_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        sa.ForeignKey("knowledge_nodes.id", ondelete="CASCADE"),
        nullable=True,
    )
    title: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    text: Mapped[str] = mapped_column(sa.Text, nullable=False)
    # Python attribute "metadata" collides with DeclarativeBase.metadata; rename in Python.
    metadata: Mapped[dict] = mapped_column(
        "metadata",
        JSONB,
        nullable=False,
        server_default=sa.text("'{}'::jsonb"),
    )
    embedding_id: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        sa.TIMESTAMP(timezone=True),
        nullable=False,
        server_default=sa.text("NOW()"),
    )


class IngestionJobORM(Base):
    __tablename__ = "ingestion_jobs"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=sa.text("gen_random_uuid()"),
    )
    user_id: Mapped[str] = mapped_column(sa.Text, nullable=False)
    project_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        sa.ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
    )
    source_type: Mapped[str] = mapped_column(sa.Text, nullable=False)  # pdf | markdown
    source_filename: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    status: Mapped[str] = mapped_column(
        sa.Text, nullable=False, server_default=sa.text("'pending'")
    )
    node_ids: Mapped[list] = mapped_column(
        JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")
    )
    error: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        sa.TIMESTAMP(timezone=True),
        nullable=False,
        server_default=sa.text("NOW()"),
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        sa.TIMESTAMP(timezone=True), nullable=True
    )
```

(The `metadata: Mapped[dict] = mapped_column("metadata", ...)` form: the Python attribute is `metadata` but SQLAlchemy's DeclarativeBase reserves that attribute name. Resolution: in SA 2.x you CAN use `metadata` as a column attribute on a mapped class — it shadows the class-level `metadata` attribute on instances but doesn't conflict because instance access goes through `__dict__`. Verify the test passes; if SA complains, rename the Python attr to `metadata_` and pass `name="metadata"` via the `mapped_column` first positional arg. Either way the SQL column is `metadata`.)

- [ ] **Step 3: Run tests**

Run: `uv run pytest packages/atlas-core/atlas_core/tests/test_db_orm_knowledge.py -v`
Expected: 2 passed.

If SA complains about the `metadata` attribute, change the Python attr to `metadata_` AND update the test's `metadata={"source":"test"}` kwarg usage to `metadata_={"source":"test"}`, then re-run.

- [ ] **Step 4: Commit**

```bash
git add packages/atlas-core/atlas_core/db/orm.py packages/atlas-core/atlas_core/tests/test_db_orm_knowledge.py
git commit -m "feat(atlas-core): add KnowledgeNodeORM + IngestionJobORM"
```

---

## Task 4: Add Pydantic models — `KnowledgeNode` + `KnowledgeNodeType` (TDD)

**Files:**
- Create: `packages/atlas-knowledge/atlas_knowledge/models/__init__.py`
- Create: `packages/atlas-knowledge/atlas_knowledge/models/nodes.py`
- Create: `packages/atlas-knowledge/atlas_knowledge/tests/test_models_nodes.py`

- [ ] **Step 1: Write failing tests**

`packages/atlas-knowledge/atlas_knowledge/tests/test_models_nodes.py`:
```python
"""Tests for atlas_knowledge.models.nodes."""
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError

from atlas_knowledge.models.nodes import KnowledgeNode, KnowledgeNodeType


def test_node_type_values():
    assert KnowledgeNodeType.DOCUMENT == "document"
    assert KnowledgeNodeType.CHUNK == "chunk"


def test_document_node_construction():
    n = KnowledgeNode(
        id=uuid4(),
        user_id="matt",
        project_id=uuid4(),
        type=KnowledgeNodeType.DOCUMENT,
        title="Notes",
        text="hello",
        metadata={"source": "test"},
        created_at=datetime.now(UTC),
    )
    assert n.parent_id is None
    assert n.embedding_id is None


def test_chunk_node_requires_parent_in_practice():
    """Schema does not enforce parent_id, but chunks should have one in practice."""
    chunk = KnowledgeNode(
        id=uuid4(),
        user_id="matt",
        project_id=uuid4(),
        type=KnowledgeNodeType.CHUNK,
        parent_id=uuid4(),
        text="chunk text",
        metadata={"index": 0},
        embedding_id="emb-1",
        created_at=datetime.now(UTC),
    )
    assert chunk.type is KnowledgeNodeType.CHUNK
    assert chunk.parent_id is not None


def test_node_text_required():
    with pytest.raises(ValidationError):
        KnowledgeNode(
            id=uuid4(),
            user_id="matt",
            project_id=uuid4(),
            type=KnowledgeNodeType.DOCUMENT,
            created_at=datetime.now(UTC),
        )  # missing text


def test_node_metadata_defaults_empty_dict():
    n = KnowledgeNode(
        id=uuid4(),
        user_id="matt",
        project_id=uuid4(),
        type=KnowledgeNodeType.DOCUMENT,
        text="x",
        created_at=datetime.now(UTC),
    )
    assert n.metadata == {}
```

Run: `uv run pytest packages/atlas-knowledge/atlas_knowledge/tests/test_models_nodes.py -v`
Expected: ImportError.

- [ ] **Step 2: Implement `nodes.py`**

`packages/atlas-knowledge/atlas_knowledge/models/nodes.py`:
```python
"""Pydantic models for knowledge nodes (documents and chunks)."""
from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import Field

from atlas_core.models.base import AtlasModel


class KnowledgeNodeType(StrEnum):
    DOCUMENT = "document"
    CHUNK = "chunk"


class KnowledgeNode(AtlasModel):
    """A node in the knowledge graph — either a parsed document or one of its chunks."""

    id: UUID
    user_id: str
    project_id: UUID
    type: KnowledgeNodeType
    parent_id: UUID | None = None     # set on chunks; references the document
    title: str | None = None          # populated on documents (filename / heading)
    text: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    embedding_id: str | None = None   # vector store ID for chunks; None for documents
    created_at: datetime
```

- [ ] **Step 3: Create package `__init__.py`**

`packages/atlas-knowledge/atlas_knowledge/models/__init__.py`:
```python
"""Pydantic models for the knowledge layer."""

from atlas_knowledge.models.nodes import KnowledgeNode, KnowledgeNodeType

__all__ = ["KnowledgeNode", "KnowledgeNodeType"]
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest packages/atlas-knowledge/atlas_knowledge/tests/test_models_nodes.py -v`
Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add packages/atlas-knowledge/atlas_knowledge/models/ packages/atlas-knowledge/atlas_knowledge/tests/test_models_nodes.py
git commit -m "feat(atlas-knowledge): add KnowledgeNode + KnowledgeNodeType Pydantic models"
```

---

## Task 5: Add retrieval + ingestion + embedding Pydantic models (TDD)

**Files:**
- Create: `packages/atlas-knowledge/atlas_knowledge/models/embeddings.py`
- Create: `packages/atlas-knowledge/atlas_knowledge/models/retrieval.py`
- Create: `packages/atlas-knowledge/atlas_knowledge/models/ingestion.py`
- Create: `packages/atlas-knowledge/atlas_knowledge/tests/test_models_retrieval.py`
- Modify: `packages/atlas-knowledge/atlas_knowledge/models/__init__.py`

- [ ] **Step 1: Write failing tests**

`packages/atlas-knowledge/atlas_knowledge/tests/test_models_retrieval.py`:
```python
"""Tests for retrieval/ingestion/embedding model shapes."""
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError

from atlas_knowledge.models.embeddings import EmbeddingRequest, EmbeddingResult
from atlas_knowledge.models.ingestion import (
    IngestionJob,
    IngestionStatus,
    IngestRequest,
    SourceType,
)
from atlas_knowledge.models.nodes import KnowledgeNode, KnowledgeNodeType
from atlas_knowledge.models.retrieval import (
    RagContext,
    RetrievalQuery,
    RetrievalResult,
    ScoredChunk,
)


def test_embedding_request_basic():
    req = EmbeddingRequest(texts=["a", "b", "c"])
    assert req.texts == ["a", "b", "c"]


def test_embedding_request_rejects_empty_list():
    with pytest.raises(ValidationError):
        EmbeddingRequest(texts=[])


def test_embedding_result_dimensions_consistent():
    r = EmbeddingResult(vectors=[[0.1, 0.2], [0.3, 0.4]], model_id="bge-small")
    assert len(r.vectors) == 2
    assert r.model_id == "bge-small"


def test_retrieval_query_defaults():
    q = RetrievalQuery(project_id=uuid4(), text="what is X?")
    assert q.top_k == 8


def test_retrieval_query_top_k_bounds():
    pid = uuid4()
    with pytest.raises(ValidationError):
        RetrievalQuery(project_id=pid, text="x", top_k=0)
    with pytest.raises(ValidationError):
        RetrievalQuery(project_id=pid, text="x", top_k=33)


def test_scored_chunk_construction():
    chunk = KnowledgeNode(
        id=uuid4(),
        user_id="matt",
        project_id=uuid4(),
        type=KnowledgeNodeType.CHUNK,
        parent_id=uuid4(),
        text="some chunk",
        created_at=datetime.now(UTC),
    )
    sc = ScoredChunk(chunk=chunk, score=0.87, parent_title="Doc")
    assert sc.score == pytest.approx(0.87)


def test_retrieval_result_round_trip():
    chunk = KnowledgeNode(
        id=uuid4(),
        user_id="matt",
        project_id=uuid4(),
        type=KnowledgeNodeType.CHUNK,
        parent_id=uuid4(),
        text="x",
        created_at=datetime.now(UTC),
    )
    res = RetrievalResult(query="q", chunks=[ScoredChunk(chunk=chunk, score=0.5)])
    assert len(res.chunks) == 1


def test_rag_context_assemble():
    ctx = RagContext(rendered="...prompt context...", citations=[{"title": "Doc", "score": 0.5}])
    assert "prompt context" in ctx.rendered


def test_ingestion_status_values():
    assert IngestionStatus.PENDING == "pending"
    assert IngestionStatus.RUNNING == "running"
    assert IngestionStatus.COMPLETED == "completed"
    assert IngestionStatus.FAILED == "failed"


def test_source_type_values():
    assert SourceType.MARKDOWN == "markdown"
    assert SourceType.PDF == "pdf"


def test_ingest_request_markdown_minimal():
    r = IngestRequest.model_validate({"project_id": str(uuid4()), "source_type": "markdown", "text": "# hello"})
    assert r.source_type is SourceType.MARKDOWN


def test_ingest_request_requires_payload():
    pid = str(uuid4())
    # Markdown without text or filename → invalid
    with pytest.raises(ValidationError):
        IngestRequest.model_validate({"project_id": pid, "source_type": "markdown"})


def test_ingestion_job_construction():
    job = IngestionJob(
        id=uuid4(),
        user_id="matt",
        project_id=uuid4(),
        source_type=SourceType.MARKDOWN,
        source_filename="notes.md",
        status=IngestionStatus.PENDING,
        node_ids=[],
        created_at=datetime.now(UTC),
    )
    assert job.completed_at is None
```

Run: `uv run pytest packages/atlas-knowledge/atlas_knowledge/tests/test_models_retrieval.py -v`
Expected: ImportError.

- [ ] **Step 2: Implement `embeddings.py`**

`packages/atlas-knowledge/atlas_knowledge/models/embeddings.py`:
```python
"""Embedding service request/response shapes."""
from pydantic import Field

from atlas_core.models.base import AtlasModel


class EmbeddingRequest(AtlasModel):
    """Batch of texts to embed."""

    texts: list[str] = Field(min_length=1)


class EmbeddingResult(AtlasModel):
    """Embedding vectors returned by an EmbeddingService.

    ``vectors[i]`` is the embedding for ``texts[i]`` of the originating
    request — caller-side correlation, no IDs in the result type.
    """

    vectors: list[list[float]]
    model_id: str
```

- [ ] **Step 3: Implement `retrieval.py`**

`packages/atlas-knowledge/atlas_knowledge/models/retrieval.py`:
```python
"""Retrieval query/result shapes."""
from typing import Any
from uuid import UUID

from pydantic import Field

from atlas_core.models.base import AtlasModel
from atlas_knowledge.models.nodes import KnowledgeNode


class RetrievalQuery(AtlasModel):
    """One RAG query — embed → vector search → ScoredChunk[]."""

    project_id: UUID
    text: str = Field(min_length=1)
    top_k: int = Field(default=8, ge=1, le=32)
    filter: dict[str, Any] | None = None  # extra metadata filter passed to the store


class ScoredChunk(AtlasModel):
    """A single chunk + similarity score + denormalized parent title."""

    chunk: KnowledgeNode
    score: float
    parent_title: str | None = None


class RetrievalResult(AtlasModel):
    """Bundle returned by Retriever.retrieve()."""

    query: str
    chunks: list[ScoredChunk]


class RagContext(AtlasModel):
    """Renderable bundle injected into the system prompt (Plan 5 wires this in)."""

    rendered: str                     # the prompt-ready text block
    citations: list[dict[str, Any]]   # parallel list of metadata for the UI
```

- [ ] **Step 4: Implement `ingestion.py`**

`packages/atlas-knowledge/atlas_knowledge/models/ingestion.py`:
```python
"""Ingestion request + job-state shapes."""
from datetime import datetime
from enum import StrEnum
from uuid import UUID

from pydantic import Field, model_validator

from atlas_core.models.base import AtlasModel, AtlasRequestModel


class SourceType(StrEnum):
    MARKDOWN = "markdown"
    PDF = "pdf"


class IngestionStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class IngestRequest(AtlasRequestModel):
    """Payload for POST /api/v1/knowledge/ingest (text/markdown path).

    For PDF uploads the API uses multipart form, not this model — a separate
    handler reads the bytes and calls the service directly.
    """

    project_id: UUID
    source_type: SourceType
    text: str | None = Field(default=None, max_length=2_000_000)
    source_filename: str | None = None

    @model_validator(mode="after")
    def _require_text_or_filename(self) -> "IngestRequest":
        if self.source_type is SourceType.MARKDOWN and not self.text:
            raise ValueError("markdown ingest requires non-empty `text`")
        return self


class IngestionJob(AtlasModel):
    """Persisted ingestion job state (mirrors IngestionJobORM)."""

    id: UUID
    user_id: str
    project_id: UUID
    source_type: SourceType
    source_filename: str | None = None
    status: IngestionStatus
    node_ids: list[UUID] = Field(default_factory=list)
    error: str | None = None
    created_at: datetime
    completed_at: datetime | None = None
```

- [ ] **Step 5: Update `models/__init__.py`**

Replace contents:
```python
"""Pydantic models for the knowledge layer."""

from atlas_knowledge.models.embeddings import EmbeddingRequest, EmbeddingResult
from atlas_knowledge.models.ingestion import (
    IngestionJob,
    IngestionStatus,
    IngestRequest,
    SourceType,
)
from atlas_knowledge.models.nodes import KnowledgeNode, KnowledgeNodeType
from atlas_knowledge.models.retrieval import (
    RagContext,
    RetrievalQuery,
    RetrievalResult,
    ScoredChunk,
)

__all__ = [
    "EmbeddingRequest",
    "EmbeddingResult",
    "IngestRequest",
    "IngestionJob",
    "IngestionStatus",
    "KnowledgeNode",
    "KnowledgeNodeType",
    "RagContext",
    "RetrievalQuery",
    "RetrievalResult",
    "ScoredChunk",
    "SourceType",
]
```

- [ ] **Step 6: Run tests**

Run: `uv run pytest packages/atlas-knowledge/atlas_knowledge/tests/test_models_retrieval.py -v`
Expected: 13 passed.

- [ ] **Step 7: Commit**

```bash
git add packages/atlas-knowledge/atlas_knowledge/models/ packages/atlas-knowledge/atlas_knowledge/tests/test_models_retrieval.py
git commit -m "feat(atlas-knowledge): add embedding/retrieval/ingestion Pydantic models"
```

---

## Task 6: Append ORM converters

**Files:**
- Modify: `packages/atlas-core/atlas_core/db/converters.py`
- Modify: `packages/atlas-core/atlas_core/tests/test_db_converters.py`

- [ ] **Step 1: Update test file (append two tests)**

Add to `packages/atlas-core/atlas_core/tests/test_db_converters.py`:
```python
def _build_knowledge_node_row() -> "KnowledgeNodeORM":
    from atlas_core.db.orm import KnowledgeNodeORM
    return KnowledgeNodeORM(
        id=uuid4(),
        user_id="matt",
        project_id=uuid4(),
        type="chunk",
        parent_id=uuid4(),
        title=None,
        text="chunk text",
        metadata={"index": 0},
        embedding_id="emb-1",
        created_at=datetime.now(timezone.utc),
    )


def _build_ingestion_job_row() -> "IngestionJobORM":
    from atlas_core.db.orm import IngestionJobORM
    return IngestionJobORM(
        id=uuid4(),
        user_id="matt",
        project_id=uuid4(),
        source_type="markdown",
        source_filename="notes.md",
        status="completed",
        node_ids=[str(uuid4())],
        error=None,
        created_at=datetime.now(timezone.utc),
        completed_at=datetime.now(timezone.utc),
    )


def test_knowledge_node_from_orm():
    from atlas_core.db.converters import knowledge_node_from_orm
    from atlas_knowledge.models.nodes import KnowledgeNodeType
    row = _build_knowledge_node_row()
    n = knowledge_node_from_orm(row)
    assert n.type is KnowledgeNodeType.CHUNK
    assert n.metadata == {"index": 0}
    assert n.embedding_id == "emb-1"


def test_ingestion_job_from_orm():
    from atlas_core.db.converters import ingestion_job_from_orm
    from atlas_knowledge.models.ingestion import IngestionStatus, SourceType
    row = _build_ingestion_job_row()
    job = ingestion_job_from_orm(row)
    assert job.status is IngestionStatus.COMPLETED
    assert job.source_type is SourceType.MARKDOWN
    assert len(job.node_ids) == 1
```

(`atlas-knowledge` is already an installable workspace member — atlas-core test code can import from it for converter logic. Keep the imports inside the test functions to avoid making atlas-core a hard dependency on atlas-knowledge at import time.)

Run: `uv run pytest packages/atlas-core/atlas_core/tests/test_db_converters.py -v`
Expected: ImportError on `knowledge_node_from_orm`/`ingestion_job_from_orm`.

- [ ] **Step 2: Append converters**

In `packages/atlas-core/atlas_core/db/converters.py`, add imports at top (alphabetized inside their group):
```python
from atlas_core.db.orm import IngestionJobORM, KnowledgeNodeORM, MessageORM, ProjectORM, SessionORM
```

Replace the existing `from atlas_core.db.orm` line. Then append at the end:
```python
def knowledge_node_from_orm(row: KnowledgeNodeORM):
    """Convert KnowledgeNodeORM → KnowledgeNode (Pydantic). Imports are local
    to avoid making atlas-core depend on atlas-knowledge at import time."""
    from atlas_knowledge.models.nodes import KnowledgeNode, KnowledgeNodeType
    return KnowledgeNode(
        id=row.id,
        user_id=row.user_id,
        project_id=row.project_id,
        type=KnowledgeNodeType(row.type),
        parent_id=row.parent_id,
        title=row.title,
        text=row.text,
        metadata=dict(row.metadata or {}),
        embedding_id=row.embedding_id,
        created_at=row.created_at,
    )


def ingestion_job_from_orm(row: IngestionJobORM):
    from atlas_knowledge.models.ingestion import IngestionJob, IngestionStatus, SourceType
    from uuid import UUID
    return IngestionJob(
        id=row.id,
        user_id=row.user_id,
        project_id=row.project_id,
        source_type=SourceType(row.source_type),
        source_filename=row.source_filename,
        status=IngestionStatus(row.status),
        node_ids=[UUID(s) for s in (row.node_ids or [])],
        error=row.error,
        created_at=row.created_at,
        completed_at=row.completed_at,
    )
```

- [ ] **Step 3: Run tests**

Run: `uv run pytest packages/atlas-core/atlas_core/tests/test_db_converters.py -v`
Expected: 5 passed (3 existing + 2 new).

- [ ] **Step 4: Commit**

```bash
git add packages/atlas-core/atlas_core/db/converters.py packages/atlas-core/atlas_core/tests/test_db_converters.py
git commit -m "feat(atlas-core): add knowledge_node + ingestion_job ORM converters"
```

---

## Task 7: `EmbeddingService` ABC + `FakeEmbedder` (TDD)

**Files:**
- Create: `packages/atlas-knowledge/atlas_knowledge/embeddings/__init__.py`
- Create: `packages/atlas-knowledge/atlas_knowledge/embeddings/service.py`
- Create: `packages/atlas-knowledge/atlas_knowledge/embeddings/providers/__init__.py`
- Create: `packages/atlas-knowledge/atlas_knowledge/embeddings/providers/_fake.py`
- Create: `packages/atlas-knowledge/atlas_knowledge/tests/test_embeddings_fake.py`

- [ ] **Step 1: Write failing tests**

`packages/atlas-knowledge/atlas_knowledge/tests/test_embeddings_fake.py`:
```python
"""Tests for the EmbeddingService ABC + FakeEmbedder used downstream."""
import pytest

from atlas_knowledge.embeddings import EmbeddingService, FakeEmbedder


def test_embedding_service_is_abstract():
    with pytest.raises(TypeError):
        EmbeddingService()  # type: ignore[abstract]


async def test_fake_embedder_embeds_documents():
    e = FakeEmbedder(dim=16)
    vectors = await e.embed_documents(["hello", "world", "hello"])
    assert len(vectors) == 3
    assert all(len(v) == 16 for v in vectors)
    # Same input → same output (deterministic)
    assert vectors[0] == vectors[2]


async def test_fake_embedder_embeds_query_consistent_with_documents():
    e = FakeEmbedder(dim=16)
    [doc_vec] = await e.embed_documents(["hello"])
    query_vec = await e.embed_query("hello")
    assert doc_vec == query_vec


async def test_fake_embedder_dim_default():
    e = FakeEmbedder()
    [v] = await e.embed_documents(["x"])
    assert len(v) == 16  # default dim


async def test_fake_embedder_model_id():
    e = FakeEmbedder()
    assert e.model_id == "fake-embedder"
```

Run: `uv run pytest packages/atlas-knowledge/atlas_knowledge/tests/test_embeddings_fake.py -v`
Expected: ImportError.

- [ ] **Step 2: Implement `service.py`**

`packages/atlas-knowledge/atlas_knowledge/embeddings/service.py`:
```python
"""EmbeddingService ABC.

Concrete implementations live in providers/. Phase 1 ships:
- ``SentenceTransformersEmbedder`` (BGE-small, in-process)
- ``FakeEmbedder`` (tests)
"""
from abc import ABC, abstractmethod


class EmbeddingService(ABC):
    """Async embedding interface."""

    model_id: str  # set by subclass __init__

    @abstractmethod
    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """Batch-embed input texts. Output index matches input index."""

    @abstractmethod
    async def embed_query(self, text: str) -> list[float]:
        """Embed a single query string. May share the model with embed_documents
        but some providers prepend a query-specific prefix (BGE: "Represent this
        sentence for searching: "). Implementations document their prefix."""
```

- [ ] **Step 3: Implement `providers/_fake.py`**

`packages/atlas-knowledge/atlas_knowledge/embeddings/providers/_fake.py`:
```python
"""FakeEmbedder — deterministic hash-based vectors for tests.

NOT semantic; it only guarantees that identical inputs produce identical
outputs and the dimension is consistent. Useful for unit-testing the
ingestion pipeline + vector store without downloading BGE-small.
"""
import hashlib

from atlas_knowledge.embeddings.service import EmbeddingService


class FakeEmbedder(EmbeddingService):
    """Hash a string → bytes → 16 floats in [-1, 1]."""

    def __init__(self, dim: int = 16) -> None:
        self.dim = dim
        self.model_id = "fake-embedder"

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._hash_to_vector(t) for t in texts]

    async def embed_query(self, text: str) -> list[float]:
        return self._hash_to_vector(text)

    def _hash_to_vector(self, text: str) -> list[float]:
        # SHA-256 → 32 bytes → repeat/truncate to ``dim`` bytes → scale to [-1, 1].
        digest = hashlib.sha256(text.encode("utf-8")).digest()
        vec_bytes = (digest * ((self.dim // len(digest)) + 1))[: self.dim]
        return [(b - 128) / 128.0 for b in vec_bytes]
```

- [ ] **Step 4: Wire up `__init__.py` files**

`packages/atlas-knowledge/atlas_knowledge/embeddings/providers/__init__.py`:
```python
"""Concrete embedding providers."""

from atlas_knowledge.embeddings.providers._fake import FakeEmbedder

__all__ = ["FakeEmbedder"]
```

`packages/atlas-knowledge/atlas_knowledge/embeddings/__init__.py`:
```python
"""Embedding service abstraction + concrete providers."""

from atlas_knowledge.embeddings.providers import FakeEmbedder
from atlas_knowledge.embeddings.service import EmbeddingService

__all__ = ["EmbeddingService", "FakeEmbedder"]
```

- [ ] **Step 5: Run tests**

Run: `uv run pytest packages/atlas-knowledge/atlas_knowledge/tests/test_embeddings_fake.py -v`
Expected: 5 passed.

- [ ] **Step 6: Commit**

```bash
git add packages/atlas-knowledge/atlas_knowledge/embeddings/ packages/atlas-knowledge/atlas_knowledge/tests/test_embeddings_fake.py
git commit -m "feat(atlas-knowledge): add EmbeddingService ABC + FakeEmbedder"
```

---

## Task 8: `SentenceTransformersEmbedder` (BGE-small)

**Files:**
- Create: `packages/atlas-knowledge/atlas_knowledge/embeddings/providers/local.py`
- Modify: `packages/atlas-knowledge/atlas_knowledge/embeddings/providers/__init__.py`
- Modify: `packages/atlas-knowledge/atlas_knowledge/embeddings/__init__.py`

**Note:** No unit test downloads BGE-small. The implementation is exercised by Task 17's live smoke. We test only the interface (`isinstance(EmbeddingService)`, `model_id`, `dim` attribute).

- [ ] **Step 1: Implement `local.py`**

`packages/atlas-knowledge/atlas_knowledge/embeddings/providers/local.py`:
```python
"""SentenceTransformersEmbedder — wraps BAAI/bge-small-en-v1.5.

Loaded lazily into a process-wide cache on first call. Sync model
inference is wrapped in ``anyio.to_thread.run_sync`` to keep the
event loop responsive.

BGE convention: queries get the prefix
``"Represent this sentence for searching relevant passages: "`` so
similarity scores cluster correctly. Documents are embedded as-is.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

import anyio.to_thread

from atlas_knowledge.embeddings.service import EmbeddingService

if TYPE_CHECKING:
    from sentence_transformers import SentenceTransformer

DEFAULT_MODEL = "BAAI/bge-small-en-v1.5"
QUERY_PREFIX = "Represent this sentence for searching relevant passages: "

_MODEL_CACHE: dict[str, "SentenceTransformer"] = {}


def _get_model(model_name: str) -> "SentenceTransformer":
    if model_name not in _MODEL_CACHE:
        # Imported lazily — only when actually needed, so test runs that
        # never instantiate this class don't pay the import cost (~2s).
        from sentence_transformers import SentenceTransformer
        _MODEL_CACHE[model_name] = SentenceTransformer(model_name)
    return _MODEL_CACHE[model_name]


class SentenceTransformersEmbedder(EmbeddingService):
    """In-process embedder using sentence-transformers."""

    def __init__(
        self,
        model_name: str = DEFAULT_MODEL,
        *,
        batch_size: int = 32,
    ) -> None:
        self.model_id = model_name
        self.batch_size = batch_size
        self._model_name = model_name

    @property
    def dim(self) -> int:
        return _get_model(self._model_name).get_sentence_embedding_dimension()

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        def _encode() -> list[list[float]]:
            model = _get_model(self._model_name)
            arr = model.encode(
                texts,
                batch_size=self.batch_size,
                normalize_embeddings=True,
                show_progress_bar=False,
            )
            return arr.tolist()

        return await anyio.to_thread.run_sync(_encode)

    async def embed_query(self, text: str) -> list[float]:
        prefixed = QUERY_PREFIX + text

        def _encode() -> list[float]:
            model = _get_model(self._model_name)
            arr = model.encode(
                [prefixed],
                normalize_embeddings=True,
                show_progress_bar=False,
            )
            return arr[0].tolist()

        return await anyio.to_thread.run_sync(_encode)
```

- [ ] **Step 2: Update `providers/__init__.py`**

```python
"""Concrete embedding providers."""

from atlas_knowledge.embeddings.providers._fake import FakeEmbedder
from atlas_knowledge.embeddings.providers.local import (
    DEFAULT_MODEL,
    SentenceTransformersEmbedder,
)

__all__ = ["DEFAULT_MODEL", "FakeEmbedder", "SentenceTransformersEmbedder"]
```

- [ ] **Step 3: Update `embeddings/__init__.py`**

```python
"""Embedding service abstraction + concrete providers."""

from atlas_knowledge.embeddings.providers import (
    DEFAULT_MODEL,
    FakeEmbedder,
    SentenceTransformersEmbedder,
)
from atlas_knowledge.embeddings.service import EmbeddingService

__all__ = [
    "DEFAULT_MODEL",
    "EmbeddingService",
    "FakeEmbedder",
    "SentenceTransformersEmbedder",
]
```

- [ ] **Step 4: Smoke import**

Run:
```bash
uv run python -c "from atlas_knowledge.embeddings import SentenceTransformersEmbedder, FakeEmbedder; print('ok')"
```
Expected: `ok` (does NOT load the model; lazy).

- [ ] **Step 5: Commit**

```bash
git add packages/atlas-knowledge/atlas_knowledge/embeddings/providers/local.py packages/atlas-knowledge/atlas_knowledge/embeddings/providers/__init__.py packages/atlas-knowledge/atlas_knowledge/embeddings/__init__.py
git commit -m "feat(atlas-knowledge): add SentenceTransformersEmbedder (BGE-small, lazy load)"
```

---

## Task 9: `VectorStore` ABC

**Files:**
- Create: `packages/atlas-knowledge/atlas_knowledge/vector/__init__.py`
- Create: `packages/atlas-knowledge/atlas_knowledge/vector/store.py`

- [ ] **Step 1: Implement `store.py`**

`packages/atlas-knowledge/atlas_knowledge/vector/store.py`:
```python
"""VectorStore ABC."""
from abc import ABC, abstractmethod
from typing import Any
from uuid import UUID

from atlas_knowledge.models.nodes import KnowledgeNode
from atlas_knowledge.models.retrieval import ScoredChunk


class VectorStore(ABC):
    """Async vector store interface — chunk-only.

    Documents are persisted to ``KnowledgeNodeORM`` (Postgres) but never
    enter the vector store. Only chunks (which carry semantic content of
    a fixed size) are embedded and indexed here.
    """

    @abstractmethod
    async def upsert(
        self,
        chunks: list[KnowledgeNode],
        embeddings: list[list[float]],
    ) -> None:
        """Insert or update chunks. ``embeddings[i]`` is the vector for ``chunks[i]``."""

    @abstractmethod
    async def search(
        self,
        query_embedding: list[float],
        top_k: int = 8,
        filter: dict[str, Any] | None = None,
    ) -> list[ScoredChunk]:
        """Return the top-K most similar chunks. ``ScoredChunk.chunk`` is
        hydrated from the vector store's metadata (no DB join required for
        Phase 1 — Plan 5 will hydrate from Postgres for richer fields)."""

    @abstractmethod
    async def delete(self, ids: list[UUID]) -> None:
        """Remove chunks by ID."""
```

- [ ] **Step 2: Create `vector/__init__.py`**

```python
"""Vector store abstraction + concrete implementations."""

from atlas_knowledge.vector.store import VectorStore

__all__ = ["VectorStore"]
```

- [ ] **Step 3: Smoke import**

Run: `uv run python -c "from atlas_knowledge.vector import VectorStore; print('ok')"`
Expected: `ok`.

- [ ] **Step 4: Commit**

```bash
git add packages/atlas-knowledge/atlas_knowledge/vector/
git commit -m "feat(atlas-knowledge): add VectorStore ABC"
```

---

## Task 10: `ChromaVectorStore` (TDD)

**Files:**
- Create: `packages/atlas-knowledge/atlas_knowledge/vector/chroma.py`
- Create: `packages/atlas-knowledge/atlas_knowledge/tests/test_vector_chroma.py`
- Modify: `packages/atlas-knowledge/atlas_knowledge/vector/__init__.py`

- [ ] **Step 1: Write failing tests**

`packages/atlas-knowledge/atlas_knowledge/tests/test_vector_chroma.py`:
```python
"""Tests for ChromaVectorStore — uses tmp_path-backed embedded Chroma."""
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from atlas_knowledge.models.nodes import KnowledgeNode, KnowledgeNodeType
from atlas_knowledge.vector.chroma import ChromaVectorStore


def _chunk(project_id, parent_id, text="x", meta=None):
    return KnowledgeNode(
        id=uuid4(),
        user_id="matt",
        project_id=project_id,
        type=KnowledgeNodeType.CHUNK,
        parent_id=parent_id,
        text=text,
        metadata=meta or {},
        created_at=datetime.now(UTC),
    )


@pytest.fixture
def store(tmp_path):
    return ChromaVectorStore(persist_dir=str(tmp_path), user_id="matt")


async def test_upsert_then_search_returns_chunk(store):
    project_id = uuid4()
    parent_id = uuid4()
    chunk = _chunk(project_id, parent_id, text="hello world")
    await store.upsert([chunk], [[0.1, 0.2, 0.3]])

    results = await store.search(query_embedding=[0.1, 0.2, 0.3], top_k=5)
    assert len(results) == 1
    assert results[0].chunk.id == chunk.id
    assert results[0].chunk.text == "hello world"


async def test_search_respects_project_filter(store):
    proj_a = uuid4()
    proj_b = uuid4()
    parent = uuid4()
    chunk_a = _chunk(proj_a, parent, text="a-text")
    chunk_b = _chunk(proj_b, parent, text="b-text")
    await store.upsert([chunk_a, chunk_b], [[0.1, 0, 0], [0.1, 0, 0]])

    results = await store.search(
        query_embedding=[0.1, 0, 0],
        top_k=5,
        filter={"project_id": str(proj_a)},
    )
    ids = {r.chunk.id for r in results}
    assert ids == {chunk_a.id}


async def test_delete_removes_chunks(store):
    pid = uuid4()
    parent = uuid4()
    chunk = _chunk(pid, parent)
    await store.upsert([chunk], [[0.1, 0.2, 0.3]])
    await store.delete([chunk.id])
    results = await store.search(query_embedding=[0.1, 0.2, 0.3], top_k=5)
    assert results == []


async def test_upsert_dimension_mismatch_raises(store):
    pid = uuid4()
    parent = uuid4()
    a = _chunk(pid, parent)
    b = _chunk(pid, parent)
    with pytest.raises(ValueError):
        await store.upsert([a, b], [[0.1, 0.2]])  # 2 chunks, 1 embedding
```

Run: `uv run pytest packages/atlas-knowledge/atlas_knowledge/tests/test_vector_chroma.py -v`
Expected: ImportError.

- [ ] **Step 2: Implement `chroma.py`**

`packages/atlas-knowledge/atlas_knowledge/vector/chroma.py`:
```python
"""ChromaDB-backed VectorStore (embedded mode)."""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import anyio.to_thread
import chromadb
from chromadb.config import Settings

from atlas_knowledge.models.nodes import KnowledgeNode, KnowledgeNodeType
from atlas_knowledge.models.retrieval import ScoredChunk
from atlas_knowledge.vector.store import VectorStore


class ChromaVectorStore(VectorStore):
    """One Chroma collection per user; project_id stored as item metadata."""

    def __init__(self, persist_dir: str, user_id: str) -> None:
        self._client = chromadb.PersistentClient(
            path=persist_dir,
            settings=Settings(anonymized_telemetry=False),
        )
        # Collection names: ``user_<user_id>`` — Chroma names must be 3–63 chars,
        # start/end alphanumeric. user_id="matt" → "user_matt" satisfies this.
        self._collection = self._client.get_or_create_collection(
            name=f"user_{user_id}",
            metadata={"hnsw:space": "cosine"},
        )

    async def upsert(
        self,
        chunks: list[KnowledgeNode],
        embeddings: list[list[float]],
    ) -> None:
        if len(chunks) != len(embeddings):
            raise ValueError(
                f"upsert length mismatch: {len(chunks)} chunks vs {len(embeddings)} embeddings"
            )
        if not chunks:
            return

        ids = [str(c.id) for c in chunks]
        documents = [c.text for c in chunks]
        metadatas = [
            {
                "project_id": str(c.project_id),
                "user_id": c.user_id,
                "parent_id": str(c.parent_id) if c.parent_id else "",
                "title": c.title or "",
                "created_at": c.created_at.isoformat(),
                **c.metadata,
            }
            for c in chunks
        ]

        def _do_upsert() -> None:
            self._collection.upsert(
                ids=ids,
                embeddings=embeddings,
                documents=documents,
                metadatas=metadatas,
            )

        await anyio.to_thread.run_sync(_do_upsert)

    async def search(
        self,
        query_embedding: list[float],
        top_k: int = 8,
        filter: dict[str, Any] | None = None,
    ) -> list[ScoredChunk]:
        where: dict[str, Any] | None = filter

        def _do_search() -> dict[str, Any]:
            return self._collection.query(
                query_embeddings=[query_embedding],
                n_results=top_k,
                where=where,
            )

        result = await anyio.to_thread.run_sync(_do_search)
        return self._scored_chunks_from_chroma(result)

    async def delete(self, ids: list[UUID]) -> None:
        if not ids:
            return

        str_ids = [str(i) for i in ids]

        def _do_delete() -> None:
            self._collection.delete(ids=str_ids)

        await anyio.to_thread.run_sync(_do_delete)

    @staticmethod
    def _scored_chunks_from_chroma(result: dict[str, Any]) -> list[ScoredChunk]:
        # Chroma returns parallel lists, each wrapped in a 1-element outer list
        # because we always pass exactly one query_embedding.
        if not result.get("ids") or not result["ids"][0]:
            return []
        ids = result["ids"][0]
        documents = result["documents"][0]
        metadatas = result["metadatas"][0]
        # Chroma returns "distances" with cosine = lower is closer.
        # Convert to similarity score: score = 1 - distance.
        distances = result["distances"][0]

        out: list[ScoredChunk] = []
        for chunk_id, doc, meta, dist in zip(ids, documents, metadatas, distances, strict=True):
            chunk = KnowledgeNode(
                id=UUID(chunk_id),
                user_id=meta.get("user_id", ""),
                project_id=UUID(meta["project_id"]),
                type=KnowledgeNodeType.CHUNK,
                parent_id=UUID(meta["parent_id"]) if meta.get("parent_id") else None,
                title=meta.get("title") or None,
                text=doc,
                metadata={
                    k: v for k, v in meta.items()
                    if k not in {"project_id", "user_id", "parent_id", "title", "created_at"}
                },
                embedding_id=chunk_id,
                created_at=_parse_dt(meta.get("created_at")),
            )
            out.append(ScoredChunk(chunk=chunk, score=1.0 - float(dist), parent_title=meta.get("title") or None))
        return out


def _parse_dt(s: str | None) -> datetime:
    if not s:
        return datetime.now(UTC)
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        return datetime.now(UTC)
```

- [ ] **Step 3: Update `vector/__init__.py`**

```python
"""Vector store abstraction + concrete implementations."""

from atlas_knowledge.vector.chroma import ChromaVectorStore
from atlas_knowledge.vector.store import VectorStore

__all__ = ["ChromaVectorStore", "VectorStore"]
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest packages/atlas-knowledge/atlas_knowledge/tests/test_vector_chroma.py -v`
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add packages/atlas-knowledge/atlas_knowledge/vector/ packages/atlas-knowledge/atlas_knowledge/tests/test_vector_chroma.py
git commit -m "feat(atlas-knowledge): add ChromaVectorStore (in-process embedded)"
```

---

## Task 11: Markdown parser (TDD)

**Files:**
- Create: `packages/atlas-knowledge/atlas_knowledge/parsers/__init__.py`
- Create: `packages/atlas-knowledge/atlas_knowledge/parsers/markdown.py`
- Create: `packages/atlas-knowledge/atlas_knowledge/tests/test_parsers_markdown.py`

- [ ] **Step 1: Write failing tests**

`packages/atlas-knowledge/atlas_knowledge/tests/test_parsers_markdown.py`:
```python
"""Tests for markdown parser (passthrough with front-matter strip)."""
from atlas_knowledge.parsers.markdown import ParsedDocument, parse_markdown


def test_parse_markdown_returns_parsed_document():
    doc = parse_markdown("# Hello\n\nbody text here.", title="Notes")
    assert isinstance(doc, ParsedDocument)
    assert doc.title == "Notes"
    assert "Hello" in doc.text
    assert doc.source_type == "markdown"


def test_parse_markdown_uses_first_h1_when_title_unset():
    doc = parse_markdown("# Auto Title\n\nbody")
    assert doc.title == "Auto Title"


def test_parse_markdown_falls_back_to_untitled():
    doc = parse_markdown("no heading here, just text.")
    assert doc.title == "Untitled"


def test_parse_markdown_strips_yaml_front_matter():
    src = """---
slug: foo
date: 2026-04-27
---
# Real Title

body
"""
    doc = parse_markdown(src)
    assert "slug: foo" not in doc.text
    assert doc.title == "Real Title"
```

Run: `uv run pytest packages/atlas-knowledge/atlas_knowledge/tests/test_parsers_markdown.py -v`
Expected: ImportError.

- [ ] **Step 2: Implement `markdown.py`**

`packages/atlas-knowledge/atlas_knowledge/parsers/markdown.py`:
```python
"""Markdown parser — passthrough with simple title extraction."""
import re
from dataclasses import dataclass

_FRONT_MATTER_RE = re.compile(r"\A---\n.*?\n---\n", re.DOTALL)
_FIRST_H1_RE = re.compile(r"^# (.+?)$", re.MULTILINE)


@dataclass(frozen=True)
class ParsedDocument:
    """A parsed source document, ready to feed into the chunker."""

    text: str
    title: str
    source_type: str  # "markdown" | "pdf"
    metadata: dict[str, object]


def parse_markdown(text: str, *, title: str | None = None) -> ParsedDocument:
    """Strip optional YAML front matter, then return the body as-is.

    Title resolution: explicit ``title`` arg → first H1 in the body → "Untitled".
    """
    body = _FRONT_MATTER_RE.sub("", text, count=1)

    resolved_title = title or _extract_first_h1(body) or "Untitled"
    return ParsedDocument(
        text=body,
        title=resolved_title,
        source_type="markdown",
        metadata={},
    )


def _extract_first_h1(body: str) -> str | None:
    m = _FIRST_H1_RE.search(body)
    return m.group(1).strip() if m else None
```

- [ ] **Step 3: Implement `parsers/__init__.py`**

```python
"""Document parsers."""

from atlas_knowledge.parsers.markdown import ParsedDocument, parse_markdown

__all__ = ["ParsedDocument", "parse_markdown"]
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest packages/atlas-knowledge/atlas_knowledge/tests/test_parsers_markdown.py -v`
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add packages/atlas-knowledge/atlas_knowledge/parsers/__init__.py packages/atlas-knowledge/atlas_knowledge/parsers/markdown.py packages/atlas-knowledge/atlas_knowledge/tests/test_parsers_markdown.py
git commit -m "feat(atlas-knowledge): add markdown parser (passthrough with front-matter strip)"
```

---

## Task 12: PDF parser (PyMuPDF)

**Files:**
- Create: `packages/atlas-knowledge/atlas_knowledge/parsers/pdf.py`
- Create: `packages/atlas-knowledge/atlas_knowledge/tests/test_parsers_pdf.py`
- Modify: `packages/atlas-knowledge/atlas_knowledge/parsers/__init__.py`

- [ ] **Step 1: Write failing tests**

`packages/atlas-knowledge/atlas_knowledge/tests/test_parsers_pdf.py`:
```python
"""Tests for the PDF parser using a generated single-page PDF fixture."""
import pytest

from atlas_knowledge.parsers.pdf import parse_pdf


@pytest.fixture
def sample_pdf_bytes() -> bytes:
    """Generate a minimal PDF in-memory with PyMuPDF — no external file needed."""
    import fitz
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), "Hello PDF World.\n\nSecond paragraph.")
    out = doc.tobytes()
    doc.close()
    return out


def test_parse_pdf_extracts_text(sample_pdf_bytes):
    doc = parse_pdf(sample_pdf_bytes, source_filename="hello.pdf")
    assert "Hello PDF World" in doc.text
    assert doc.title == "hello.pdf"
    assert doc.source_type == "pdf"


def test_parse_pdf_uses_filename_if_no_pdf_metadata_title(sample_pdf_bytes):
    doc = parse_pdf(sample_pdf_bytes, source_filename="report-Q3.pdf")
    assert doc.title == "report-Q3.pdf"


def test_parse_pdf_no_filename_falls_back(sample_pdf_bytes):
    doc = parse_pdf(sample_pdf_bytes)
    assert doc.title == "Untitled PDF"
```

Run: `uv run pytest packages/atlas-knowledge/atlas_knowledge/tests/test_parsers_pdf.py -v`
Expected: ImportError on `parse_pdf`.

- [ ] **Step 2: Implement `pdf.py`**

`packages/atlas-knowledge/atlas_knowledge/parsers/pdf.py`:
```python
"""PDF parser using PyMuPDF (``fitz``)."""
import fitz

from atlas_knowledge.parsers.markdown import ParsedDocument


def parse_pdf(data: bytes, *, source_filename: str | None = None) -> ParsedDocument:
    """Extract text from a PDF byte buffer. Joins page text with double newlines."""
    doc = fitz.open(stream=data, filetype="pdf")
    try:
        pages: list[str] = []
        for page in doc:
            pages.append(page.get_text("text"))
        text = "\n\n".join(pages).strip()
        # PDF metadata title is rarely useful; prefer filename, then fallback.
        meta_title = (doc.metadata or {}).get("title") or ""
        title = source_filename or (meta_title.strip() if meta_title.strip() else "Untitled PDF")
        return ParsedDocument(
            text=text,
            title=title,
            source_type="pdf",
            metadata={"page_count": doc.page_count},
        )
    finally:
        doc.close()
```

- [ ] **Step 3: Update `parsers/__init__.py`**

```python
"""Document parsers."""

from atlas_knowledge.parsers.markdown import ParsedDocument, parse_markdown
from atlas_knowledge.parsers.pdf import parse_pdf

__all__ = ["ParsedDocument", "parse_markdown", "parse_pdf"]
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest packages/atlas-knowledge/atlas_knowledge/tests/test_parsers_pdf.py -v`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add packages/atlas-knowledge/atlas_knowledge/parsers/pdf.py packages/atlas-knowledge/atlas_knowledge/parsers/__init__.py packages/atlas-knowledge/atlas_knowledge/tests/test_parsers_pdf.py
git commit -m "feat(atlas-knowledge): add PDF parser (PyMuPDF)"
```

---

## Task 13: `SemanticChunker` (TDD)

**Files:**
- Create: `packages/atlas-knowledge/atlas_knowledge/chunking/__init__.py`
- Create: `packages/atlas-knowledge/atlas_knowledge/chunking/semantic.py`
- Create: `packages/atlas-knowledge/atlas_knowledge/tests/test_chunking_semantic.py`

- [ ] **Step 1: Write failing tests**

`packages/atlas-knowledge/atlas_knowledge/tests/test_chunking_semantic.py`:
```python
"""Tests for SemanticChunker — paragraph + heading aware splitting."""
from atlas_knowledge.chunking.semantic import SemanticChunker


def test_short_text_yields_one_chunk():
    c = SemanticChunker(target_tokens=512, overlap_tokens=128)
    chunks = c.chunk("Just one sentence.")
    assert len(chunks) == 1
    assert chunks[0].text == "Just one sentence."


def test_long_text_yields_multiple_chunks():
    paragraph = "word " * 600  # well over 512 tokens
    c = SemanticChunker(target_tokens=512, overlap_tokens=128)
    chunks = c.chunk(paragraph)
    assert len(chunks) >= 2


def test_chunks_carry_index_and_token_count():
    text = ("paragraph " * 600).strip()
    c = SemanticChunker(target_tokens=512, overlap_tokens=128)
    chunks = c.chunk(text)
    assert all(ch.index == i for i, ch in enumerate(chunks))
    assert all(ch.token_count > 0 for ch in chunks)


def test_chunks_overlap_when_split():
    """The overlap window should reuse some tokens from the previous chunk."""
    text = ("alpha " * 1200).strip()
    c = SemanticChunker(target_tokens=512, overlap_tokens=128)
    chunks = c.chunk(text)
    assert len(chunks) >= 2
    last_words_of_first = chunks[0].text.split()[-50:]
    first_words_of_second = chunks[1].text.split()[:50]
    assert any(w in first_words_of_second for w in last_words_of_first)


def test_paragraph_break_preferred_split():
    """When a paragraph break exists near the budget, prefer splitting there."""
    para_a = "alpha " * 300
    para_b = "beta " * 300
    text = f"{para_a.strip()}\n\n{para_b.strip()}"
    c = SemanticChunker(target_tokens=400, overlap_tokens=50)
    chunks = c.chunk(text)
    # First chunk should be roughly paragraph A (give or take overlap)
    assert "alpha" in chunks[0].text
    assert "beta" in chunks[-1].text
```

Run: `uv run pytest packages/atlas-knowledge/atlas_knowledge/tests/test_chunking_semantic.py -v`
Expected: ImportError.

- [ ] **Step 2: Implement `semantic.py`**

`packages/atlas-knowledge/atlas_knowledge/chunking/semantic.py`:
```python
"""Semantic chunker — whitespace-tokenized sliding window with paragraph snapping.

This is intentionally simple for Phase 1. It uses whitespace word counts as a
cheap proxy for tokens (BGE-small's true tokenizer would be more accurate but
adds 100ms+ overhead per document and a hard dependency on the tokenizer at
chunking time). For ATLAS at single-user scale the approximation is fine; Phase
2 can swap in a real tokenizer if retrieval quality regresses.

Strategy:
1. Split on blank lines into paragraphs (paragraph = atomic unit).
2. Greedily pack paragraphs into windows up to ``target_tokens``.
3. If a single paragraph exceeds the target, split it on word boundaries.
4. Generate ``overlap_tokens`` worth of trailing words from each chunk and
   prepend them to the next.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class Chunk:
    text: str
    index: int
    token_count: int


class SemanticChunker:
    def __init__(self, *, target_tokens: int = 512, overlap_tokens: int = 128) -> None:
        if overlap_tokens >= target_tokens:
            raise ValueError("overlap_tokens must be smaller than target_tokens")
        self.target_tokens = target_tokens
        self.overlap_tokens = overlap_tokens

    def chunk(self, text: str) -> list[Chunk]:
        if not text.strip():
            return []

        words = text.split()
        if len(words) <= self.target_tokens:
            return [Chunk(text=text.strip(), index=0, token_count=len(words))]

        # Paragraph boundaries: index of word AFTER each blank line.
        paragraph_starts = self._paragraph_start_indices(text)

        out: list[Chunk] = []
        start = 0
        idx = 0
        n = len(words)
        while start < n:
            end = min(start + self.target_tokens, n)

            # Snap end to nearest paragraph boundary within [start + 50%, end].
            snap_lo = start + self.target_tokens // 2
            candidates = [b for b in paragraph_starts if snap_lo <= b <= end]
            if candidates:
                end = candidates[-1]

            chunk_words = words[start:end]
            chunk_text = " ".join(chunk_words)
            out.append(Chunk(text=chunk_text, index=idx, token_count=len(chunk_words)))
            idx += 1

            if end >= n:
                break
            start = max(end - self.overlap_tokens, start + 1)

        return out

    @staticmethod
    def _paragraph_start_indices(text: str) -> list[int]:
        """Return word indices that begin a new paragraph (post-blank-line)."""
        starts: list[int] = []
        word_index = 0
        in_blank = False
        for token in text.split("\n"):
            if token.strip() == "":
                in_blank = True
                continue
            if in_blank:
                starts.append(word_index)
                in_blank = False
            word_index += len(token.split())
        return starts
```

- [ ] **Step 3: Implement `chunking/__init__.py`**

```python
"""Document chunkers."""

from atlas_knowledge.chunking.semantic import Chunk, SemanticChunker

__all__ = ["Chunk", "SemanticChunker"]
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest packages/atlas-knowledge/atlas_knowledge/tests/test_chunking_semantic.py -v`
Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add packages/atlas-knowledge/atlas_knowledge/chunking/ packages/atlas-knowledge/atlas_knowledge/tests/test_chunking_semantic.py
git commit -m "feat(atlas-knowledge): add SemanticChunker (paragraph-aware sliding window)"
```

---

## Task 14: `IngestionService` (TDD)

**Files:**
- Create: `packages/atlas-knowledge/atlas_knowledge/ingestion/__init__.py`
- Create: `packages/atlas-knowledge/atlas_knowledge/ingestion/service.py`
- Create: `packages/atlas-knowledge/atlas_knowledge/tests/test_ingestion_service.py`

- [ ] **Step 1: Write failing tests**

`packages/atlas-knowledge/atlas_knowledge/tests/test_ingestion_service.py`:
```python
"""Integration test for IngestionService — uses FakeEmbedder + tmp Chroma."""
from uuid import uuid4

import pytest
from sqlalchemy import select

from atlas_core.db.orm import IngestionJobORM, KnowledgeNodeORM, ProjectORM
from atlas_knowledge.embeddings import FakeEmbedder
from atlas_knowledge.ingestion.service import IngestionService
from atlas_knowledge.parsers.markdown import parse_markdown
from atlas_knowledge.vector.chroma import ChromaVectorStore


@pytest.fixture
async def project_id(db_session):
    p = ProjectORM(user_id="matt", name="P", default_model="claude-sonnet-4-6")
    db_session.add(p)
    await db_session.flush()
    return p.id


@pytest.fixture
def vector_store(tmp_path):
    return ChromaVectorStore(persist_dir=str(tmp_path), user_id="matt")


@pytest.fixture
def service(vector_store):
    return IngestionService(
        embedder=FakeEmbedder(dim=16),
        vector_store=vector_store,
    )


@pytest.mark.asyncio
async def test_ingest_markdown_creates_document_chunks_and_completes_job(
    service, project_id, db_session
):
    parsed = parse_markdown("# Hello\n\n" + ("body word " * 600), title="Hello")
    job_id = await service.ingest(
        db=db_session,
        user_id="matt",
        project_id=project_id,
        parsed=parsed,
        source_type="markdown",
        source_filename="hello.md",
    )

    job = (await db_session.execute(select(IngestionJobORM))).scalar_one()
    assert job.id == job_id
    assert job.status == "completed"
    assert job.completed_at is not None
    assert len(job.node_ids) >= 2  # at least one document + one chunk

    nodes = (await db_session.execute(select(KnowledgeNodeORM))).scalars().all()
    docs = [n for n in nodes if n.type == "document"]
    chunks = [n for n in nodes if n.type == "chunk"]
    assert len(docs) == 1
    assert len(chunks) >= 1
    assert all(c.parent_id == docs[0].id for c in chunks)
    assert docs[0].title == "Hello"


@pytest.mark.asyncio
async def test_ingest_failure_marks_job_failed(service, project_id, db_session):
    """If embedding raises, the job row should be marked failed with error text."""

    class _BoomEmbedder(FakeEmbedder):
        async def embed_documents(self, texts):
            raise RuntimeError("boom")

    bad_service = IngestionService(
        embedder=_BoomEmbedder(),
        vector_store=service._vector_store,  # noqa: SLF001
    )
    parsed = parse_markdown("# X\n\nbody.")
    job_id = await bad_service.ingest(
        db=db_session,
        user_id="matt",
        project_id=project_id,
        parsed=parsed,
        source_type="markdown",
        source_filename=None,
    )
    job = (await db_session.execute(select(IngestionJobORM))).scalar_one()
    assert job.id == job_id
    assert job.status == "failed"
    assert "boom" in (job.error or "")
```

Run: `uv run pytest packages/atlas-knowledge/atlas_knowledge/tests/test_ingestion_service.py -v`
Expected: ImportError.

- [ ] **Step 2: Implement `service.py`**

`packages/atlas-knowledge/atlas_knowledge/ingestion/service.py`:
```python
"""IngestionService — orchestrates parser → chunker → embedder → vector store + DB.

The contract: caller supplies an already-parsed document. This keeps the service
agnostic about the source format (markdown text vs PDF bytes); the API layer
chooses the parser based on content type.
"""
from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from atlas_core.db.orm import IngestionJobORM, KnowledgeNodeORM
from atlas_knowledge.chunking.semantic import SemanticChunker
from atlas_knowledge.embeddings.service import EmbeddingService
from atlas_knowledge.models.nodes import KnowledgeNode, KnowledgeNodeType
from atlas_knowledge.parsers.markdown import ParsedDocument
from atlas_knowledge.vector.store import VectorStore

log = structlog.get_logger("atlas.knowledge.ingest")


class IngestionService:
    def __init__(
        self,
        embedder: EmbeddingService,
        vector_store: VectorStore,
        *,
        chunker: SemanticChunker | None = None,
    ) -> None:
        self._embedder = embedder
        self._vector_store = vector_store
        self._chunker = chunker or SemanticChunker(target_tokens=512, overlap_tokens=128)

    async def ingest(
        self,
        *,
        db: AsyncSession,
        user_id: str,
        project_id: UUID,
        parsed: ParsedDocument,
        source_type: str,             # "markdown" | "pdf"
        source_filename: str | None,
    ) -> UUID:
        """Run the pipeline. Returns the job_id. Always commits a job row,
        even on failure (with status='failed' + error)."""
        job = IngestionJobORM(
            user_id=user_id,
            project_id=project_id,
            source_type=source_type,
            source_filename=source_filename,
            status="running",
        )
        db.add(job)
        await db.flush()
        log.info("ingest.start", job_id=str(job.id), source=source_type)

        try:
            # 1. Persist the document node.
            doc_row = KnowledgeNodeORM(
                user_id=user_id,
                project_id=project_id,
                type="document",
                title=parsed.title,
                text=parsed.text,
                metadata={"source_type": source_type, **parsed.metadata},
            )
            db.add(doc_row)
            await db.flush()

            # 2. Chunk.
            raw_chunks = self._chunker.chunk(parsed.text)
            if not raw_chunks:
                # Edge case: empty document. Job completes with just the doc node.
                job.status = "completed"
                job.completed_at = datetime.now(UTC)
                job.node_ids = [str(doc_row.id)]
                await db.flush()
                return job.id

            # 3. Persist chunk rows (so they get IDs we can use for the vector store).
            chunk_rows: list[KnowledgeNodeORM] = []
            for raw in raw_chunks:
                row = KnowledgeNodeORM(
                    id=uuid4(),
                    user_id=user_id,
                    project_id=project_id,
                    type="chunk",
                    parent_id=doc_row.id,
                    title=parsed.title,
                    text=raw.text,
                    metadata={"index": raw.index, "token_count": raw.token_count},
                )
                db.add(row)
                chunk_rows.append(row)
            await db.flush()

            # 4. Embed + push to vector store.
            embeddings = await self._embedder.embed_documents([r.text for r in chunk_rows])
            chunk_models = [
                KnowledgeNode(
                    id=r.id,
                    user_id=r.user_id,
                    project_id=r.project_id,
                    type=KnowledgeNodeType.CHUNK,
                    parent_id=r.parent_id,
                    title=r.title,
                    text=r.text,
                    metadata=dict(r.metadata or {}),
                    created_at=r.created_at or datetime.now(UTC),
                )
                for r in chunk_rows
            ]
            await self._vector_store.upsert(chunk_models, embeddings)

            # 5. Stamp embedding_id on each chunk row.
            for row in chunk_rows:
                row.embedding_id = str(row.id)
            await db.flush()

            # 6. Mark job complete.
            job.status = "completed"
            job.completed_at = datetime.now(UTC)
            job.node_ids = [str(doc_row.id)] + [str(r.id) for r in chunk_rows]
            await db.flush()
            log.info("ingest.complete", job_id=str(job.id), chunks=len(chunk_rows))
            return job.id

        except Exception as e:
            log.exception("ingest.failed", job_id=str(job.id))
            job.status = "failed"
            job.error = str(e)
            job.completed_at = datetime.now(UTC)
            await db.flush()
            return job.id
```

- [ ] **Step 3: Implement `ingestion/__init__.py`**

```python
"""Ingestion orchestration."""

from atlas_knowledge.ingestion.service import IngestionService

__all__ = ["IngestionService"]
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest packages/atlas-knowledge/atlas_knowledge/tests/test_ingestion_service.py -v`
Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add packages/atlas-knowledge/atlas_knowledge/ingestion/ packages/atlas-knowledge/atlas_knowledge/tests/test_ingestion_service.py
git commit -m "feat(atlas-knowledge): add IngestionService (parser → chunker → embedder → store)"
```

---

## Task 15: `Retriever` (TDD)

**Files:**
- Create: `packages/atlas-knowledge/atlas_knowledge/retrieval/__init__.py`
- Create: `packages/atlas-knowledge/atlas_knowledge/retrieval/retriever.py`
- Create: `packages/atlas-knowledge/atlas_knowledge/tests/test_retriever.py`

- [ ] **Step 1: Write failing tests**

`packages/atlas-knowledge/atlas_knowledge/tests/test_retriever.py`:
```python
"""Tests for Retriever using FakeEmbedder + tmp Chroma."""
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from atlas_knowledge.embeddings import FakeEmbedder
from atlas_knowledge.models.nodes import KnowledgeNode, KnowledgeNodeType
from atlas_knowledge.models.retrieval import RetrievalQuery
from atlas_knowledge.retrieval.retriever import Retriever
from atlas_knowledge.vector.chroma import ChromaVectorStore


@pytest.fixture
def store(tmp_path):
    return ChromaVectorStore(persist_dir=str(tmp_path), user_id="matt")


@pytest.fixture
def retriever(store):
    return Retriever(embedder=FakeEmbedder(dim=16), vector_store=store)


async def _seed(store, embedder, project_id, parent_id, texts):
    chunks = [
        KnowledgeNode(
            id=uuid4(),
            user_id="matt",
            project_id=project_id,
            type=KnowledgeNodeType.CHUNK,
            parent_id=parent_id,
            text=t,
            created_at=datetime.now(UTC),
        )
        for t in texts
    ]
    embeddings = await embedder.embed_documents(texts)
    await store.upsert(chunks, embeddings)
    return chunks


async def test_retrieve_returns_top_k(store, retriever):
    pid = uuid4()
    parent = uuid4()
    chunks = await _seed(store, retriever._embedder, pid, parent, ["foo", "bar", "baz", "foo"])  # noqa: SLF001
    res = await retriever.retrieve(RetrievalQuery(project_id=pid, text="foo", top_k=2))
    assert res.query == "foo"
    assert len(res.chunks) == 2
    assert all(sc.chunk.id in {c.id for c in chunks} for sc in res.chunks)


async def test_retrieve_filters_by_project(store, retriever):
    proj_a = uuid4()
    proj_b = uuid4()
    parent = uuid4()
    await _seed(store, retriever._embedder, proj_a, parent, ["alpha"])  # noqa: SLF001
    await _seed(store, retriever._embedder, proj_b, parent, ["alpha"])  # noqa: SLF001

    res = await retriever.retrieve(RetrievalQuery(project_id=proj_a, text="alpha", top_k=5))
    assert all(sc.chunk.project_id == proj_a for sc in res.chunks)


async def test_retrieve_top_k_default(store, retriever):
    pid = uuid4()
    parent = uuid4()
    await _seed(store, retriever._embedder, pid, parent, [f"text-{i}" for i in range(10)])  # noqa: SLF001
    res = await retriever.retrieve(RetrievalQuery(project_id=pid, text="text-3"))
    assert len(res.chunks) <= 8  # top_k default
```

Run: `uv run pytest packages/atlas-knowledge/atlas_knowledge/tests/test_retriever.py -v`
Expected: ImportError.

- [ ] **Step 2: Implement `retriever.py`**

`packages/atlas-knowledge/atlas_knowledge/retrieval/retriever.py`:
```python
"""Retriever — query → embed → vector search → ScoredChunk[]."""
from atlas_knowledge.embeddings.service import EmbeddingService
from atlas_knowledge.models.retrieval import RetrievalQuery, RetrievalResult
from atlas_knowledge.vector.store import VectorStore


class Retriever:
    """Phase 1 dense-only retriever."""

    def __init__(self, embedder: EmbeddingService, vector_store: VectorStore) -> None:
        self._embedder = embedder
        self._vector_store = vector_store

    async def retrieve(self, query: RetrievalQuery) -> RetrievalResult:
        embedding = await self._embedder.embed_query(query.text)

        filter_dict: dict[str, object] = {"project_id": str(query.project_id)}
        if query.filter:
            filter_dict.update(query.filter)

        scored = await self._vector_store.search(
            query_embedding=embedding,
            top_k=query.top_k,
            filter=filter_dict,
        )
        return RetrievalResult(query=query.text, chunks=scored)
```

- [ ] **Step 3: Implement `retrieval/__init__.py`**

```python
"""Retrieval pipeline."""

from atlas_knowledge.retrieval.retriever import Retriever

__all__ = ["Retriever"]
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest packages/atlas-knowledge/atlas_knowledge/tests/test_retriever.py -v`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add packages/atlas-knowledge/atlas_knowledge/retrieval/ packages/atlas-knowledge/atlas_knowledge/tests/test_retriever.py
git commit -m "feat(atlas-knowledge): add Retriever (query → embed → search)"
```

---

## Task 16: Wire knowledge endpoints into `atlas-api` (TDD)

**Files:**
- Create: `apps/api/atlas_api/routers/knowledge.py`
- Create: `apps/api/atlas_api/tests/test_knowledge_router.py`
- Modify: `apps/api/atlas_api/main.py` (lifespan builds embedder + vector store + ingestion service + retriever; include knowledge router; include atlas-api dep on atlas-knowledge)
- Modify: `apps/api/pyproject.toml` (add `atlas-knowledge` to deps)
- Modify: `apps/api/atlas_api/deps.py` (append `get_ingestion_service`, `get_retriever`)

- [ ] **Step 1: Add atlas-knowledge to atlas-api deps**

In `apps/api/pyproject.toml`, ensure dependencies includes `"atlas-knowledge"`. If missing, add it.

Run: `uv sync --all-packages`
Expected: success.

- [ ] **Step 2: Append to `apps/api/atlas_api/deps.py`**

```python
from atlas_knowledge.ingestion.service import IngestionService
from atlas_knowledge.retrieval.retriever import Retriever


def get_ingestion_service(connection: HTTPConnection) -> IngestionService:
    return connection.app.state.ingestion_service


def get_retriever(connection: HTTPConnection) -> Retriever:
    return connection.app.state.retriever
```

(Place these next to `get_model_router` etc. Keep the existing `HTTPConnection` import.)

- [ ] **Step 3: Modify `apps/api/atlas_api/main.py`**

Add imports:
```python
from atlas_knowledge.embeddings import SentenceTransformersEmbedder
from atlas_knowledge.ingestion.service import IngestionService
from atlas_knowledge.retrieval.retriever import Retriever
from atlas_knowledge.vector.chroma import ChromaVectorStore

from atlas_api.routers import knowledge as knowledge_router
```

In the lifespan (after the model registry build), add:
```python
    embedder = SentenceTransformersEmbedder()
    vector_store = ChromaVectorStore(
        persist_dir=config.db.chroma_path,
        user_id=config.user_id,
    )
    app.state.embedder = embedder
    app.state.vector_store = vector_store
    app.state.ingestion_service = IngestionService(
        embedder=embedder, vector_store=vector_store
    )
    app.state.retriever = Retriever(embedder=embedder, vector_store=vector_store)
```

Add the router:
```python
app.include_router(knowledge_router.router, prefix="/api/v1")
```

(Keep all existing `app.include_router` calls.)

- [ ] **Step 4: Implement `routers/knowledge.py`**

`apps/api/atlas_api/routers/knowledge.py`:
```python
"""Knowledge layer REST endpoints.

POST   /api/v1/knowledge/ingest          Upload markdown text or a PDF (multipart)
GET    /api/v1/knowledge/jobs/{id}       Ingestion job status
GET    /api/v1/knowledge/nodes           List nodes for a project
DELETE /api/v1/knowledge/nodes/{id}      Delete node + chunks
GET    /api/v1/knowledge/search          Debug RAG search
"""
from __future__ import annotations

from typing import Any
from uuid import UUID

from atlas_core.config import AtlasConfig
from atlas_core.db.converters import (
    ingestion_job_from_orm,
    knowledge_node_from_orm,
)
from atlas_core.db.orm import IngestionJobORM, KnowledgeNodeORM
from atlas_knowledge.ingestion.service import IngestionService
from atlas_knowledge.models.ingestion import (
    IngestionJob,
    IngestRequest,
    SourceType,
)
from atlas_knowledge.models.nodes import KnowledgeNode
from atlas_knowledge.models.retrieval import RetrievalQuery, RetrievalResult
from atlas_knowledge.parsers.markdown import parse_markdown
from atlas_knowledge.parsers.pdf import parse_pdf
from atlas_knowledge.retrieval.retriever import Retriever
from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from starlette.requests import HTTPConnection

from atlas_api.deps import (
    get_ingestion_service,
    get_retriever,
    get_session,
    get_settings,
)

router = APIRouter(tags=["knowledge"])


# --- Ingestion -----------------------------------------------------------

@router.post("/knowledge/ingest", response_model=IngestionJob, status_code=202)
async def ingest_endpoint(
    payload: IngestRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_session),
    service: IngestionService = Depends(get_ingestion_service),
    settings: AtlasConfig = Depends(get_settings),
    connection: HTTPConnection = None,  # injected below
) -> IngestionJob:
    """Markdown ingest. PDF goes through ``ingest_pdf_endpoint``."""
    if payload.source_type is not SourceType.MARKDOWN:
        raise HTTPException(
            status_code=400,
            detail="source_type=markdown for this endpoint; use multipart upload for PDFs",
        )
    parsed = parse_markdown(payload.text or "", title=None)

    # Run synchronously here so the response can include the final job state.
    # Phase 4 will move this to Celery; for Phase 1 background task semantics
    # are deferred until the markdown path proves itself.
    job_id = await service.ingest(
        db=db,
        user_id=settings.user_id,
        project_id=payload.project_id,
        parsed=parsed,
        source_type="markdown",
        source_filename=payload.source_filename,
    )
    job_row = await db.get(IngestionJobORM, job_id)
    assert job_row is not None
    return ingestion_job_from_orm(job_row)


@router.post("/knowledge/ingest/pdf", response_model=IngestionJob, status_code=202)
async def ingest_pdf_endpoint(
    project_id: UUID = Form(...),
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_session),
    service: IngestionService = Depends(get_ingestion_service),
    settings: AtlasConfig = Depends(get_settings),
) -> IngestionJob:
    data = await file.read()
    parsed = parse_pdf(data, source_filename=file.filename)
    job_id = await service.ingest(
        db=db,
        user_id=settings.user_id,
        project_id=project_id,
        parsed=parsed,
        source_type="pdf",
        source_filename=file.filename,
    )
    job_row = await db.get(IngestionJobORM, job_id)
    assert job_row is not None
    return ingestion_job_from_orm(job_row)


@router.get("/knowledge/jobs/{job_id}", response_model=IngestionJob)
async def get_job(
    job_id: UUID,
    db: AsyncSession = Depends(get_session),
) -> IngestionJob:
    row = await db.get(IngestionJobORM, job_id)
    if row is None:
        raise HTTPException(status_code=404, detail="ingestion job not found")
    return ingestion_job_from_orm(row)


# --- Nodes ---------------------------------------------------------------

@router.get("/knowledge/nodes", response_model=list[KnowledgeNode])
async def list_nodes(
    project_id: UUID,
    db: AsyncSession = Depends(get_session),
) -> list[KnowledgeNode]:
    result = await db.execute(
        select(KnowledgeNodeORM).where(KnowledgeNodeORM.project_id == project_id)
    )
    return [knowledge_node_from_orm(r) for r in result.scalars().all()]


@router.delete("/knowledge/nodes/{node_id}", status_code=204)
async def delete_node(
    node_id: UUID,
    db: AsyncSession = Depends(get_session),
) -> None:
    row = await db.get(KnowledgeNodeORM, node_id)
    if row is None:
        raise HTTPException(status_code=404, detail="node not found")
    # CASCADE on FK handles child chunks.
    await db.delete(row)
    await db.flush()
    # Vector store cleanup is a Plan 5 concern; chunks remain in Chroma until
    # the next per-project rebuild. Phase 1 doesn't expose a partial-delete on
    # Chroma to keep semantics simple.


# --- Search (debug) ------------------------------------------------------

@router.get("/knowledge/search", response_model=RetrievalResult)
async def search(
    project_id: UUID,
    query: str,
    top_k: int = 8,
    retriever: Retriever = Depends(get_retriever),
) -> RetrievalResult:
    return await retriever.retrieve(
        RetrievalQuery(project_id=project_id, text=query, top_k=top_k)
    )
```

- [ ] **Step 5: Write integration test**

`apps/api/atlas_api/tests/test_knowledge_router.py`:
```python
"""Integration tests for /api/v1/knowledge/* — uses FakeEmbedder + tmp Chroma."""
import pytest
from sqlalchemy import select

from atlas_core.db.orm import KnowledgeNodeORM, ProjectORM
from atlas_knowledge.embeddings import FakeEmbedder
from atlas_knowledge.ingestion.service import IngestionService
from atlas_knowledge.retrieval.retriever import Retriever
from atlas_knowledge.vector.chroma import ChromaVectorStore

from atlas_api.deps import get_ingestion_service, get_retriever
from atlas_api.main import app


@pytest.fixture
def fake_knowledge_stack(tmp_path):
    embedder = FakeEmbedder(dim=16)
    store = ChromaVectorStore(persist_dir=str(tmp_path), user_id="matt")
    return {
        "ingestion": IngestionService(embedder=embedder, vector_store=store),
        "retriever": Retriever(embedder=embedder, vector_store=store),
    }


@pytest.fixture
def app_with_knowledge_overrides(app_client, fake_knowledge_stack):
    app.dependency_overrides[get_ingestion_service] = lambda: fake_knowledge_stack["ingestion"]
    app.dependency_overrides[get_retriever] = lambda: fake_knowledge_stack["retriever"]
    yield app_client
    # Clean up the two we added; leave whatever app_client set.
    app.dependency_overrides.pop(get_ingestion_service, None)
    app.dependency_overrides.pop(get_retriever, None)


@pytest.mark.asyncio
async def test_ingest_markdown_then_search(app_with_knowledge_overrides, db_session):
    project = ProjectORM(user_id="matt", name="P", default_model="claude-sonnet-4-6")
    db_session.add(project)
    await db_session.flush()

    body = {
        "project_id": str(project.id),
        "source_type": "markdown",
        "text": "# Notes\n\n" + ("alpha beta " * 600),
        "source_filename": "notes.md",
    }
    resp = await app_with_knowledge_overrides.post("/api/v1/knowledge/ingest", json=body)
    assert resp.status_code == 202
    job = resp.json()
    assert job["status"] == "completed"

    nodes = (await db_session.execute(select(KnowledgeNodeORM))).scalars().all()
    assert any(n.type == "document" for n in nodes)
    assert any(n.type == "chunk" for n in nodes)

    search = await app_with_knowledge_overrides.get(
        "/api/v1/knowledge/search",
        params={"project_id": str(project.id), "query": "alpha beta", "top_k": 3},
    )
    assert search.status_code == 200
    body = search.json()
    assert body["query"] == "alpha beta"
    assert len(body["chunks"]) >= 1


@pytest.mark.asyncio
async def test_get_unknown_job_returns_404(app_with_knowledge_overrides):
    from uuid import uuid4
    resp = await app_with_knowledge_overrides.get(f"/api/v1/knowledge/jobs/{uuid4()}")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_delete_unknown_node_returns_404(app_with_knowledge_overrides):
    from uuid import uuid4
    resp = await app_with_knowledge_overrides.delete(f"/api/v1/knowledge/nodes/{uuid4()}")
    assert resp.status_code == 404
```

Run: `uv run pytest apps/api/atlas_api/tests/test_knowledge_router.py -v`
Expected: ImportError or 3 passed depending on order.

- [ ] **Step 6: Run tests + ruff**

Run: `uv run pytest -q && uv run ruff check . && uv run ruff format --check .`
Expected: all pass + clean. If ruff complains, run `uv run ruff check --fix . && uv run ruff format .`.

- [ ] **Step 7: Commit**

```bash
git add apps/api/ packages/atlas-knowledge/
git commit -m "feat(atlas-api): wire knowledge ingestion + search endpoints"
```

(`packages/atlas-knowledge/` is included since the `__init__.py` re-export tweak from earlier tasks shows up here. Adjust if your diff is cleaner.)

---

## Task 17: End-to-end smoke + final review

**Files:** none modified; verification only.

- [ ] **Step 1: Smoke import + route enumeration**

```bash
ATLAS_DB__DATABASE_URL=postgresql://atlas:atlas@localhost:5432/atlas \
  uv run python -c "from atlas_api.main import app; print([r.path for r in app.routes if r.path.startswith('/api')])"
```
Expected: 5 project routes + `/api/v1/models` + WS path + 5 knowledge routes (ingest, ingest/pdf, jobs/{id}, nodes, nodes/{id}, search).

- [ ] **Step 2: Full test suite**

```bash
uv run pytest -q 2>&1 | tail -10
```
Expected: all pass (Plan 3 had 108; Plan 4 should add ~30+ tests).

- [ ] **Step 3: Lint + format**

```bash
uv run ruff check . && uv run ruff format --check .
```
If issues: run `--fix` and `format` then commit `chore: ruff autofix and format`.

- [ ] **Step 4: Live smoke (manual; Matt runs)**

```bash
uv run uvicorn atlas_api.main:app --host 127.0.0.1 --port 8000
```

In another shell:
```bash
PROJECT_ID=$(curl -s -X POST http://127.0.0.1:8000/api/v1/projects \
  -H 'Content-Type: application/json' \
  -d '{"name":"Knowledge Smoke","default_model":"claude-sonnet-4-6"}' \
  | python3 -c "import sys,json;print(json.load(sys.stdin)['id'])")

# Ingest some markdown
curl -s -X POST http://127.0.0.1:8000/api/v1/knowledge/ingest \
  -H 'Content-Type: application/json' \
  -d "{\"project_id\":\"$PROJECT_ID\",\"source_type\":\"markdown\",\"text\":\"# Test\\n\\nBGE-small embeddings work locally with sentence-transformers. ChromaDB stores them in-process and persists to disk.\"}" \
  | python3 -m json.tool

# Search
curl -s "http://127.0.0.1:8000/api/v1/knowledge/search?project_id=$PROJECT_ID&query=embeddings&top_k=3" \
  | python3 -m json.tool
```
Expected: ingest returns a completed job; search returns the chunk(s).

(First call to ingest may take 10–30s as BGE-small downloads on first use.)

- [ ] **Step 5: Verify persistence**

```bash
docker exec atlas-postgres psql -U atlas -d atlas -c "SELECT id, type, LEFT(text, 60) AS preview FROM knowledge_nodes ORDER BY created_at;"
docker exec atlas-postgres psql -U atlas -d atlas -c "SELECT id, status, source_type, completed_at FROM ingestion_jobs ORDER BY created_at;"
ls -la ./data/chroma | head
```
Expected: 1 document + N chunks; 1 job status=completed; chroma dir contains a sqlite file + per-collection blobs.

- [ ] **Step 6: Cleanup**

```bash
docker exec atlas-postgres psql -U atlas -d atlas -c "DELETE FROM knowledge_nodes; DELETE FROM ingestion_jobs; DELETE FROM projects WHERE name='Knowledge Smoke';"
rm -rf ./data/chroma  # only if you want a clean slate; otherwise leave it
```

Stop the API (Ctrl-C).

---

## Definition of Done for Plan 4

1. `uv sync --all-packages` succeeds.
2. `uv run pytest -q` passes — adds ~30+ tests across nodes/retrieval/embeddings/vector/parsers/chunking/ingestion/retriever/router on top of Plan 3's 108.
3. `uv run ruff check .` and `ruff format --check .` clean.
4. `alembic upgrade head` applies migration 0003 cleanly to a fresh DB; `alembic downgrade -1` rolls back without orphan FKs.
5. `POST /api/v1/knowledge/ingest` with markdown text creates 1 `KnowledgeNode(type=document)` + N `KnowledgeNode(type=chunk)` rows + 1 `IngestionJob(status=completed)` row + N entries in the per-user Chroma collection.
6. `GET /api/v1/knowledge/search?project_id=X&query=Y` returns ranked `ScoredChunk[]` filtered to project X.
7. `GET /api/v1/knowledge/jobs/{id}` returns the job state.
8. `GET /api/v1/knowledge/nodes?project_id=X` lists nodes; `DELETE /api/v1/knowledge/nodes/{id}` removes a node and its chunks (Postgres cascade); Chroma cleanup deferred to Plan 5.
9. BGE-small loads exactly once per process (lazy cache); subsequent embeds reuse the cached model.

When all DoD items pass, this plan is complete. Plan 5 (wire RAG into the chat WS) follows.
