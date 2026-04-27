# ATLAS Phase 1 — Plan 3: LLM Provider Abstraction + Prompt Registry + Chat WebSocket

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Implements:** `docs/superpowers/specs/2026-04-26-atlas-phase-1-foundation-design.md` — §4 (LLM provider abstraction with Anthropic + LM Studio), §5 (prompt registry from v0.2 §E), §6 (chat WebSocket protocol from §3.3), §10 (sessions / messages / model_usage tables).

**Goal:** Real streaming chat. Connect a WebSocket client to `/api/v1/ws/{session_id}`, send a `chat.message`, see tokens stream back from either Anthropic or LM Studio. Persist user + assistant messages with token usage in Postgres after each turn.

**Architecture:** A provider-agnostic `BaseModel` ABC normalizes streaming events from the Anthropic SDK and LM Studio's OpenAI-compatible API to a single `ModelEvent` type. A `ModelRouter` selects a provider per request based on project privacy + override. A Jinja2 `PromptRegistry` composes system prompts from modular templates. The WebSocket handler loads the project + recent message history from Postgres, builds the prompt, routes to a provider, streams tokens to the client, then persists the turn. Tool-use event types (`tool_call`, `tool_result`) exist in the type system and are forwarded to the WS but never emitted by Phase 1 providers.

**Tech Stack:** `anthropic>=0.40` (cloud streaming) · `openai>=1.55` (LM Studio's OpenAI-compatible endpoint) · `jinja2>=3.1` (prompt templates) · FastAPI WebSocket + `fastapi.testclient.TestClient` for tests · async SQLAlchemy from Plan 2 · structlog correlation IDs per WS connection.

---

## File Structure

```
atlas-agent/
├── apps/api/
│   ├── pyproject.toml                              # MODIFIED (no new deps; uses atlas-core)
│   └── atlas_api/
│       ├── routers/
│       │   ├── models.py                           # NEW (GET /api/v1/models)
│       │   └── projects.py                         # unchanged
│       ├── ws/
│       │   ├── __init__.py                         # NEW
│       │   └── chat.py                             # NEW (/api/v1/ws/{session_id})
│       └── main.py                                 # MODIFIED (include models router + ws handler)
├── packages/atlas-core/
│   ├── pyproject.toml                              # MODIFIED (anthropic, openai, jinja2)
│   └── atlas_core/
│       ├── db/
│       │   ├── orm.py                              # MODIFIED (append SessionORM, MessageORM, ModelUsageORM)
│       │   └── converters.py                       # MODIFIED (append session/message converters)
│       ├── models/
│       │   ├── sessions.py                         # NEW (Session, SessionCreate, MessageRole)
│       │   ├── messages.py                         # NEW (Message, ChatRequest, StreamEvent, StreamEventType)
│       │   ├── llm.py                              # NEW (ModelSpec, ModelEvent, ModelEventType, ModelUsage, ToolSchema, ToolCall, ToolResult)
│       │   └── __init__.py                         # MODIFIED (re-export the new symbols)
│       ├── providers/
│       │   ├── __init__.py                         # NEW
│       │   ├── base.py                             # NEW (BaseModel ABC, ProviderError)
│       │   ├── anthropic.py                        # NEW (AnthropicProvider)
│       │   ├── lmstudio.py                         # NEW (LMStudioProvider)
│       │   ├── registry.py                         # NEW (ModelRegistry, ModelRouter)
│       │   └── _fake.py                            # NEW (FakeProvider for tests — atlas_core internal, importable)
│       ├── prompts/
│       │   ├── __init__.py                         # NEW
│       │   ├── registry.py                         # NEW (PromptRegistry, prompt_registry singleton helper)
│       │   ├── builder.py                          # NEW (SystemPromptBuilder)
│       │   └── templates/
│       │       └── system/
│       │           ├── base.j2                     # NEW
│       │           ├── project_context.j2          # NEW
│       │           └── output_format.j2            # NEW
│       └── tests/
│           ├── test_models_sessions.py             # NEW
│           ├── test_models_messages.py             # NEW
│           ├── test_models_llm.py                  # NEW
│           ├── test_providers_base.py              # NEW
│           ├── test_providers_anthropic.py         # NEW
│           ├── test_providers_lmstudio.py          # NEW
│           ├── test_providers_registry.py          # NEW
│           ├── test_prompts_registry.py            # NEW
│           └── test_prompts_builder.py             # NEW
└── infra/alembic/versions/
    └── 0002_add_sessions_messages_usage.py         # NEW
```

**Responsibility per new file:**
- `models/sessions.py` — `Session`, `SessionCreate`, `MessageRole` enum
- `models/messages.py` — `Message`, `ChatRequest`, `StreamEvent`, `StreamEventType` (the WS protocol shapes)
- `models/llm.py` — provider-agnostic LLM types: `ModelSpec` (id + capabilities), `ModelEvent`/`ModelEventType` (normalized stream chunks), `ModelUsage` (token counts), `ToolSchema`/`ToolCall`/`ToolResult` (no-ops in Phase 1, plumbing for Phase 3)
- `providers/base.py` — `BaseModel` ABC defining `stream()`, plus shared `ProviderError`
- `providers/anthropic.py` — wraps `anthropic.AsyncAnthropic`, normalizes its event types to `ModelEvent`
- `providers/lmstudio.py` — wraps `openai.AsyncOpenAI` pointed at LM Studio, same normalization
- `providers/registry.py` — `ModelRegistry` (build providers from config, cached model list) + `ModelRouter` (select by request)
- `providers/_fake.py` — `FakeProvider` for testing the WS layer without hitting real APIs
- `prompts/registry.py` — `PromptRegistry` wrapping a Jinja2 `Environment` with `StrictUndefined`
- `prompts/builder.py` — `SystemPromptBuilder.build(request, project, ...)` composes sections
- `prompts/templates/system/*.j2` — the actual prompts that ship with Plan 3
- `routers/models.py` — `GET /api/v1/models` returns the discovered model list
- `ws/chat.py` — the WebSocket handler

---

## Task 1: Add `anthropic`, `openai`, `jinja2` dependencies

**Files:**
- Modify: `packages/atlas-core/pyproject.toml`

- [ ] **Step 1: Update `dependencies` in `packages/atlas-core/pyproject.toml`**

Append three lines to the existing `dependencies = [...]` block. The full block becomes:

```toml
dependencies = [
    "pydantic>=2.10",
    "pydantic-settings>=2.7",
    "structlog>=24.4",
    "sqlalchemy[asyncio]>=2.0.36",
    "asyncpg>=0.30",
    "alembic>=1.14",
    "psycopg2-binary>=2.9",
    "anthropic>=0.40",
    "openai>=1.55",
    "jinja2>=3.1",
]
```

- [ ] **Step 2: Re-sync the workspace**

Run: `uv sync --all-packages`
Expected: resolves and installs the new deps. `uv.lock` is updated.

- [ ] **Step 3: Verify imports**

Run:
```bash
uv run python -c "import anthropic, openai, jinja2; print(anthropic.__version__, openai.__version__, jinja2.__version__)"
```
Expected: prints version numbers.

- [ ] **Step 4: Commit**

```bash
git add packages/atlas-core/pyproject.toml uv.lock
git commit -m "chore(atlas-core): add anthropic, openai, jinja2 deps"
```

---

## Task 2: Add migration for `sessions`, `messages`, `model_usage` tables

**Files:**
- Create: `infra/alembic/versions/0002_add_sessions_messages_usage.py`

- [ ] **Step 1: Generate the revision**

```bash
ATLAS_DB__DATABASE_URL=postgresql://atlas:atlas@localhost:5432/atlas \
  uv run alembic revision -m "add sessions messages model_usage" --rev-id 0002
```
Expected: A new file appears at `infra/alembic/versions/0002_*.py`.

- [ ] **Step 2: Replace the stub `upgrade()` and `downgrade()` bodies**

Open `infra/alembic/versions/0002_add_sessions_messages_usage.py` and replace `upgrade()` / `downgrade()` with:

```python
def upgrade() -> None:
    op.create_table(
        "sessions",
        sa.Column(
            "id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("user_id", sa.Text(), nullable=False),
        sa.Column(
            "project_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("model", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.Column(
            "last_active_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
    )
    op.create_index("sessions_user_project_idx", "sessions", ["user_id", "project_id"])

    op.create_table(
        "messages",
        sa.Column(
            "id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("user_id", sa.Text(), nullable=False),
        sa.Column(
            "session_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("sessions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("role", sa.Text(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("tool_calls", sa.dialects.postgresql.JSONB(), nullable=True),
        sa.Column("rag_context", sa.dialects.postgresql.JSONB(), nullable=True),
        sa.Column("model", sa.Text(), nullable=True),
        sa.Column("token_count", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
    )
    op.create_index("messages_session_idx", "messages", ["session_id", "created_at"])

    op.create_table(
        "model_usage",
        sa.Column(
            "id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("user_id", sa.Text(), nullable=False),
        sa.Column(
            "session_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("sessions.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "project_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("model_id", sa.Text(), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("output_tokens", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("task_type", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
    )
    op.create_index("model_usage_user_created_idx", "model_usage", ["user_id", "created_at"])

    # Reuse the set_updated_at function from migration 0001 — but sessions doesn't
    # need updated_at (last_active_at is touched explicitly by the WS handler).


def downgrade() -> None:
    op.drop_index("model_usage_user_created_idx", table_name="model_usage")
    op.drop_table("model_usage")
    op.drop_index("messages_session_idx", table_name="messages")
    op.drop_table("messages")
    op.drop_index("sessions_user_project_idx", table_name="sessions")
    op.drop_table("sessions")
```

- [ ] **Step 3: Apply to the dev DB**

```bash
ATLAS_DB__DATABASE_URL=postgresql://atlas:atlas@localhost:5432/atlas uv run alembic upgrade head
```
Expected: `INFO ... Running upgrade 0001 -> 0002, add sessions messages model_usage`.

- [ ] **Step 4: Verify the tables exist with FKs**

```bash
docker exec atlas-postgres psql -U atlas -d atlas -c "\d sessions" \
  && docker exec atlas-postgres psql -U atlas -d atlas -c "\d messages" \
  && docker exec atlas-postgres psql -U atlas -d atlas -c "\d model_usage"
```
Expected: each `\d` shows the columns + the FK constraints.

- [ ] **Step 5: Commit**

```bash
git add infra/alembic/versions/0002_add_sessions_messages_usage.py
git commit -m "feat(db): add sessions, messages, model_usage tables"
```

---

## Task 3: Add SQLAlchemy ORM models for the new tables

**Files:**
- Modify: `packages/atlas-core/atlas_core/db/orm.py` (append three classes)
- Create: `packages/atlas-core/atlas_core/tests/test_db_orm_chat.py`

- [ ] **Step 1: Write failing structural tests**

`packages/atlas-core/atlas_core/tests/test_db_orm_chat.py`:
```python
"""Structural tests for the chat-related ORM models."""
from sqlalchemy import inspect

from atlas_core.db.orm import MessageORM, ModelUsageORM, SessionORM


def test_session_orm_columns():
    cols = {c.name for c in inspect(SessionORM).columns}
    assert cols == {"id", "user_id", "project_id", "model", "created_at", "last_active_at"}


def test_message_orm_columns():
    cols = {c.name for c in inspect(MessageORM).columns}
    assert cols == {
        "id",
        "user_id",
        "session_id",
        "role",
        "content",
        "tool_calls",
        "rag_context",
        "model",
        "token_count",
        "created_at",
    }


def test_model_usage_orm_columns():
    cols = {c.name for c in inspect(ModelUsageORM).columns}
    assert cols == {
        "id",
        "user_id",
        "session_id",
        "project_id",
        "provider",
        "model_id",
        "input_tokens",
        "output_tokens",
        "latency_ms",
        "task_type",
        "created_at",
    }


def test_session_orm_has_project_fk():
    fks = list(inspect(SessionORM).columns["project_id"].foreign_keys)
    assert len(fks) == 1
    assert fks[0].column.table.name == "projects"


def test_message_orm_has_session_fk():
    fks = list(inspect(MessageORM).columns["session_id"].foreign_keys)
    assert len(fks) == 1
    assert fks[0].column.table.name == "sessions"
```

Run: `uv run pytest packages/atlas-core/atlas_core/tests/test_db_orm_chat.py -v`
Expected: ImportError on the missing classes.

- [ ] **Step 2: Append the three ORM classes to `packages/atlas-core/atlas_core/db/orm.py`**

Add these imports if not already present:
```python
from sqlalchemy import ForeignKey, Integer
```

Append at the end of the file (after `class ProjectORM`):

```python
class SessionORM(Base):
    """Maps to the `sessions` table."""

    __tablename__ = "sessions"

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
    model: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )
    last_active_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        Index("sessions_user_project_idx", "user_id", "project_id"),
    )


class MessageORM(Base):
    """Maps to the `messages` table."""

    __tablename__ = "messages"

    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
        server_default=func.gen_random_uuid(),
    )
    user_id: Mapped[str] = mapped_column(Text, nullable=False)
    session_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("sessions.id", ondelete="CASCADE"),
        nullable=False,
    )
    role: Mapped[str] = mapped_column(Text, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    tool_calls: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    rag_context: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    model: Mapped[str | None] = mapped_column(Text, nullable=True)
    token_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        Index("messages_session_idx", "session_id", "created_at"),
    )


class ModelUsageORM(Base):
    """Maps to the `model_usage` table."""

    __tablename__ = "model_usage"

    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
        server_default=func.gen_random_uuid(),
    )
    user_id: Mapped[str] = mapped_column(Text, nullable=False)
    session_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("sessions.id", ondelete="SET NULL"),
        nullable=True,
    )
    project_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="SET NULL"),
        nullable=True,
    )
    provider: Mapped[str] = mapped_column(Text, nullable=False)
    model_id: Mapped[str] = mapped_column(Text, nullable=False)
    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    task_type: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        Index("model_usage_user_created_idx", "user_id", "created_at"),
    )
```

- [ ] **Step 3: Run tests**

Run: `uv run pytest packages/atlas-core/atlas_core/tests/test_db_orm_chat.py -v`
Expected: 5 passed.

Also run the existing project ORM test to make sure the additions didn't regress it:
```bash
uv run pytest packages/atlas-core/atlas_core/tests/test_db_orm.py -v
```
Expected: 3 passed.

- [ ] **Step 4: Commit**

```bash
git add packages/atlas-core/atlas_core/db/orm.py packages/atlas-core/atlas_core/tests/test_db_orm_chat.py
git commit -m "feat(atlas-core): add Session/Message/ModelUsage ORM models"
```

---

## Task 4: Add Session Pydantic models + MessageRole enum (TDD)

**Files:**
- Create: `packages/atlas-core/atlas_core/models/sessions.py`
- Create: `packages/atlas-core/atlas_core/tests/test_models_sessions.py`

- [ ] **Step 1: Write failing tests**

`packages/atlas-core/atlas_core/tests/test_models_sessions.py`:
```python
"""Tests for atlas_core.models.sessions."""
from datetime import datetime, timezone
from uuid import uuid4

import pytest
from pydantic import ValidationError

from atlas_core.models.sessions import MessageRole, Session, SessionCreate


def test_message_role_values():
    assert MessageRole.SYSTEM == "system"
    assert MessageRole.USER == "user"
    assert MessageRole.ASSISTANT == "assistant"
    assert MessageRole.TOOL == "tool"


def test_session_construction_with_all_fields():
    s = Session(
        id=uuid4(),
        user_id="matt",
        project_id=uuid4(),
        model="claude-sonnet-4-6",
        created_at=datetime.now(timezone.utc),
        last_active_at=datetime.now(timezone.utc),
    )
    assert s.user_id == "matt"


def test_session_create_minimal_payload():
    payload = SessionCreate.model_validate({"project_id": str(uuid4())})
    assert payload.model is None  # optional


def test_session_create_requires_project_id():
    with pytest.raises(ValidationError):
        SessionCreate.model_validate({})
```

Run: `uv run pytest packages/atlas-core/atlas_core/tests/test_models_sessions.py -v`
Expected: ImportError.

- [ ] **Step 2: Implement `sessions.py`**

`packages/atlas-core/atlas_core/models/sessions.py`:
```python
"""Pydantic models for chat sessions."""
from datetime import datetime
from enum import StrEnum
from uuid import UUID

from atlas_core.models.base import AtlasModel, AtlasRequestModel, TimestampedModel


class MessageRole(StrEnum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


class Session(AtlasModel):
    """A chat session — one WebSocket connection, one project."""

    id: UUID
    user_id: str
    project_id: UUID
    model: str | None = None
    created_at: datetime
    last_active_at: datetime


class SessionCreate(AtlasRequestModel):
    """Body to create a session via REST (Phase 1: also created on WS connect)."""

    project_id: UUID
    model: str | None = None
```

(Note: `Session` does NOT extend `TimestampedModel` because the DB column is `last_active_at`, not `updated_at` — the field shapes differ. We use `AtlasModel` directly and declare the timestamp fields explicitly.)

- [ ] **Step 3: Run tests**

Run: `uv run pytest packages/atlas-core/atlas_core/tests/test_models_sessions.py -v`
Expected: 4 passed.

- [ ] **Step 4: Commit**

```bash
git add packages/atlas-core/atlas_core/models/sessions.py packages/atlas-core/atlas_core/tests/test_models_sessions.py
git commit -m "feat(atlas-core): add Session Pydantic models + MessageRole enum"
```

---

## Task 5: Add Message + WebSocket protocol Pydantic models (TDD)

**Files:**
- Create: `packages/atlas-core/atlas_core/models/messages.py`
- Create: `packages/atlas-core/atlas_core/tests/test_models_messages.py`

- [ ] **Step 1: Write failing tests**

`packages/atlas-core/atlas_core/tests/test_models_messages.py`:
```python
"""Tests for atlas_core.models.messages."""
from datetime import datetime, timezone
from uuid import uuid4

import pytest
from pydantic import ValidationError

from atlas_core.models.messages import (
    ChatRequest,
    Message,
    StreamEvent,
    StreamEventType,
)
from atlas_core.models.sessions import MessageRole


def test_message_construction():
    m = Message(
        id=uuid4(),
        user_id="matt",
        session_id=uuid4(),
        role=MessageRole.USER,
        content="hello",
        created_at=datetime.now(timezone.utc),
    )
    assert m.role is MessageRole.USER


def test_message_optional_fields_default_none():
    m = Message(
        id=uuid4(),
        user_id="matt",
        session_id=uuid4(),
        role=MessageRole.USER,
        content="hi",
        created_at=datetime.now(timezone.utc),
    )
    assert m.tool_calls is None
    assert m.rag_context is None
    assert m.model is None
    assert m.token_count is None


def test_chat_request_minimal():
    cr = ChatRequest.model_validate({"text": "hello", "project_id": str(uuid4())})
    assert cr.text == "hello"
    assert cr.model_override is None


def test_chat_request_rejects_empty_text():
    with pytest.raises(ValidationError):
        ChatRequest.model_validate({"text": "", "project_id": str(uuid4())})


def test_chat_request_text_too_long():
    long_text = "x" * 32_001
    with pytest.raises(ValidationError):
        ChatRequest.model_validate({"text": long_text, "project_id": str(uuid4())})


def test_stream_event_token_type():
    e = StreamEvent(
        type=StreamEventType.TOKEN,
        payload={"token": "hello"},
        sequence=1,
    )
    assert e.type == "chat.token"


def test_stream_event_type_values():
    assert StreamEventType.TOKEN == "chat.token"
    assert StreamEventType.TOOL_CALL == "chat.tool_use"
    assert StreamEventType.TOOL_RESULT == "chat.tool_result"
    assert StreamEventType.DONE == "chat.done"
    assert StreamEventType.ERROR == "chat.error"
    assert StreamEventType.RAG_CONTEXT == "rag.context"
```

Run: `uv run pytest packages/atlas-core/atlas_core/tests/test_models_messages.py -v`
Expected: ImportError.

- [ ] **Step 2: Implement `messages.py`**

`packages/atlas-core/atlas_core/models/messages.py`:
```python
"""Pydantic models for messages and the WebSocket chat protocol."""
from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import Field

from atlas_core.models.base import AtlasModel, AtlasRequestModel
from atlas_core.models.sessions import MessageRole


class Message(AtlasModel):
    """A single conversation turn persisted in Postgres."""

    id: UUID
    user_id: str
    session_id: UUID
    role: MessageRole
    content: str
    tool_calls: list[dict[str, Any]] | None = None
    rag_context: list[dict[str, Any]] | None = None
    model: str | None = None
    token_count: int | None = None
    created_at: datetime


class ChatRequest(AtlasRequestModel):
    """Payload of a ``chat.message`` WebSocket event."""

    text: str = Field(min_length=1, max_length=32_000)
    project_id: UUID
    model_override: str | None = Field(default=None, pattern=r"^[a-zA-Z0-9._\-:/]+$")
    rag_enabled: bool = True  # Phase 1 ignores this; Plan 5 wires it in
    top_k_context: int = Field(default=8, ge=1, le=32)
    temperature: float = Field(default=0.7, ge=0.0, le=2.0)


class StreamEventType(StrEnum):
    """Server → client WebSocket event names."""

    TOKEN = "chat.token"
    TOOL_CALL = "chat.tool_use"
    TOOL_RESULT = "chat.tool_result"
    RAG_CONTEXT = "rag.context"
    DONE = "chat.done"
    ERROR = "chat.error"


class StreamEvent(AtlasModel):
    """One server → client WebSocket message.

    Phase 1: ``sequence`` is a monotonic per-connection counter so the
    client can detect drops or out-of-order arrival.
    """

    type: StreamEventType
    payload: dict[str, Any]
    sequence: int = Field(ge=0)
```

- [ ] **Step 3: Run tests**

Run: `uv run pytest packages/atlas-core/atlas_core/tests/test_models_messages.py -v`
Expected: 7 passed.

- [ ] **Step 4: Commit**

```bash
git add packages/atlas-core/atlas_core/models/messages.py packages/atlas-core/atlas_core/tests/test_models_messages.py
git commit -m "feat(atlas-core): add Message + ChatRequest + StreamEvent models"
```

---

## Task 6: Add LLM-layer Pydantic models — ModelSpec, ModelEvent, ToolSchema (TDD)

**Files:**
- Create: `packages/atlas-core/atlas_core/models/llm.py`
- Create: `packages/atlas-core/atlas_core/tests/test_models_llm.py`

- [ ] **Step 1: Write failing tests**

`packages/atlas-core/atlas_core/tests/test_models_llm.py`:
```python
"""Tests for atlas_core.models.llm."""
from atlas_core.models.llm import (
    ModelEvent,
    ModelEventType,
    ModelSpec,
    ModelUsage,
    ToolCall,
    ToolResult,
    ToolSchema,
)


def test_model_event_type_values():
    assert ModelEventType.TOKEN == "token"
    assert ModelEventType.TOOL_CALL == "tool_call"
    assert ModelEventType.TOOL_RESULT == "tool_result"
    assert ModelEventType.DONE == "done"
    assert ModelEventType.ERROR == "error"


def test_model_spec_construction():
    spec = ModelSpec(
        provider="anthropic",
        model_id="claude-sonnet-4-6",
        context_window=200_000,
        supports_tools=True,
        supports_streaming=True,
    )
    assert spec.provider == "anthropic"


def test_model_event_token():
    e = ModelEvent(type=ModelEventType.TOKEN, data={"text": "hello"})
    assert e.type == "token"
    assert e.data["text"] == "hello"


def test_model_event_done_with_usage():
    usage = ModelUsage(input_tokens=42, output_tokens=17, model_id="x", provider="y")
    e = ModelEvent(type=ModelEventType.DONE, data={"usage": usage.model_dump(mode="python")})
    assert e.type == "done"
    assert e.data["usage"]["input_tokens"] == 42


def test_tool_schema_round_trip():
    ts = ToolSchema(
        name="x.y",
        description="test tool",
        parameters={"type": "object", "properties": {}},
        plugin="x",
    )
    assert ts.requires_confirmation is False  # default


def test_tool_call_and_result_pair():
    call = ToolCall(id="t-1", tool="github.search", args={"q": "x"})
    result = ToolResult(call_id="t-1", tool="github.search", result={"hits": []})
    assert call.id == result.call_id
```

Run: `uv run pytest packages/atlas-core/atlas_core/tests/test_models_llm.py -v`
Expected: ImportError.

- [ ] **Step 2: Implement `llm.py`**

`packages/atlas-core/atlas_core/models/llm.py`:
```python
"""Provider-agnostic LLM types.

The ``BaseModel`` provider ABC streams ``ModelEvent`` instances. Each
provider implementation translates its native chunks into these types.
``ToolCall`` / ``ToolResult`` / ``ToolSchema`` exist for the Phase 3
plugin layer; Phase 1 providers never emit tool events but the type
plumbing is in place.
"""
from enum import StrEnum
from typing import Any

from pydantic import Field

from atlas_core.models.base import AtlasModel


class ModelSpec(AtlasModel):
    """Describes one LLM choice surfaced via ``GET /api/v1/models``."""

    provider: str             # "anthropic" | "lmstudio" | future
    model_id: str             # e.g. "claude-sonnet-4-6"
    context_window: int = Field(ge=1)
    supports_tools: bool
    supports_streaming: bool = True


class ModelUsage(AtlasModel):
    """Token + cost metrics for one model invocation."""

    provider: str
    model_id: str
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    latency_ms: int | None = Field(default=None, ge=0)


class ModelEventType(StrEnum):
    TOKEN = "token"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    DONE = "done"
    ERROR = "error"


class ModelEvent(AtlasModel):
    """One normalized event in a streaming model response.

    ``data`` shape per type:
    - ``token``: ``{"text": "..."}``
    - ``tool_call``: serialized ``ToolCall``
    - ``tool_result``: serialized ``ToolResult``
    - ``done``: ``{"usage": ...}``
    - ``error``: ``{"code": "...", "message": "..."}``
    """

    type: ModelEventType
    data: dict[str, Any] = Field(default_factory=dict)


class ToolSchema(AtlasModel):
    """JSON-Schema description of a tool, exposed to the model."""

    name: str                       # e.g. "github.search_code"
    description: str
    parameters: dict[str, Any]      # full JSON Schema
    plugin: str
    requires_confirmation: bool = False


class ToolCall(AtlasModel):
    """A tool invocation requested by the model (Phase 1: never emitted)."""

    id: str
    tool: str
    args: dict[str, Any]


class ToolResult(AtlasModel):
    """The result of executing a ``ToolCall``."""

    call_id: str
    tool: str
    result: Any                     # tool-specific shape
    error: str | None = None
```

- [ ] **Step 3: Run tests**

Run: `uv run pytest packages/atlas-core/atlas_core/tests/test_models_llm.py -v`
Expected: 6 passed.

- [ ] **Step 4: Re-export from `models/__init__.py`**

Replace the entire contents of `packages/atlas-core/atlas_core/models/__init__.py` with:

```python
"""Pydantic models shared across ATLAS."""

from atlas_core.models.base import (
    AtlasModel,
    AtlasRequestModel,
    MutableAtlasModel,
    TimestampedModel,
)
from atlas_core.models.llm import (
    ModelEvent,
    ModelEventType,
    ModelSpec,
    ModelUsage,
    ToolCall,
    ToolResult,
    ToolSchema,
)
from atlas_core.models.messages import (
    ChatRequest,
    Message,
    StreamEvent,
    StreamEventType,
)
from atlas_core.models.projects import (
    PrivacyLevel,
    Project,
    ProjectCreate,
    ProjectStatus,
    ProjectUpdate,
)
from atlas_core.models.sessions import (
    MessageRole,
    Session,
    SessionCreate,
)

__all__ = [
    "AtlasModel",
    "AtlasRequestModel",
    "ChatRequest",
    "Message",
    "MessageRole",
    "ModelEvent",
    "ModelEventType",
    "ModelSpec",
    "ModelUsage",
    "MutableAtlasModel",
    "PrivacyLevel",
    "Project",
    "ProjectCreate",
    "ProjectStatus",
    "ProjectUpdate",
    "Session",
    "SessionCreate",
    "StreamEvent",
    "StreamEventType",
    "TimestampedModel",
    "ToolCall",
    "ToolResult",
    "ToolSchema",
]
```

- [ ] **Step 5: Commit**

```bash
git add packages/atlas-core/atlas_core/models/llm.py packages/atlas-core/atlas_core/models/__init__.py packages/atlas-core/atlas_core/tests/test_models_llm.py
git commit -m "feat(atlas-core): add ModelSpec/ModelEvent/Tool* LLM types"
```

---

## Task 7: Append session/message ORM converters

**Files:**
- Modify: `packages/atlas-core/atlas_core/db/converters.py`
- Create: `packages/atlas-core/atlas_core/tests/test_db_converters.py`

- [ ] **Step 1: Write failing tests**

`packages/atlas-core/atlas_core/tests/test_db_converters.py`:
```python
"""Tests for atlas_core.db.converters — pure conversion logic, no DB roundtrip."""
from datetime import datetime, timezone
from uuid import uuid4

from atlas_core.db.converters import message_from_orm, session_from_orm
from atlas_core.db.orm import MessageORM, SessionORM


def _build_session_row() -> SessionORM:
    return SessionORM(
        id=uuid4(),
        user_id="matt",
        project_id=uuid4(),
        model="claude-sonnet-4-6",
        created_at=datetime.now(timezone.utc),
        last_active_at=datetime.now(timezone.utc),
    )


def _build_message_row() -> MessageORM:
    return MessageORM(
        id=uuid4(),
        user_id="matt",
        session_id=uuid4(),
        role="assistant",
        content="hi",
        tool_calls=None,
        rag_context=None,
        model="claude-sonnet-4-6",
        token_count=10,
        created_at=datetime.now(timezone.utc),
    )


def test_session_from_orm_roundtrip():
    row = _build_session_row()
    s = session_from_orm(row)
    assert s.user_id == row.user_id
    assert s.project_id == row.project_id
    assert s.model == row.model


def test_message_from_orm_roundtrip():
    row = _build_message_row()
    m = message_from_orm(row)
    assert m.user_id == row.user_id
    assert m.role == "assistant"
    assert m.content == "hi"


def test_message_from_orm_handles_jsonb_none():
    row = _build_message_row()
    row.tool_calls = None
    row.rag_context = None
    m = message_from_orm(row)
    assert m.tool_calls is None
    assert m.rag_context is None
```

Run: `uv run pytest packages/atlas-core/atlas_core/tests/test_db_converters.py -v`
Expected: ImportError on `session_from_orm`/`message_from_orm`.

- [ ] **Step 2: Append converters to `packages/atlas-core/atlas_core/db/converters.py`**

Add these imports (and keep existing imports):
```python
from atlas_core.db.orm import MessageORM, SessionORM
from atlas_core.models.messages import Message
from atlas_core.models.sessions import MessageRole, Session
```

Append at the end:

```python
def session_from_orm(row: SessionORM) -> Session:
    return Session(
        id=row.id,
        user_id=row.user_id,
        project_id=row.project_id,
        model=row.model,
        created_at=row.created_at,
        last_active_at=row.last_active_at,
    )


def message_from_orm(row: MessageORM) -> Message:
    return Message(
        id=row.id,
        user_id=row.user_id,
        session_id=row.session_id,
        role=MessageRole(row.role),
        content=row.content,
        tool_calls=row.tool_calls,
        rag_context=row.rag_context,
        model=row.model,
        token_count=row.token_count,
        created_at=row.created_at,
    )
```

- [ ] **Step 3: Run tests**

Run: `uv run pytest packages/atlas-core/atlas_core/tests/test_db_converters.py -v`
Expected: 3 passed.

- [ ] **Step 4: Commit**

```bash
git add packages/atlas-core/atlas_core/db/converters.py packages/atlas-core/atlas_core/tests/test_db_converters.py
git commit -m "feat(atlas-core): add session_from_orm + message_from_orm converters"
```

---

## Task 8: Add `BaseModel` provider ABC + `ProviderError` (TDD)

**Files:**
- Create: `packages/atlas-core/atlas_core/providers/__init__.py`
- Create: `packages/atlas-core/atlas_core/providers/base.py`
- Create: `packages/atlas-core/atlas_core/providers/_fake.py`
- Create: `packages/atlas-core/atlas_core/tests/test_providers_base.py`

- [ ] **Step 1: Create the package**

```bash
mkdir -p packages/atlas-core/atlas_core/providers
```

`packages/atlas-core/atlas_core/providers/__init__.py`:
```python
"""LLM provider abstraction + concrete implementations."""

from atlas_core.providers.base import BaseModel, ProviderError
from atlas_core.providers._fake import FakeProvider

__all__ = ["BaseModel", "FakeProvider", "ProviderError"]
```

- [ ] **Step 2: Write failing tests**

`packages/atlas-core/atlas_core/tests/test_providers_base.py`:
```python
"""Tests for atlas_core.providers.base + the FakeProvider used in WS tests."""
import pytest

from atlas_core.models.llm import ModelEventType
from atlas_core.providers import BaseModel, FakeProvider, ProviderError


def test_provider_error_carries_code_and_message():
    err = ProviderError(code="rate_limit", message="too many")
    assert err.code == "rate_limit"
    assert "too many" in str(err)


def test_base_model_is_abstract():
    with pytest.raises(TypeError):
        BaseModel()  # type: ignore[abstract]


async def test_fake_provider_streams_tokens_then_done():
    fp = FakeProvider(model_id="fake-1", token_chunks=["hello", " ", "world"])
    events = []
    async for ev in fp.stream(messages=[{"role": "user", "content": "hi"}]):
        events.append(ev)
    types = [e.type for e in events]
    assert types == [
        ModelEventType.TOKEN,
        ModelEventType.TOKEN,
        ModelEventType.TOKEN,
        ModelEventType.DONE,
    ]
    final = events[-1]
    assert final.data["usage"]["input_tokens"] >= 0


async def test_fake_provider_can_be_configured_to_error():
    fp = FakeProvider(model_id="fake-1", token_chunks=[], error_on_call=True)
    events = []
    async for ev in fp.stream(messages=[]):
        events.append(ev)
    assert any(e.type == ModelEventType.ERROR for e in events)
```

Run: `uv run pytest packages/atlas-core/atlas_core/tests/test_providers_base.py -v`
Expected: ImportError.

- [ ] **Step 3: Implement `base.py`**

`packages/atlas-core/atlas_core/providers/base.py`:
```python
"""LLM provider ABC and shared error type."""
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from typing import Any

from atlas_core.models.llm import ModelEvent, ModelSpec


class ProviderError(Exception):
    """Wraps any provider-side failure (network, auth, rate limit, ...)."""

    def __init__(self, code: str, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable


class BaseModel(ABC):
    """Abstract async streaming LLM provider.

    Concrete implementations live alongside this file. Plan 3 ships
    ``AnthropicProvider`` and ``LMStudioProvider``; later phases add more.
    """

    spec: ModelSpec  # set by subclass __init__

    @abstractmethod
    async def stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
    ) -> AsyncIterator[ModelEvent]:
        """Yield normalized ``ModelEvent`` instances until the response ends.

        Always concludes with one ``ModelEventType.DONE`` event whose ``data["usage"]``
        carries the input/output token counts. On failure, yields one ``ModelEventType.ERROR``
        and stops (rather than raising — the WS handler catches via the event type).
        """
        if False:  # pragma: no cover — ABC contract
            yield  # type: ignore[unreachable]
```

- [ ] **Step 4: Implement `_fake.py`**

`packages/atlas-core/atlas_core/providers/_fake.py`:
```python
"""FakeProvider — used by tests to exercise the WS layer without real API calls.

Importable from ``atlas_core.providers`` so test files don't need to reach
into private modules.
"""
from collections.abc import AsyncIterator
from typing import Any

from atlas_core.models.llm import ModelEvent, ModelEventType, ModelSpec
from atlas_core.providers.base import BaseModel


class FakeProvider(BaseModel):
    """Streams a fixed sequence of token chunks, then emits ``done`` with usage."""

    def __init__(
        self,
        *,
        model_id: str = "fake-1",
        token_chunks: list[str] | None = None,
        error_on_call: bool = False,
    ) -> None:
        self.spec = ModelSpec(
            provider="fake",
            model_id=model_id,
            context_window=8192,
            supports_tools=False,
            supports_streaming=True,
        )
        self.token_chunks = token_chunks or ["hello", " world"]
        self.error_on_call = error_on_call

    async def stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
    ) -> AsyncIterator[ModelEvent]:
        if self.error_on_call:
            yield ModelEvent(
                type=ModelEventType.ERROR,
                data={"code": "fake_error", "message": "configured to fail"},
            )
            return

        output_tokens = 0
        for chunk in self.token_chunks:
            output_tokens += len(chunk.split()) or 1
            yield ModelEvent(type=ModelEventType.TOKEN, data={"text": chunk})

        # Approximate input tokens from message content lengths
        input_tokens = sum(len(m.get("content", "").split()) for m in messages)

        yield ModelEvent(
            type=ModelEventType.DONE,
            data={
                "usage": {
                    "provider": self.spec.provider,
                    "model_id": self.spec.model_id,
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                    "latency_ms": 0,
                }
            },
        )
```

- [ ] **Step 5: Run tests**

Run: `uv run pytest packages/atlas-core/atlas_core/tests/test_providers_base.py -v`
Expected: 4 passed.

- [ ] **Step 6: Commit**

```bash
git add packages/atlas-core/atlas_core/providers/ packages/atlas-core/atlas_core/tests/test_providers_base.py
git commit -m "feat(atlas-core): add provider BaseModel ABC + FakeProvider for tests"
```

---

## Task 9: Implement `AnthropicProvider` (TDD)

**Files:**
- Create: `packages/atlas-core/atlas_core/providers/anthropic.py`
- Create: `packages/atlas-core/atlas_core/tests/test_providers_anthropic.py`

- [ ] **Step 1: Write failing tests**

`packages/atlas-core/atlas_core/tests/test_providers_anthropic.py`:
```python
"""Tests for AnthropicProvider — uses a fake AsyncAnthropic transport."""
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from atlas_core.models.llm import ModelEventType
from atlas_core.providers.anthropic import AnthropicProvider


class _FakeAnthropicStream:
    """Mimics ``anthropic.AsyncMessageStreamManager`` for tests."""

    def __init__(self, events: list[Any]) -> None:
        self._events = events

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def __aiter__(self):
        for e in self._events:
            yield e


def _text_delta(text: str):
    """Emit an event matching anthropic SDK's RawContentBlockDeltaEvent shape (text)."""
    ev = MagicMock()
    ev.type = "content_block_delta"
    ev.delta = MagicMock()
    ev.delta.type = "text_delta"
    ev.delta.text = text
    return ev


def _message_stop(input_tokens: int, output_tokens: int):
    """Emit an event matching anthropic SDK's MessageDeltaEvent / MessageStopEvent shape."""
    ev = MagicMock()
    ev.type = "message_delta"
    ev.usage = MagicMock()
    ev.usage.input_tokens = input_tokens
    ev.usage.output_tokens = output_tokens
    return ev


@pytest.fixture
def fake_client():
    client = MagicMock()
    client.messages = MagicMock()
    return client


async def test_anthropic_provider_streams_tokens_and_emits_done(fake_client):
    fake_client.messages.stream = MagicMock(
        return_value=_FakeAnthropicStream(
            [
                _text_delta("hello"),
                _text_delta(" world"),
                _message_stop(input_tokens=12, output_tokens=2),
            ]
        )
    )

    provider = AnthropicProvider(
        api_key="sk-test",
        model_id="claude-sonnet-4-6",
        _client=fake_client,
    )

    events = []
    async for ev in provider.stream(
        messages=[{"role": "user", "content": "hi"}],
    ):
        events.append(ev)

    types = [e.type for e in events]
    assert types == [
        ModelEventType.TOKEN,
        ModelEventType.TOKEN,
        ModelEventType.DONE,
    ]
    assert events[0].data["text"] == "hello"
    assert events[-1].data["usage"]["input_tokens"] == 12
    assert events[-1].data["usage"]["output_tokens"] == 2


async def test_anthropic_provider_emits_error_on_exception(fake_client):
    def _raise(*args, **kwargs):
        raise RuntimeError("network down")

    fake_client.messages.stream = _raise

    provider = AnthropicProvider(
        api_key="sk-test",
        model_id="claude-sonnet-4-6",
        _client=fake_client,
    )

    events = []
    async for ev in provider.stream(messages=[{"role": "user", "content": "hi"}]):
        events.append(ev)

    assert len(events) == 1
    assert events[0].type == ModelEventType.ERROR
    assert "network down" in events[0].data["message"]


def test_anthropic_provider_spec():
    provider = AnthropicProvider(
        api_key="sk-test",
        model_id="claude-sonnet-4-6",
        context_window=200_000,
    )
    assert provider.spec.provider == "anthropic"
    assert provider.spec.model_id == "claude-sonnet-4-6"
    assert provider.spec.context_window == 200_000
```

Run: `uv run pytest packages/atlas-core/atlas_core/tests/test_providers_anthropic.py -v`
Expected: ImportError.

- [ ] **Step 2: Implement `anthropic.py`**

`packages/atlas-core/atlas_core/providers/anthropic.py`:
```python
"""Anthropic provider — wraps anthropic.AsyncAnthropic.messages.stream."""
import time
from collections.abc import AsyncIterator
from typing import Any

import anthropic

from atlas_core.models.llm import ModelEvent, ModelEventType, ModelSpec
from atlas_core.providers.base import BaseModel


class AnthropicProvider(BaseModel):
    """Streaming Anthropic provider.

    The ``_client`` keyword is for tests — pass a stub to bypass the SDK.
    """

    def __init__(
        self,
        api_key: str,
        model_id: str,
        *,
        context_window: int = 200_000,
        supports_tools: bool = True,
        _client: Any | None = None,
    ) -> None:
        self.spec = ModelSpec(
            provider="anthropic",
            model_id=model_id,
            context_window=context_window,
            supports_tools=supports_tools,
            supports_streaming=True,
        )
        self._client = _client or anthropic.AsyncAnthropic(api_key=api_key)

    async def stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
    ) -> AsyncIterator[ModelEvent]:
        # Anthropic requires a separate `system` arg, not a system message.
        system, user_messages = _split_system(messages)

        kwargs: dict[str, Any] = {
            "model": self.spec.model_id,
            "messages": user_messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if system:
            kwargs["system"] = system
        if tools:
            kwargs["tools"] = tools

        started = time.monotonic()
        input_tokens = 0
        output_tokens = 0

        try:
            async with self._client.messages.stream(**kwargs) as stream:
                async for event in stream:
                    et = getattr(event, "type", None)
                    if et == "content_block_delta":
                        delta = getattr(event, "delta", None)
                        if delta is not None and getattr(delta, "type", None) == "text_delta":
                            yield ModelEvent(
                                type=ModelEventType.TOKEN,
                                data={"text": delta.text},
                            )
                    elif et in ("message_delta", "message_stop"):
                        usage = getattr(event, "usage", None)
                        if usage is not None:
                            input_tokens = getattr(usage, "input_tokens", input_tokens) or input_tokens
                            output_tokens = (
                                getattr(usage, "output_tokens", output_tokens) or output_tokens
                            )
        except Exception as e:
            yield ModelEvent(
                type=ModelEventType.ERROR,
                data={"code": "anthropic_error", "message": str(e)},
            )
            return

        latency_ms = int((time.monotonic() - started) * 1000)
        yield ModelEvent(
            type=ModelEventType.DONE,
            data={
                "usage": {
                    "provider": "anthropic",
                    "model_id": self.spec.model_id,
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                    "latency_ms": latency_ms,
                }
            },
        )


def _split_system(
    messages: list[dict[str, Any]],
) -> tuple[str | None, list[dict[str, Any]]]:
    """Anthropic's API takes ``system`` as a top-level arg, not a message role."""
    system_parts: list[str] = []
    rest: list[dict[str, Any]] = []
    for m in messages:
        if m.get("role") == "system":
            system_parts.append(m.get("content", ""))
        else:
            rest.append(m)
    return ("\n\n".join(system_parts) if system_parts else None), rest
```

- [ ] **Step 3: Run tests**

Run: `uv run pytest packages/atlas-core/atlas_core/tests/test_providers_anthropic.py -v`
Expected: 3 passed.

- [ ] **Step 4: Commit**

```bash
git add packages/atlas-core/atlas_core/providers/anthropic.py packages/atlas-core/atlas_core/tests/test_providers_anthropic.py
git commit -m "feat(atlas-core): add AnthropicProvider with normalized streaming"
```

---

## Task 10: Implement `LMStudioProvider` (TDD)

**Files:**
- Create: `packages/atlas-core/atlas_core/providers/lmstudio.py`
- Create: `packages/atlas-core/atlas_core/tests/test_providers_lmstudio.py`

- [ ] **Step 1: Write failing tests**

`packages/atlas-core/atlas_core/tests/test_providers_lmstudio.py`:
```python
"""Tests for LMStudioProvider — uses a fake AsyncOpenAI transport."""
from typing import Any
from unittest.mock import MagicMock

import pytest

from atlas_core.models.llm import ModelEventType
from atlas_core.providers.lmstudio import LMStudioProvider


class _FakeOpenAIChunk:
    """Mimics openai.types.chat.ChatCompletionChunk shape."""

    def __init__(self, content: str | None = None, finish_reason: str | None = None,
                 prompt_tokens: int = 0, completion_tokens: int = 0):
        delta = MagicMock()
        delta.content = content
        choice = MagicMock()
        choice.delta = delta
        choice.finish_reason = finish_reason
        self.choices = [choice]
        if prompt_tokens or completion_tokens:
            usage = MagicMock()
            usage.prompt_tokens = prompt_tokens
            usage.completion_tokens = completion_tokens
            self.usage = usage
        else:
            self.usage = None


class _FakeOpenAIStream:
    def __init__(self, chunks: list[Any]) -> None:
        self._chunks = chunks

    def __aiter__(self):
        return self._gen()

    async def _gen(self):
        for c in self._chunks:
            yield c


@pytest.fixture
def fake_client():
    client = MagicMock()
    client.chat = MagicMock()
    client.chat.completions = MagicMock()
    return client


async def test_lmstudio_provider_streams_tokens_and_emits_done(fake_client):
    async def _create_stream(*args, **kwargs):
        return _FakeOpenAIStream(
            [
                _FakeOpenAIChunk(content="hello"),
                _FakeOpenAIChunk(content=" world"),
                _FakeOpenAIChunk(content=None, finish_reason="stop",
                                 prompt_tokens=11, completion_tokens=2),
            ]
        )

    fake_client.chat.completions.create = _create_stream

    provider = LMStudioProvider(
        base_url="http://x:1234/v1",
        model_id="gemma-3-12b",
        _client=fake_client,
    )

    events = []
    async for ev in provider.stream(
        messages=[{"role": "user", "content": "hi"}],
    ):
        events.append(ev)

    types = [e.type for e in events]
    assert types == [
        ModelEventType.TOKEN,
        ModelEventType.TOKEN,
        ModelEventType.DONE,
    ]
    assert events[-1].data["usage"]["input_tokens"] == 11
    assert events[-1].data["usage"]["output_tokens"] == 2


async def test_lmstudio_provider_emits_error_on_exception(fake_client):
    async def _raise(*args, **kwargs):
        raise RuntimeError("connection refused")

    fake_client.chat.completions.create = _raise

    provider = LMStudioProvider(
        base_url="http://x:1234/v1",
        model_id="gemma-3-12b",
        _client=fake_client,
    )

    events = []
    async for ev in provider.stream(messages=[{"role": "user", "content": "hi"}]):
        events.append(ev)

    assert len(events) == 1
    assert events[0].type == ModelEventType.ERROR


def test_lmstudio_provider_spec():
    provider = LMStudioProvider(
        base_url="http://x:1234/v1",
        model_id="gemma-3-12b",
    )
    assert provider.spec.provider == "lmstudio"
    assert provider.spec.model_id == "gemma-3-12b"
```

Run: `uv run pytest packages/atlas-core/atlas_core/tests/test_providers_lmstudio.py -v`
Expected: ImportError.

- [ ] **Step 2: Implement `lmstudio.py`**

`packages/atlas-core/atlas_core/providers/lmstudio.py`:
```python
"""LM Studio provider — uses openai.AsyncOpenAI against the LM Studio endpoint."""
import time
from collections.abc import AsyncIterator
from typing import Any

import openai

from atlas_core.models.llm import ModelEvent, ModelEventType, ModelSpec
from atlas_core.providers.base import BaseModel


class LMStudioProvider(BaseModel):
    """Streaming OpenAI-compatible provider pointed at LM Studio.

    LM Studio's `/v1/chat/completions` endpoint is wire-compatible with
    OpenAI; we just point the SDK at the local URL.
    """

    def __init__(
        self,
        base_url: str,
        model_id: str,
        *,
        context_window: int = 8192,
        supports_tools: bool = False,  # local models vary; default off
        api_key: str = "lm-studio",   # ignored by LM Studio but required by SDK
        _client: Any | None = None,
    ) -> None:
        self.spec = ModelSpec(
            provider="lmstudio",
            model_id=model_id,
            context_window=context_window,
            supports_tools=supports_tools,
            supports_streaming=True,
        )
        self._client = _client or openai.AsyncOpenAI(base_url=base_url, api_key=api_key)

    async def stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
    ) -> AsyncIterator[ModelEvent]:
        kwargs: dict[str, Any] = {
            "model": self.spec.model_id,
            "messages": messages,
            "stream": True,
            "stream_options": {"include_usage": True},
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if tools and self.spec.supports_tools:
            kwargs["tools"] = tools

        started = time.monotonic()
        input_tokens = 0
        output_tokens = 0

        try:
            stream = await self._client.chat.completions.create(**kwargs)
            async for chunk in stream:
                if not chunk.choices:
                    # The final chunk in include_usage mode has empty choices but a usage block
                    if getattr(chunk, "usage", None) is not None:
                        input_tokens = chunk.usage.prompt_tokens or 0
                        output_tokens = chunk.usage.completion_tokens or 0
                    continue
                choice = chunk.choices[0]
                delta = getattr(choice, "delta", None)
                if delta is not None and getattr(delta, "content", None):
                    yield ModelEvent(
                        type=ModelEventType.TOKEN,
                        data={"text": delta.content},
                    )
                if getattr(chunk, "usage", None) is not None:
                    input_tokens = chunk.usage.prompt_tokens or input_tokens
                    output_tokens = chunk.usage.completion_tokens or output_tokens
        except Exception as e:
            yield ModelEvent(
                type=ModelEventType.ERROR,
                data={"code": "lmstudio_error", "message": str(e)},
            )
            return

        latency_ms = int((time.monotonic() - started) * 1000)
        yield ModelEvent(
            type=ModelEventType.DONE,
            data={
                "usage": {
                    "provider": "lmstudio",
                    "model_id": self.spec.model_id,
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                    "latency_ms": latency_ms,
                }
            },
        )
```

- [ ] **Step 3: Run tests**

Run: `uv run pytest packages/atlas-core/atlas_core/tests/test_providers_lmstudio.py -v`
Expected: 3 passed.

- [ ] **Step 4: Commit**

```bash
git add packages/atlas-core/atlas_core/providers/lmstudio.py packages/atlas-core/atlas_core/tests/test_providers_lmstudio.py
git commit -m "feat(atlas-core): add LMStudioProvider via openai SDK"
```

---

## Task 11: Add `ModelRegistry` and `ModelRouter` (TDD)

**Files:**
- Create: `packages/atlas-core/atlas_core/providers/registry.py`
- Create: `packages/atlas-core/atlas_core/tests/test_providers_registry.py`

- [ ] **Step 1: Write failing tests**

`packages/atlas-core/atlas_core/tests/test_providers_registry.py`:
```python
"""Tests for ModelRegistry + ModelRouter."""
from uuid import uuid4

import pytest

from atlas_core.models.llm import ModelSpec
from atlas_core.models.projects import PrivacyLevel, Project, ProjectStatus
from atlas_core.providers import FakeProvider
from atlas_core.providers.registry import ModelRegistry, ModelRouter


def _project(privacy: PrivacyLevel = PrivacyLevel.CLOUD_OK, default_model: str = "claude-sonnet-4-6") -> Project:
    from datetime import datetime, timezone
    return Project(
        id=uuid4(),
        user_id="matt",
        name="P",
        description=None,
        status=ProjectStatus.ACTIVE,
        privacy_level=privacy,
        default_model=default_model,
        enabled_plugins=[],
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )


def test_model_registry_register_and_get():
    reg = ModelRegistry()
    fp = FakeProvider(model_id="fake-1")
    reg.register(fp)
    assert reg.get("fake-1") is fp


def test_model_registry_specs_returns_all():
    reg = ModelRegistry()
    reg.register(FakeProvider(model_id="a"))
    reg.register(FakeProvider(model_id="b"))
    specs = reg.specs()
    ids = {s.model_id for s in specs}
    assert ids == {"a", "b"}


def test_model_router_uses_explicit_override():
    reg = ModelRegistry()
    cloud = FakeProvider(model_id="cloud-1")
    local = FakeProvider(model_id="local-1")
    reg.register(cloud)
    reg.register(local)
    router = ModelRouter(reg)

    # Manually set fake provider provider name to mimic real ones for the policy
    cloud.spec = ModelSpec(provider="anthropic", model_id="cloud-1", context_window=1, supports_tools=False)
    local.spec = ModelSpec(provider="lmstudio", model_id="local-1", context_window=1, supports_tools=False)

    chosen = router.select(_project(), model_override="local-1")
    assert chosen is local


def test_model_router_local_only_picks_lmstudio():
    reg = ModelRegistry()
    cloud = FakeProvider(model_id="cloud-1")
    local = FakeProvider(model_id="local-1")
    cloud.spec = ModelSpec(provider="anthropic", model_id="cloud-1", context_window=1, supports_tools=False)
    local.spec = ModelSpec(provider="lmstudio", model_id="local-1", context_window=1, supports_tools=False)
    reg.register(cloud)
    reg.register(local)

    router = ModelRouter(reg)
    chosen = router.select(_project(privacy=PrivacyLevel.LOCAL_ONLY))
    assert chosen.spec.provider == "lmstudio"


def test_model_router_falls_back_to_default_model():
    reg = ModelRegistry()
    fp = FakeProvider(model_id="claude-sonnet-4-6")
    fp.spec = ModelSpec(provider="anthropic", model_id="claude-sonnet-4-6", context_window=1, supports_tools=False)
    reg.register(fp)

    router = ModelRouter(reg)
    chosen = router.select(_project())  # cloud_ok, default model
    assert chosen.spec.model_id == "claude-sonnet-4-6"


def test_model_router_raises_if_local_only_and_no_local_provider():
    reg = ModelRegistry()
    cloud = FakeProvider(model_id="cloud-1")
    cloud.spec = ModelSpec(provider="anthropic", model_id="cloud-1", context_window=1, supports_tools=False)
    reg.register(cloud)

    router = ModelRouter(reg)
    with pytest.raises(ValueError, match="local"):
        router.select(_project(privacy=PrivacyLevel.LOCAL_ONLY))
```

Run: `uv run pytest packages/atlas-core/atlas_core/tests/test_providers_registry.py -v`
Expected: ImportError on `ModelRegistry`/`ModelRouter`.

- [ ] **Step 2: Implement `registry.py`**

`packages/atlas-core/atlas_core/providers/registry.py`:
```python
"""Model registry + router.

The registry holds concrete provider instances keyed by model_id. The
router applies the Phase 1 simplified selection policy:

1. Explicit ``model_override`` from the request → that provider.
2. Project ``privacy_level == 'local_only'`` → first ``lmstudio`` provider.
3. Else → provider matching ``project.default_model``.
"""
from atlas_core.models.llm import ModelSpec
from atlas_core.models.projects import PrivacyLevel, Project
from atlas_core.providers.base import BaseModel


class ModelRegistry:
    """In-memory registry of provider instances."""

    def __init__(self) -> None:
        self._by_model_id: dict[str, BaseModel] = {}

    def register(self, provider: BaseModel) -> None:
        self._by_model_id[provider.spec.model_id] = provider

    def get(self, model_id: str) -> BaseModel | None:
        return self._by_model_id.get(model_id)

    def specs(self) -> list[ModelSpec]:
        return [p.spec for p in self._by_model_id.values()]

    def all(self) -> list[BaseModel]:
        return list(self._by_model_id.values())


class ModelRouter:
    """Phase 1 simplified routing — see module docstring."""

    def __init__(self, registry: ModelRegistry) -> None:
        self.registry = registry

    def select(
        self,
        project: Project,
        *,
        model_override: str | None = None,
    ) -> BaseModel:
        if model_override is not None:
            provider = self.registry.get(model_override)
            if provider is None:
                raise ValueError(f"Unknown model_override: {model_override}")
            return provider

        if project.privacy_level == PrivacyLevel.LOCAL_ONLY:
            for provider in self.registry.all():
                if provider.spec.provider == "lmstudio":
                    return provider
            raise ValueError("Project is local_only but no local (lmstudio) provider is registered")

        provider = self.registry.get(project.default_model)
        if provider is None:
            raise ValueError(
                f"Project default_model '{project.default_model}' not in registry"
            )
        return provider
```

- [ ] **Step 3: Run tests**

Run: `uv run pytest packages/atlas-core/atlas_core/tests/test_providers_registry.py -v`
Expected: 6 passed.

- [ ] **Step 4: Commit**

```bash
git add packages/atlas-core/atlas_core/providers/registry.py packages/atlas-core/atlas_core/tests/test_providers_registry.py
git commit -m "feat(atlas-core): add ModelRegistry + ModelRouter"
```

---

## Task 12: Add `Prompt Registry` + initial Jinja templates (TDD)

**Files:**
- Create: `packages/atlas-core/atlas_core/prompts/__init__.py`
- Create: `packages/atlas-core/atlas_core/prompts/registry.py`
- Create: `packages/atlas-core/atlas_core/prompts/templates/system/base.j2`
- Create: `packages/atlas-core/atlas_core/prompts/templates/system/project_context.j2`
- Create: `packages/atlas-core/atlas_core/prompts/templates/system/output_format.j2`
- Create: `packages/atlas-core/atlas_core/tests/test_prompts_registry.py`

- [ ] **Step 1: Create the package + templates dir**

```bash
mkdir -p packages/atlas-core/atlas_core/prompts/templates/system
```

`packages/atlas-core/atlas_core/prompts/__init__.py`:
```python
"""Jinja2-based prompt registry and composition."""

from atlas_core.prompts.registry import PromptRegistry, prompt_registry

__all__ = ["PromptRegistry", "prompt_registry"]
```

- [ ] **Step 2: Add the three templates**

`packages/atlas-core/atlas_core/prompts/templates/system/base.j2`:
```jinja
{# VARIABLES:
   agent_name: str = "ATLAS"
   current_date: str (required)
   user_name: str | None = None
#}
You are {{ agent_name }}, an AI-native assistant for a professional consulting practice.
Today is {{ current_date }}.
{% if user_name %}You are working with {{ user_name }}.{% endif %}

You are precise, direct, and methodologically rigorous. When uncertain, you say so.
```

`packages/atlas-core/atlas_core/prompts/templates/system/project_context.j2`:
```jinja
{# VARIABLES:
   project_name: str (required)
   project_description: str | None
   privacy_level: str (required)
#}
## Project Context
You are working in project: **{{ project_name }}**.
{% if project_description %}{{ project_description }}{% endif %}

Privacy mode: {{ privacy_level }}.
```

`packages/atlas-core/atlas_core/prompts/templates/system/output_format.j2`:
```jinja
Answer in clear, direct prose. Use Markdown for structure when helpful.
Cite sources when drawing on retrieved knowledge.
```

- [ ] **Step 3: Write failing tests**

`packages/atlas-core/atlas_core/tests/test_prompts_registry.py`:
```python
"""Tests for the PromptRegistry."""
import pytest

from atlas_core.prompts import PromptRegistry, prompt_registry


def test_registry_renders_known_template():
    out = prompt_registry.get(
        "system/base",
        agent_name="ATLAS",
        current_date="2026-04-27",
    )
    assert "ATLAS" in out
    assert "2026-04-27" in out


def test_registry_strict_undefined_raises_on_missing_var():
    with pytest.raises(Exception):
        prompt_registry.get("system/base")  # missing current_date


def test_registry_compose_joins_sections():
    out = prompt_registry.compose_system_prompt(
        sections=["system/base", "system/output_format"],
        agent_name="ATLAS",
        current_date="2026-04-27",
    )
    assert "ATLAS" in out
    assert "Markdown" in out
    # Sections joined by double newline
    assert "\n\n" in out


def test_registry_template_exists():
    assert prompt_registry.template_exists("system/base")
    assert not prompt_registry.template_exists("system/does_not_exist")


def test_registry_reload_clears_cache():
    """reload() must not raise; subsequent renders still work."""
    prompt_registry.reload()
    out = prompt_registry.get(
        "system/base",
        agent_name="X",
        current_date="2026-04-27",
    )
    assert "X" in out


def test_new_registry_can_be_constructed():
    reg = PromptRegistry()
    out = reg.get("system/output_format")  # template with no required vars
    assert "Markdown" in out
```

Run: `uv run pytest packages/atlas-core/atlas_core/tests/test_prompts_registry.py -v`
Expected: ImportError on `PromptRegistry`.

- [ ] **Step 4: Implement `registry.py`**

`packages/atlas-core/atlas_core/prompts/registry.py`:
```python
"""Jinja2-based prompt template registry.

Templates live under ``atlas_core/prompts/templates/``. The default
``prompt_registry`` singleton is instantiated at import time and used
throughout the app; tests can construct fresh ``PromptRegistry()``
instances if they need isolation.
"""
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined, TemplateNotFound

TEMPLATES_DIR = Path(__file__).parent / "templates"


class PromptRegistry:
    """Wraps a Jinja2 ``Environment`` and exposes simple render helpers."""

    def __init__(self, templates_dir: Path | None = None) -> None:
        self._templates_dir = templates_dir or TEMPLATES_DIR
        self._env = Environment(
            loader=FileSystemLoader(str(self._templates_dir)),
            undefined=StrictUndefined,
            trim_blocks=True,
            lstrip_blocks=True,
            autoescape=False,  # prompts are plaintext, not HTML
        )

    def get(self, template_path: str, **variables: object) -> str:
        """Render the named template with the given variables.

        ``template_path`` is relative to the templates root, without ``.j2``,
        e.g. ``"system/base"``.
        """
        template = self._env.get_template(f"{template_path}.j2")
        return template.render(**variables)

    def compose_system_prompt(self, sections: list[str], **variables: object) -> str:
        """Render multiple sections and join with double newlines."""
        return "\n\n".join(self.get(s, **variables) for s in sections)

    def template_exists(self, template_path: str) -> bool:
        try:
            self._env.get_template(f"{template_path}.j2")
            return True
        except TemplateNotFound:
            return False

    def reload(self) -> None:
        """Drop Jinja's compiled-template cache so on-disk edits take effect."""
        self._env.cache = {} if self._env.cache is not None else self._env.cache


# Module-level singleton — most callers use this.
prompt_registry = PromptRegistry()
```

- [ ] **Step 5: Run tests**

Run: `uv run pytest packages/atlas-core/atlas_core/tests/test_prompts_registry.py -v`
Expected: 6 passed.

- [ ] **Step 6: Commit**

```bash
git add packages/atlas-core/atlas_core/prompts/ packages/atlas-core/atlas_core/tests/test_prompts_registry.py
git commit -m "feat(atlas-core): add Jinja2 PromptRegistry + initial system templates"
```

---

## Task 13: Add `SystemPromptBuilder` (TDD)

**Files:**
- Create: `packages/atlas-core/atlas_core/prompts/builder.py`
- Create: `packages/atlas-core/atlas_core/tests/test_prompts_builder.py`

- [ ] **Step 1: Write failing tests**

`packages/atlas-core/atlas_core/tests/test_prompts_builder.py`:
```python
"""Tests for SystemPromptBuilder."""
from datetime import datetime, timezone
from uuid import uuid4

from atlas_core.models.projects import PrivacyLevel, Project, ProjectStatus
from atlas_core.prompts.builder import SystemPromptBuilder
from atlas_core.prompts.registry import prompt_registry


def _project(name: str = "TestProject") -> Project:
    return Project(
        id=uuid4(),
        user_id="matt",
        name=name,
        description="A description",
        status=ProjectStatus.ACTIVE,
        privacy_level=PrivacyLevel.CLOUD_OK,
        default_model="claude-sonnet-4-6",
        enabled_plugins=[],
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )


def test_build_includes_project_name_and_date():
    builder = SystemPromptBuilder(prompt_registry)
    out = builder.build(_project(name="MMM Project"))
    assert "MMM Project" in out
    assert "ATLAS" in out
    assert "Markdown" in out  # output_format section


def test_build_respects_user_name_override():
    builder = SystemPromptBuilder(prompt_registry)
    out = builder.build(_project(), user_name="Matt")
    assert "Matt" in out


def test_build_includes_privacy_level():
    builder = SystemPromptBuilder(prompt_registry)
    out = builder.build(_project())
    assert "cloud_ok" in out
```

Run: `uv run pytest packages/atlas-core/atlas_core/tests/test_prompts_builder.py -v`
Expected: ImportError.

- [ ] **Step 2: Implement `builder.py`**

`packages/atlas-core/atlas_core/prompts/builder.py`:
```python
"""Compose a system prompt for a chat turn from modular template sections."""
from datetime import datetime, timezone

from atlas_core.models.projects import Project
from atlas_core.prompts.registry import PromptRegistry


class SystemPromptBuilder:
    """Pick the right Jinja sections based on request context, render, join."""

    def __init__(self, registry: PromptRegistry) -> None:
        self.registry = registry

    def build(
        self,
        project: Project,
        *,
        user_name: str | None = None,
        current_date: str | None = None,
    ) -> str:
        sections = ["system/base", "system/project_context", "system/output_format"]
        variables = {
            "agent_name": "ATLAS",
            "current_date": current_date or datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            "user_name": user_name,
            "project_name": project.name,
            "project_description": project.description,
            "privacy_level": str(project.privacy_level),
        }
        return self.registry.compose_system_prompt(sections, **variables)
```

- [ ] **Step 3: Run tests**

Run: `uv run pytest packages/atlas-core/atlas_core/tests/test_prompts_builder.py -v`
Expected: 3 passed.

- [ ] **Step 4: Commit**

```bash
git add packages/atlas-core/atlas_core/prompts/builder.py packages/atlas-core/atlas_core/tests/test_prompts_builder.py
git commit -m "feat(atlas-core): add SystemPromptBuilder"
```

---

## Task 14: Wire registry construction + lifespan; add `GET /api/v1/models`

**Files:**
- Create: `apps/api/atlas_api/routers/models.py`
- Modify: `apps/api/atlas_api/main.py` (build registry in lifespan, include router)
- Modify: `apps/api/atlas_api/deps.py` (add `get_model_router`, `get_model_registry`)
- Create: `apps/api/atlas_api/tests/test_models_router.py`

- [ ] **Step 1: Extend `apps/api/atlas_api/deps.py`**

Add at the end of the file:

```python
from atlas_core.providers.registry import ModelRegistry, ModelRouter


def get_model_registry(request: Request) -> ModelRegistry:
    return request.app.state.model_registry


def get_model_router(request: Request) -> ModelRouter:
    return request.app.state.model_router
```

- [ ] **Step 2: Modify `apps/api/atlas_api/main.py` to build registry in lifespan**

Replace `apps/api/atlas_api/main.py` with:

```python
"""ATLAS FastAPI application entry point."""
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI

from atlas_api import __version__
from atlas_api.routers import models as models_router
from atlas_api.routers import projects as projects_router
from atlas_core.config import AtlasConfig
from atlas_core.db.session import create_engine_from_config, create_session_factory
from atlas_core.logging import configure_logging
from atlas_core.providers.anthropic import AnthropicProvider
from atlas_core.providers.lmstudio import LMStudioProvider
from atlas_core.providers.registry import ModelRegistry, ModelRouter

config = AtlasConfig()
configure_logging(environment=config.environment, log_level=config.log_level)
log = structlog.get_logger("atlas.api")


def _build_registry(cfg: AtlasConfig) -> ModelRegistry:
    """Construct the model registry from config — Anthropic + LM Studio."""
    reg = ModelRegistry()

    if cfg.llm.anthropic_api_key is not None:
        for model_id in (
            "claude-opus-4-7",
            "claude-sonnet-4-6",
            "claude-haiku-4-5-20251001",
        ):
            reg.register(
                AnthropicProvider(
                    api_key=cfg.llm.anthropic_api_key.get_secret_value(),
                    model_id=model_id,
                )
            )

    # LM Studio: register a single configured local_model. Discovery via
    # /v1/models is done lazily; the registry is populated up-front so the
    # router can find it. If the user didn't set ATLAS_LLM__LOCAL_MODEL,
    # we skip — that's a soft failure (logged), not a startup crash.
    if cfg.llm.local_model:
        reg.register(
            LMStudioProvider(
                base_url=str(cfg.llm.lmstudio_base_url),
                model_id=cfg.llm.local_model,
            )
        )
    else:
        log.warning(
            "lmstudio.skipped_registration",
            reason="ATLAS_LLM__LOCAL_MODEL not set; set it to the loaded LM Studio model",
        )

    return reg


@asynccontextmanager
async def lifespan(app: FastAPI):
    engine = create_engine_from_config(config)
    registry = _build_registry(config)
    app.state.config = config
    app.state.engine = engine
    app.state.session_factory = create_session_factory(engine)
    app.state.model_registry = registry
    app.state.model_router = ModelRouter(registry)
    log.info(
        "api.startup",
        environment=config.environment,
        version=__version__,
        registered_models=[s.model_id for s in registry.specs()],
    )
    try:
        yield
    finally:
        log.info("api.shutdown")
        await engine.dispose()


app = FastAPI(
    title="ATLAS API",
    version=__version__,
    description="Personal AI consultant — Phase 1 Foundation",
    lifespan=lifespan,
)

app.include_router(projects_router.router, prefix="/api/v1")
app.include_router(models_router.router, prefix="/api/v1")


@app.get("/health")
async def health() -> dict[str, str]:
    """Liveness probe."""
    return {
        "status": "ok",
        "environment": config.environment,
        "version": __version__,
    }
```

- [ ] **Step 3: Implement the `/api/v1/models` router**

`apps/api/atlas_api/routers/models.py`:
```python
"""GET /api/v1/models — list registered LLM providers."""
from fastapi import APIRouter, Depends

from atlas_api.deps import get_model_registry
from atlas_core.models.llm import ModelSpec
from atlas_core.providers.registry import ModelRegistry

router = APIRouter(tags=["models"])


@router.get("/models", response_model=list[ModelSpec])
async def list_models(
    registry: ModelRegistry = Depends(get_model_registry),
) -> list[ModelSpec]:
    return registry.specs()
```

- [ ] **Step 4: Write the integration test**

`apps/api/atlas_api/tests/test_models_router.py`:
```python
"""Integration test for /api/v1/models."""


async def test_list_models_returns_registered_specs(app_client):
    response = await app_client.get("/api/v1/models")
    assert response.status_code == 200
    body = response.json()
    assert isinstance(body, list)
    # The conftest test environment has ATLAS_LLM__ANTHROPIC_API_KEY unset
    # by default — registry is empty unless a test sets it. Just verify the
    # response shape; specific contents are environment-dependent.
    for entry in body:
        assert "provider" in entry
        assert "model_id" in entry
        assert "context_window" in entry
```

- [ ] **Step 5: Run tests**

Run: `uv run pytest apps/api/atlas_api/tests/test_models_router.py apps/api/atlas_api/tests/test_health.py apps/api/atlas_api/tests/test_projects_router.py -v 2>&1 | tail -25`
Expected: all pass (the existing health + projects tests must not regress).

- [ ] **Step 6: Commit**

```bash
git add apps/api/atlas_api/main.py apps/api/atlas_api/deps.py apps/api/atlas_api/routers/models.py apps/api/atlas_api/tests/test_models_router.py
git commit -m "feat(atlas-api): wire ModelRegistry in lifespan; add GET /api/v1/models"
```

---

## Task 15: Add WebSocket chat handler with FakeProvider initially (TDD)

**Files:**
- Create: `apps/api/atlas_api/ws/__init__.py`
- Create: `apps/api/atlas_api/ws/chat.py`
- Modify: `apps/api/atlas_api/main.py` (mount the WS endpoint)
- Create: `apps/api/atlas_api/tests/test_ws_chat.py`

- [ ] **Step 1: Create the ws package**

```bash
mkdir -p apps/api/atlas_api/ws
touch apps/api/atlas_api/ws/__init__.py
```

- [ ] **Step 2: Write failing tests**

`apps/api/atlas_api/tests/test_ws_chat.py`:
```python
"""Integration tests for the chat WebSocket using FastAPI TestClient."""
import json
from uuid import UUID

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy import select

from atlas_api.deps import get_model_router, get_session
from atlas_api.main import app
from atlas_core.db.orm import MessageORM, SessionORM
from atlas_core.providers import FakeProvider
from atlas_core.providers.registry import ModelRegistry, ModelRouter


@pytest.fixture
def fake_router():
    """ModelRouter that always returns a FakeProvider regardless of project config."""
    reg = ModelRegistry()
    fp = FakeProvider(model_id="fake-1", token_chunks=["hello", " ", "world"])
    reg.register(fp)
    router = ModelRouter(reg)
    # Override select() to ignore project policy and always return our fake
    router.select = lambda project, model_override=None: fp  # type: ignore[assignment]
    return router


@pytest_asyncio.fixture
async def ws_client(db_session, fake_router):
    """FastAPI TestClient with both session and router overrides.

    Async fixture (consumes the async ``db_session`` fixture). The TestClient
    itself is sync, but using it inside an async fixture is fine — it spawns
    its own ASGI runner thread internally.
    """
    async def _override_session():
        yield db_session

    def _override_router():
        return fake_router

    app.dependency_overrides[get_session] = _override_session
    app.dependency_overrides[get_model_router] = _override_router

    client = TestClient(app)
    yield client
    app.dependency_overrides.clear()


async def _seed_project(db_session) -> UUID:
    """Insert a project row and return its id."""
    from atlas_core.db.orm import ProjectORM
    p = ProjectORM(
        user_id="matt",
        name="WSTest",
        default_model="fake-1",
    )
    db_session.add(p)
    await db_session.flush()
    return p.id


@pytest.mark.asyncio
async def test_ws_chat_streams_tokens_and_persists_messages(ws_client, db_session):
    project_id = await _seed_project(db_session)
    # Need a session row. WS handler creates one if path id not in DB.
    from uuid import uuid4
    session_id = uuid4()

    with ws_client.websocket_connect(f"/api/v1/ws/{session_id}") as ws:
        ws.send_json({
            "type": "chat.message",
            "payload": {"text": "hi", "project_id": str(project_id)},
        })

        events = []
        while True:
            msg = ws.receive_json()
            events.append(msg)
            if msg["type"] in ("chat.done", "chat.error"):
                break

    types = [e["type"] for e in events]
    assert "chat.token" in types
    assert types[-1] == "chat.done"
    # FakeProvider yields 3 token chunks
    token_count = sum(1 for t in types if t == "chat.token")
    assert token_count == 3

    # Persistence: 2 message rows (user + assistant) + 1 session row
    sessions = (await db_session.execute(select(SessionORM))).scalars().all()
    messages = (await db_session.execute(select(MessageORM))).scalars().all()
    assert len(sessions) == 1
    assert len(messages) == 2
    roles = {m.role for m in messages}
    assert roles == {"user", "assistant"}


@pytest.mark.asyncio
async def test_ws_chat_emits_error_event_on_provider_failure(ws_client, db_session, fake_router):
    project_id = await _seed_project(db_session)
    # Reconfigure the fake to fail
    failing = FakeProvider(model_id="fake-1", error_on_call=True)
    fake_router.select = lambda project, model_override=None: failing  # type: ignore[assignment]

    from uuid import uuid4
    session_id = uuid4()

    with ws_client.websocket_connect(f"/api/v1/ws/{session_id}") as ws:
        ws.send_json({
            "type": "chat.message",
            "payload": {"text": "hi", "project_id": str(project_id)},
        })
        msg = ws.receive_json()
        # The first event should be the error
        assert msg["type"] == "chat.error"


@pytest.mark.asyncio
async def test_ws_chat_rejects_unknown_message_type(ws_client, db_session):
    project_id = await _seed_project(db_session)
    from uuid import uuid4
    with ws_client.websocket_connect(f"/api/v1/ws/{uuid4()}") as ws:
        ws.send_json({"type": "weird.unknown", "payload": {}})
        msg = ws.receive_json()
        assert msg["type"] == "chat.error"
        assert "unknown" in msg["payload"]["message"].lower()
```

Run: `uv run pytest apps/api/atlas_api/tests/test_ws_chat.py -v`
Expected: ImportError (the WS endpoint doesn't exist yet).

- [ ] **Step 3: Implement the WS handler**

`apps/api/atlas_api/ws/chat.py`:
```python
"""WebSocket chat endpoint.

Per-message flow:
  1. Receive a JSON message; validate against the WS protocol.
  2. Load (or create) the Session row for this WebSocket connection.
  3. Load the Project + recent Messages for context.
  4. Build the system prompt via SystemPromptBuilder.
  5. Route to a provider via ModelRouter.
  6. Stream ModelEvents → translate to StreamEvents → send to client.
  7. On done: persist user + assistant Message rows + ModelUsage row.
"""
from __future__ import annotations

import time
from collections.abc import AsyncIterator
from uuid import UUID, uuid4

import structlog
from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from atlas_api.deps import get_model_router, get_session, get_settings
from atlas_core.config import AtlasConfig
from atlas_core.db.converters import message_from_orm
from atlas_core.db.orm import MessageORM, ModelUsageORM, ProjectORM, SessionORM
from atlas_core.models.llm import ModelEventType
from atlas_core.models.messages import ChatRequest, StreamEvent, StreamEventType
from atlas_core.models.sessions import MessageRole
from atlas_core.prompts.builder import SystemPromptBuilder
from atlas_core.prompts.registry import prompt_registry
from atlas_core.providers.registry import ModelRouter

router = APIRouter()
log = structlog.get_logger("atlas.api.ws")
prompt_builder = SystemPromptBuilder(prompt_registry)

CONTEXT_WINDOW_TURNS = 20  # Plan 5 will adapt this dynamically


@router.websocket("/ws/{session_id}")
async def chat_ws(
    websocket: WebSocket,
    session_id: UUID,
    db: AsyncSession = Depends(get_session),
    model_router: ModelRouter = Depends(get_model_router),
    settings: AtlasConfig = Depends(get_settings),
) -> None:
    await websocket.accept()
    sequence = 0
    structlog.contextvars.bind_contextvars(session_id=str(session_id))
    log.info("ws.connect")

    try:
        while True:
            try:
                raw = await websocket.receive_json()
            except WebSocketDisconnect:
                log.info("ws.disconnect")
                return

            msg_type = raw.get("type")
            payload = raw.get("payload", {})

            if msg_type != "chat.message":
                sequence = await _send(
                    websocket,
                    StreamEventType.ERROR,
                    {"code": "unknown_type", "message": f"unknown message type: {msg_type}"},
                    sequence,
                )
                continue

            try:
                req = ChatRequest.model_validate(payload)
            except Exception as e:
                sequence = await _send(
                    websocket,
                    StreamEventType.ERROR,
                    {"code": "invalid_payload", "message": str(e)},
                    sequence,
                )
                continue

            try:
                sequence = await _handle_chat_message(
                    websocket, session_id, req, db, model_router, settings, sequence
                )
            except Exception as e:
                log.exception("ws.unhandled_error")
                sequence = await _send(
                    websocket,
                    StreamEventType.ERROR,
                    {"code": "internal_error", "message": str(e)},
                    sequence,
                )
    finally:
        structlog.contextvars.unbind_contextvars("session_id")


async def _handle_chat_message(
    websocket: WebSocket,
    session_id: UUID,
    req: ChatRequest,
    db: AsyncSession,
    model_router: ModelRouter,
    settings: AtlasConfig,
    sequence: int,
) -> int:
    # 1. Resolve the Project (must exist for this user)
    project = await db.get(ProjectORM, req.project_id)
    if project is None or project.user_id != settings.user_id:
        return await _send(
            websocket,
            StreamEventType.ERROR,
            {"code": "project_not_found", "message": "project not found or unauthorized"},
            sequence,
        )

    # 2. Ensure the Session row exists
    session_row = await db.get(SessionORM, session_id)
    if session_row is None:
        session_row = SessionORM(
            id=session_id,
            user_id=settings.user_id,
            project_id=project.id,
            model=req.model_override or project.default_model,
        )
        db.add(session_row)
        await db.flush()

    # 3. Build the message history for the model
    history_rows = await _load_recent_messages(db, session_id, limit=CONTEXT_WINDOW_TURNS)
    system_prompt = prompt_builder.build(_project_to_pydantic(project))
    model_messages = _assemble_messages(system_prompt, history_rows, req.text)

    # 4. Persist the user turn before streaming the assistant response
    user_row = MessageORM(
        user_id=settings.user_id,
        session_id=session_id,
        role=MessageRole.USER.value,
        content=req.text,
    )
    db.add(user_row)
    await db.flush()

    # 5. Route to a provider
    try:
        provider = model_router.select(
            _project_to_pydantic(project), model_override=req.model_override
        )
    except ValueError as e:
        return await _send(
            websocket,
            StreamEventType.ERROR,
            {"code": "no_provider", "message": str(e)},
            sequence,
        )

    # 6. Stream events
    assistant_text_parts: list[str] = []
    usage: dict | None = None
    started = time.monotonic()

    async for event in provider.stream(
        messages=model_messages,
        temperature=req.temperature,
    ):
        if event.type == ModelEventType.TOKEN:
            text = event.data.get("text", "")
            assistant_text_parts.append(text)
            sequence = await _send(
                websocket, StreamEventType.TOKEN, {"token": text}, sequence
            )
        elif event.type == ModelEventType.TOOL_CALL:
            sequence = await _send(
                websocket, StreamEventType.TOOL_CALL, event.data, sequence
            )
        elif event.type == ModelEventType.TOOL_RESULT:
            sequence = await _send(
                websocket, StreamEventType.TOOL_RESULT, event.data, sequence
            )
        elif event.type == ModelEventType.ERROR:
            return await _send(
                websocket, StreamEventType.ERROR, event.data, sequence
            )
        elif event.type == ModelEventType.DONE:
            usage = event.data.get("usage", {})

    latency_ms = int((time.monotonic() - started) * 1000)
    full_assistant_text = "".join(assistant_text_parts)

    # 7. Persist the assistant turn + usage
    assistant_row = MessageORM(
        user_id=settings.user_id,
        session_id=session_id,
        role=MessageRole.ASSISTANT.value,
        content=full_assistant_text,
        model=provider.spec.model_id,
        token_count=(usage or {}).get("output_tokens"),
    )
    db.add(assistant_row)

    if usage:
        db.add(
            ModelUsageORM(
                user_id=settings.user_id,
                session_id=session_id,
                project_id=project.id,
                provider=usage.get("provider", provider.spec.provider),
                model_id=usage.get("model_id", provider.spec.model_id),
                input_tokens=usage.get("input_tokens", 0),
                output_tokens=usage.get("output_tokens", 0),
                latency_ms=usage.get("latency_ms", latency_ms),
            )
        )

    await db.flush()

    sequence = await _send(
        websocket,
        StreamEventType.DONE,
        {
            "usage": usage or {},
            "model": provider.spec.model_id,
            "latency_ms": latency_ms,
        },
        sequence,
    )
    return sequence


async def _send(
    websocket: WebSocket,
    type_: StreamEventType,
    payload: dict,
    sequence: int,
) -> int:
    event = StreamEvent(type=type_, payload=payload, sequence=sequence)
    await websocket.send_json(event.model_dump(mode="json"))
    return sequence + 1


async def _load_recent_messages(
    db: AsyncSession, session_id: UUID, limit: int
) -> list[MessageORM]:
    result = await db.execute(
        select(MessageORM)
        .where(MessageORM.session_id == session_id)
        .order_by(desc(MessageORM.created_at))
        .limit(limit)
    )
    rows = list(result.scalars().all())
    rows.reverse()  # ascending
    return rows


def _assemble_messages(
    system_prompt: str,
    history: list[MessageORM],
    new_user_text: str,
) -> list[dict]:
    out: list[dict] = [{"role": "system", "content": system_prompt}]
    for row in history:
        out.append({"role": row.role, "content": row.content})
    out.append({"role": "user", "content": new_user_text})
    return out


def _project_to_pydantic(row: ProjectORM):
    """Local converter to avoid circular imports — uses the public converter."""
    from atlas_core.db.converters import project_from_orm
    return project_from_orm(row)
```

- [ ] **Step 4: Mount the router in `main.py`**

In `apps/api/atlas_api/main.py`, add to the imports:
```python
from atlas_api.ws import chat as ws_chat
```

and after the existing `app.include_router(...)` calls, add:
```python
app.include_router(ws_chat.router, prefix="/api/v1")
```

- [ ] **Step 5: Run the WS tests**

Make sure docker-compose services are up.

Run: `uv run pytest apps/api/atlas_api/tests/test_ws_chat.py -v`
Expected: 3 passed.

If the WS tests hang or get an "event loop closed" error, the fixture wiring may need adjustment. The `app_client` fixture in conftest.py was async (httpx-based); the WS tests use a sync `TestClient` and do their own setup via `ws_client` fixture above.

- [ ] **Step 6: Run the full suite to verify no regressions**

Run: `uv run pytest -v 2>&1 | tail -10`
Expected: all tests pass.

- [ ] **Step 7: Commit**

```bash
git add apps/api/atlas_api/ws/ apps/api/atlas_api/main.py apps/api/atlas_api/tests/test_ws_chat.py
git commit -m "feat(atlas-api): add /api/v1/ws/{session_id} chat WebSocket"
```

---

## Task 16: Verify the routers __init__ has `models` and the deps wiring works end-to-end

**Files:** none modified; verification only.

- [ ] **Step 1: Smoke import**

Run:
```bash
ATLAS_DB__DATABASE_URL=postgresql://atlas:atlas@localhost:5432/atlas \
  uv run python -c "from atlas_api.main import app; print([r.path for r in app.routes if r.path.startswith('/api')])"
```
Expected: prints all 5 project routes + `/api/v1/models` + `/api/v1/ws/{session_id}` (the WS path renders without curly braces depending on FastAPI version — both forms acceptable).

- [ ] **Step 2: Run the full test suite**

```bash
uv run pytest -v 2>&1 | tail -10
```

Expected: all tests pass.

- [ ] **Step 3: Run ruff**

```bash
uv run ruff check . && uv run ruff format --check .
```

If issues are reported, run `ruff check --fix` and `ruff format`, then commit:
```bash
git add -u
git commit -m "chore: ruff autofix and format"
```

---

## Task 17: End-to-end smoke against the live API with a real provider

**Files:** none.

- [ ] **Step 1: Confirm dev DB has the new tables**

```bash
ATLAS_DB__DATABASE_URL=postgresql://atlas:atlas@localhost:5432/atlas \
  uv run alembic upgrade head
docker exec atlas-postgres psql -U atlas -d atlas -c "\dt"
```
Expected: `projects`, `sessions`, `messages`, `model_usage`, `alembic_version`.

- [ ] **Step 2: Set ATLAS_LLM__LOCAL_MODEL in .env so LM Studio is registered**

Edit `.env` and add (or update) the line:
```
ATLAS_LLM__LOCAL_MODEL=<the exact model id loaded in LM Studio>
```

You can list LM Studio's loaded models by running:
```bash
curl -s http://100.91.155.118:1234/v1/models | python3 -m json.tool
```

Pick one model_id from the response and put it in `.env`. If LM Studio has nothing loaded, skip the LM Studio half of this task.

- [ ] **Step 3: Start the API**

```bash
uv run uvicorn atlas_api.main:app --host 127.0.0.1 --port 8000
```

Watch for the `api.startup` log line and confirm `registered_models` includes both Anthropic IDs and your LM Studio model_id.

- [ ] **Step 4: List models via REST**

```bash
curl -s http://127.0.0.1:8000/api/v1/models | python3 -m json.tool
```

Expected: array containing `claude-opus-4-7`, `claude-sonnet-4-6`, `claude-haiku-4-5-20251001`, plus your LM Studio model.

- [ ] **Step 5: Create a project**

```bash
PROJECT_ID=$(curl -s -X POST http://127.0.0.1:8000/api/v1/projects \
  -H 'Content-Type: application/json' \
  -d '{"name":"WS Smoke","default_model":"claude-sonnet-4-6"}' \
  | python3 -c "import sys,json;print(json.load(sys.stdin)['id'])")
echo "PROJECT_ID=$PROJECT_ID"
```

- [ ] **Step 6: Chat via wscat or a tiny Python script**

If `wscat` is installed:
```bash
SESSION_ID=$(uv run python -c "from uuid import uuid4; print(uuid4())")
wscat -c ws://127.0.0.1:8000/api/v1/ws/$SESSION_ID
# Once connected:
{"type":"chat.message","payload":{"text":"Say hello in three words.","project_id":"<PROJECT_ID>"}}
```

If you don't have `wscat`, use this throwaway script (paste into a file `/tmp/smoke_ws.py`):
```python
import asyncio
import json
import sys
import websockets

SESSION_ID = sys.argv[1]
PROJECT_ID = sys.argv[2]
TEXT = " ".join(sys.argv[3:]) or "Say hello in three words."


async def main():
    url = f"ws://127.0.0.1:8000/api/v1/ws/{SESSION_ID}"
    async with websockets.connect(url) as ws:
        await ws.send(json.dumps({
            "type": "chat.message",
            "payload": {"text": TEXT, "project_id": PROJECT_ID},
        }))
        while True:
            raw = await ws.recv()
            event = json.loads(raw)
            print(event["type"], json.dumps(event["payload"])[:120])
            if event["type"] in ("chat.done", "chat.error"):
                break


asyncio.run(main())
```

Run:
```bash
SESSION_ID=$(uv run python -c "from uuid import uuid4; print(uuid4())")
uv run python /tmp/smoke_ws.py "$SESSION_ID" "$PROJECT_ID"
```

Expected: a stream of `chat.token` events ending with `chat.done`. You should see actual response text from Claude.

- [ ] **Step 7: Verify persistence**

```bash
docker exec atlas-postgres psql -U atlas -d atlas -c "SELECT id, role, LEFT(content, 60) AS preview, model FROM messages ORDER BY created_at;"
docker exec atlas-postgres psql -U atlas -d atlas -c "SELECT provider, model_id, input_tokens, output_tokens, latency_ms FROM model_usage;"
```

Expected: 2 message rows (1 user, 1 assistant with the LLM's response) + 1 model_usage row with non-zero token counts and latency.

- [ ] **Step 8: Test LM Studio path (if registered)**

Update the project's default_model:
```bash
curl -s -X PATCH "http://127.0.0.1:8000/api/v1/projects/$PROJECT_ID" \
  -H 'Content-Type: application/json' \
  -d '{"default_model":"<your-lmstudio-model-id>"}' | python3 -m json.tool
```

Then re-run the chat from Step 6 with a fresh `SESSION_ID`. Expected: tokens stream from the local model. Verify a new `model_usage` row was added with `provider=lmstudio`.

- [ ] **Step 9: Cleanup**

```bash
docker exec atlas-postgres psql -U atlas -d atlas -c "DELETE FROM model_usage;"
docker exec atlas-postgres psql -U atlas -d atlas -c "DELETE FROM messages;"
docker exec atlas-postgres psql -U atlas -d atlas -c "DELETE FROM sessions;"
docker exec atlas-postgres psql -U atlas -d atlas -c "DELETE FROM projects WHERE name='WS Smoke';"
```

Stop the API (Ctrl-C in its terminal).

---

## Definition of Done for Plan 3

1. `uv sync --all-packages` succeeds.
2. `uv run pytest -v` passes — approximately **97 tests** (53 prior + 44 new across messages/sessions/llm/converters/providers/router/registry/prompts/builder/ws).
3. `uv run ruff check .` and `ruff format --check .` clean.
4. `alembic upgrade head` applies migrations 0001 → 0002 cleanly to a fresh DB; `alembic downgrade base` is a clean roundtrip.
5. `GET /api/v1/models` returns the registered providers (Anthropic IDs + LM Studio model if configured).
6. WebSocket chat works end-to-end:
   - Connect to `/api/v1/ws/{uuid}`
   - Send `chat.message` with `project_id`
   - Receive a stream of `chat.token` events
   - Receive `chat.done` with `usage` payload
   - Both user + assistant `Message` rows persist; one `ModelUsage` row records the token counts
7. Both Anthropic and LM Studio (when LM Studio is reachable + has a model loaded) work via the same WebSocket path — only the project's `default_model` changes.
8. Tool plumbing: `chat.tool_use` and `chat.tool_result` event types exist in the protocol and are forwarded by the WS handler if the provider emits them. Phase 1 providers never emit them; the path is exercised by tests in Plan 3 only via the type system, not by real events.
9. Cancellation: closing the WebSocket mid-stream causes the handler to exit cleanly via `WebSocketDisconnect`. The partial assistant message is **not** persisted.

When all DoD items pass, this plan is complete. Plan 4 (knowledge layer — embeddings, vector store, ingestion, retrieval) builds on top.
