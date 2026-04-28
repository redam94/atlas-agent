# ATLAS Phase 1 — Foundation Design

**Date:** 2026-04-26
**Status:** Approved
**Amends/Implements:** `docs/atlas_design_document.md` v0.1.0 (§14 Phase 1) and selected sections of `docs/atlas_design_addendum.md` v0.2 (B. Pydantic-First, E. Prompt Management)

---

## 1. Goal & Scope

### Goal

A working personal-AI chat experience for a single user (Matt). Open browser, pick a project, type a message, see it stream back, with answers grounded in PDFs and markdown notes that have been ingested into the project. Establishes the architectural patterns (Pydantic-first, provider abstraction, prompt registry, streaming-first) that all later phases plug into.

### In Scope

- FastAPI backend with WebSocket streaming chat
- LLM provider abstraction with two providers: Anthropic (cloud) and LM Studio (local, OpenAI-compatible at `http://100.91.155.118:1234/v1`)
- In-process embeddings via `sentence-transformers` (`BAAI/bge-small-en-v1.5`, ~130MB)
- Vector RAG via ChromaDB behind a `VectorStore` abstract interface
- Ingestion: paste-text/markdown notes and PDF upload (PyMuPDF)
- React frontend (Vite + TS + Tailwind v4 + shadcn/ui): collapsible sidebar, streaming chat, RAG context drawer, inline tool-use cards
- Postgres 16 (projects, sessions, messages, model_usage, ingestion_jobs)
- Redis 7 (session state, streaming buffers)
- Single-user, no auth — hardcoded `user_id = "matt"` everywhere a `user_id` would go
- Multi-user-ready schema: every relevant table has a `user_id` column from day 1
- Pydantic-first model layer (from v0.2 §B)
- Jinja-based prompt registry (from v0.2 §E)
- Local dev environment via `docker-compose`
- `uv` workspace for the Python monorepo

### Out of Scope (deferred)

- Knowledge graph (Neo4j), graph RAG, PageRank scoring — Phase 2
- Plugins (Gmail, GitHub, GCP, Discord) — Phase 3
- LangGraph agent graphs and code execution sandbox — Phase 4
- Audio assistant (Whisper STT, TTS, wake word) — Phase 4
- OAuth, FastAPI Users, login flows — when needed
- URL/web scraping (Trafilatura, Playwright) — Phase 2
- Cross-encoder reranking, BM25 — Phase 2
- OpenAI and Gemini providers — added when needed
- Terraform / GCP deployment — Phase 5
- Celery — added the first time an ingestion job justifies it (likely Phase 2 or 3)
- Cost dashboard UI (we log `model_usage` but do not visualize) — Phase 4
- Conversation summarization for context-window overflow — defer; sliding window only

---

## 2. Architecture

```
┌─────────────────────────────────────────────────────────┐
│ React Frontend (Vite, TypeScript)                       │
│ Sidebar · Chat · RAG drawer · Tool-use cards            │
└──────────────────┬──────────────────────────────────────┘
                   │ HTTP + WebSocket
                   ▼
┌─────────────────────────────────────────────────────────┐
│ FastAPI Gateway (apps/api)                              │
│ /api/v1/projects  /api/v1/knowledge                     │
│ /api/v1/models    /api/v1/ws/{session_id}               │
│ Auth stub (single hardcoded user_id="matt")             │
└──────────┬──────────────────────────┬───────────────────┘
           │                          │
           ▼                          ▼
┌─────────────────────┐    ┌──────────────────────┐
│ atlas-core          │    │ atlas-knowledge      │
│ - Pydantic models   │    │ - Pydantic models    │
│ - Provider abstrac. │    │ - Ingestion pipeline │
│ - Model router      │    │ - Embedding service  │
│ - Prompt registry   │    │ - VectorStore iface  │
│ - Agent loop        │    │ - Retriever          │
└──────────┬──────────┘    └──────────┬───────────┘
           │                          │
           ▼                          ▼
┌─────────────────────────────────────────────────────────┐
│ Data Layer                                              │
│ Postgres 16 (service)   ·  Redis 7 (service)            │
│ ChromaDB (in-process library; persists to ./data/chroma)│
└─────────────────────────────────────────────────────────┘
```

Note: ChromaDB runs in **embedded mode** — it's an in-process Python library, not a separate service. The volume mount for `./data/chroma` is what makes it durable across container restarts. (Switching to client/server mode is a config change in `atlas-knowledge/vector/chroma.py` if we ever need it.)

Two Python packages in Phase 1:

- `atlas-core` — the agent layer. Provider abstraction, model router, agent loop, prompt registry, shared Pydantic base models, config.
- `atlas-knowledge` — the RAG layer. Ingestion pipeline, chunker, embedding service, vector store interface and ChromaDB implementation, retriever.

`atlas-plugins` does not exist in Phase 1.

---

## 3. Pydantic Model Layer (foundational, from v0.2 §B)

All data crossing a boundary (HTTP, WebSocket, DB, LLM) is a Pydantic v2 model. Single source of truth — no `dict[str, Any]` shuttling between layers.

### Base classes (`atlas-core/atlas_core/models/base.py`)

```python
class AtlasModel(BaseModel):
    model_config = ConfigDict(
        strict=True,
        frozen=True,
        populate_by_name=True,
        use_enum_values=True,
        validate_assignment=True,
    )

class MutableAtlasModel(AtlasModel):
    model_config = ConfigDict(**{**AtlasModel.model_config, "frozen": False})

class TimestampedModel(AtlasModel):
    id: UUID = Field(default_factory=uuid4)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
```

### `atlas-core/atlas_core/models/`

- `base.py` — `AtlasModel`, `MutableAtlasModel`, `TimestampedModel`
- `projects.py` — `Project`, `ProjectCreate`, `ProjectUpdate`, `PrivacyLevel`
- `sessions.py` — `Session`, `SessionState`
- `messages.py` — `Message`, `MessageRole`, `ChatRequest`, `StreamEvent`, `StreamEventType`
- `models.py` — `ModelSpec`, `ModelEvent`, `ModelEventType`, `ModelUsage`
- `errors.py` — `AtlasError`, `ProviderError`, `ValidationError`

### `atlas-knowledge/atlas_knowledge/models/`

- `nodes.py` — `KnowledgeNode` (base), `DocumentNode`, `ChunkNode`
- `ingestion.py` — `IngestRequest`, `IngestResult`, `IngestStatus`, `ParsedDocument`
- `retrieval.py` — `RetrievalQuery`, `RetrievalResult`, `RagContext`, `ScoredChunk`
- `embeddings.py` — `EmbeddingRequest`, `EmbeddingResult`

### Configuration

`atlas-core/atlas_core/config.py` uses `pydantic-settings`:

```python
class LLMConfig(BaseSettings):
    anthropic_api_key: SecretStr | None = None
    lmstudio_base_url: AnyUrl = "http://100.91.155.118:1234/v1"
    default_model: str = "claude-sonnet-4-6"
    local_model: str | None = None     # auto-discovered from LM Studio /v1/models if unset

class DatabaseConfig(BaseSettings):
    database_url: SecretStr
    redis_url: AnyUrl = "redis://localhost:6379"
    chroma_path: str = "./data/chroma"

class AtlasConfig(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_nested_delimiter="__", case_sensitive=False
    )
    llm: LLMConfig = LLMConfig()
    db: DatabaseConfig = DatabaseConfig()
    environment: Literal["development", "production"] = "development"
    log_level: str = "INFO"
    user_id: str = "matt"   # single-user stub
```

---

## 4. LLM Provider Abstraction

```
atlas-core/atlas_core/models/providers/
├── base.py        # BaseModel ABC: stream() yields ModelEvent; embed() optional
├── anthropic.py   # AnthropicProvider using `anthropic` SDK
├── lmstudio.py    # LMStudioProvider using AsyncOpenAI pointed at LM Studio URL
└── registry.py    # ModelRegistry, ModelRouter
```

### Interface

```python
class BaseModel(ABC):
    provider: str
    model_id: str
    context_window: int
    supports_tools: bool
    supports_streaming: bool = True

    @abstractmethod
    async def stream(
        self,
        messages: list[Message],
        tools: list[ToolSchema] | None = None,
        temperature: float = 0.7,
    ) -> AsyncIterator[ModelEvent]: ...
```

### Normalized streaming events

`ModelEvent` types — provider-agnostic:
- `token` — incremental text
- `tool_call` — model invokes a tool (Phase 1: never emitted, schema present for Phase 3)
- `tool_result` — result of tool invocation (same)
- `done` — final usage payload (input/output tokens, model_id, latency)
- `error` — provider error wrapped as `ProviderError`

### Router policy (Phase 1 simplified)

```
1. If request.model_override is set → return that model
2. If project.privacy_level == "local_only" → return LM Studio model
3. Otherwise → return project.default_model
```

Cost-based, task-based, and capability-based routing from v0.1 §6.3 are deferred. The router signature accepts the future arguments but ignores them.

---

## 5. Prompt Registry

```
atlas-core/atlas_core/prompts/
├── registry.py            # PromptRegistry (Jinja2 env, StrictUndefined, hot-reload)
├── builder.py             # SystemPromptBuilder.compose(...)
└── templates/
    ├── system/
    │   ├── base.j2
    │   ├── project_context.j2
    │   ├── rag_instructions.j2
    │   └── output_format.j2
    └── rag/
        └── context_injection.j2
```

`StrictUndefined` so missing variables fail loud. Each template documents its variables in a leading Jinja comment block.

`SystemPromptBuilder.compose(request, project, rag_context)` selects sections, renders each, and joins with double newlines. Phase 1 ships only the templates above; later phases add their own (`tasks/`, `agents/`, `plugins/`).

Hot-reload in dev: `registry.reload()` clears the Jinja cache without restart.

---

## 6. Knowledge & RAG

### Ingestion pipeline

Linear, async, runs in a FastAPI background task in Phase 1 (Celery deferred):

```
IngestRequest
  → Parser (PyMuPDF for PDF, passthrough for markdown)
  → ParsedDocument
  → SemanticChunker
      target ~512 tokens, 128 overlap
      respects: paragraph breaks, markdown headings, list items
  → ChunkNode[]
  → EmbeddingService.embed_batch(chunks)
  → VectorStore.upsert(chunks, embeddings)
  → ingestion_jobs row → status = "completed"
```

Failures at any stage → `ingestion_jobs.status = "failed"`, `error` populated.

### Embedding service

```
atlas-knowledge/atlas_knowledge/embeddings/
├── service.py    # EmbeddingService (interface)
└── providers/
    └── local.py  # SentenceTransformersEmbedder (BGE-small)
```

Model loaded lazily on first call, cached for the process lifetime. Batched (default batch size 32). No external embedding service in Phase 1.

### Retrieval (Phase 1 — dense only)

```
RetrievalQuery
  → embed query (same EmbeddingService)
  → VectorStore.search(top_k=8, filter={project_id})
  → list[ScoredChunk]
  → RetrievalResult assembled (chunk text + parent doc title + score)
  → rendered into context via rag/context_injection.j2
  → injected into system prompt
```

BM25, RRF fusion, graph expansion, and cross-encoder reranking are deferred to Phase 2. The `Retriever` interface is structured to accept additional pipeline stages.

### VectorStore interface

```python
class VectorStore(ABC):
    @abstractmethod
    async def upsert(
        self,
        chunks: list[ChunkNode],
        embeddings: list[list[float]],
    ) -> None: ...

    @abstractmethod
    async def search(
        self,
        query_embedding: list[float],
        top_k: int = 8,
        filter: dict | None = None,
    ) -> list[ScoredChunk]: ...

    @abstractmethod
    async def delete(self, ids: list[UUID]) -> None: ...
```

ChromaDB implementation in `atlas-knowledge/atlas_knowledge/vector/chroma.py`. Embedded mode (no separate server). One Chroma collection per user; `project_id` is a metadata filter on each item.

---

## 7. WebSocket Chat Flow

```
Client                                Server
  WS connect /api/v1/ws/{session_id}
  ─────────────────────────────────▶  Load/create Session in Redis (TTL 24h)

  chat.message {text, project_id}
  ─────────────────────────────────▶
                                       1. Embed query
                                       2. VectorStore.search(filter={project_id})
                                       3. Assemble RetrievalResult[]
  rag.context {nodes, query}
  ◀─────────────────────────────────
                                       4. SystemPromptBuilder.compose(...)
                                       5. ModelRouter.select(request, project)
                                       6. provider.stream(messages)

  chat.token {token}  (xN)
  ◀─────────────────────────────────

  chat.done {usage, model, latency_ms}
  ◀─────────────────────────────────  7. Persist Message + ModelUsage rows
```

### Session state in Redis

- `project_id`, `model_preference`
- `messages`: list of last N `Message` objects (sliding window)
- TTL: 24h, refreshed on every message

### Cancellation

Mid-stream client disconnect → server cancels via `asyncio.CancelledError` propagating through the streaming generator. The conversation turn is **not** persisted — no orphan half-messages in the DB.

### Context-window overflow

Sliding window truncation only (drop oldest messages first, keep system + last K). Summarization fallback is deferred.

---

## 8. Frontend

### Stack

React 19, Vite, TypeScript, Tailwind v4, shadcn/ui, Zustand (global), React Query (server state), native WebSocket.

### Routes

- `/` — redirects to active project (or "select a project" view if none exist)
- `/projects/:id` — chat view scoped to that project

### Layout

- **Left sidebar (collapsible)** — project list with active highlighted, "+ New Project" modal trigger, settings/model dropdown at bottom
- **Main panel** — chat with streaming token rendering, message history with copy + regenerate controls
- **Right drawer (toggleable)** — RAG context panel showing latest `RetrievalResult[]`: chunk text preview, source title, similarity score
- **Inline tool-use cards** — collapsible cards rendered for any `chat.tool_use` / `chat.tool_result` events. Phase 1 will rarely emit these; the component is in place for Phase 3.

(Plan 6 placed the model picker above the chat input bar instead — per-session selection feels more natural near the action.)

### State

**Zustand global store:**
- `auth`: stubbed user object
- `projects`: { list, active_project_id }
- `models`: { available, selected_per_session }
- `ui`: { sidebar_collapsed, rag_drawer_open }

**React Query hooks:**
- `useProjects()` — list
- `useProject(id)` — single + invalidate on update
- `useKnowledgeNodes(project_id)` — for the project dashboard
- `useModels()` — list available models from `/api/v1/models`

**Custom hook:** `useAtlasChat(sessionId)` wraps native `WebSocket`. Maintains a message accumulator, exposes `send(text, config?)`, handles reconnect with exponential backoff. Token events append to the in-flight assistant message; `done` event finalizes it.

---

## 9. API Surface (Phase 1)

```
GET    /health
GET    /api/v1/projects                  List projects
POST   /api/v1/projects                  Create project
GET    /api/v1/projects/{id}             Get project
PATCH  /api/v1/projects/{id}             Update project
DELETE /api/v1/projects/{id}             Delete project (soft)

POST   /api/v1/knowledge/ingest          Upload PDF or post text/markdown
GET    /api/v1/knowledge/search          One-shot RAG search (debug/admin)
GET    /api/v1/knowledge/jobs/{id}       Ingestion job status
GET    /api/v1/knowledge/nodes           List nodes (filter by project_id)
DELETE /api/v1/knowledge/nodes/{id}      Remove a node + its chunks

GET    /api/v1/models                    List available models
POST   /api/v1/models/test               Test connectivity to a provider

WS     /api/v1/ws/{session_id}           Chat WebSocket (see §7)
```

Plugins, audio, and admin endpoints from v0.1 §3.2 are deferred.

---

## 10. Postgres Schema (Phase 1)

Multi-user-ready: every table has `user_id`. In Phase 1 every row has `user_id = 'matt'`.

```sql
CREATE TABLE projects (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id TEXT NOT NULL,
    name TEXT NOT NULL,
    description TEXT,
    status TEXT NOT NULL DEFAULT 'active',     -- active | paused | archived
    privacy_level TEXT NOT NULL DEFAULT 'cloud_ok',  -- cloud_ok | local_only
    default_model TEXT NOT NULL,
    enabled_plugins JSONB NOT NULL DEFAULT '[]',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX projects_user_idx ON projects(user_id);

CREATE TABLE sessions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id TEXT NOT NULL,
    project_id UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    model TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_active_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX sessions_user_project_idx ON sessions(user_id, project_id);

CREATE TABLE messages (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id TEXT NOT NULL,
    session_id UUID NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    role TEXT NOT NULL,                        -- system | user | assistant | tool
    content TEXT NOT NULL,
    tool_calls JSONB,
    rag_context JSONB,
    model TEXT,
    token_count INTEGER,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX messages_session_idx ON messages(session_id, created_at);

CREATE TABLE model_usage (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id TEXT NOT NULL,
    session_id UUID REFERENCES sessions(id) ON DELETE SET NULL,
    project_id UUID REFERENCES projects(id) ON DELETE SET NULL,
    provider TEXT NOT NULL,
    model_id TEXT NOT NULL,
    input_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    latency_ms INTEGER,
    task_type TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX model_usage_user_created_idx ON model_usage(user_id, created_at);

CREATE TABLE ingestion_jobs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id TEXT NOT NULL,
    project_id UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    source_type TEXT NOT NULL,                 -- pdf | markdown
    source_filename TEXT,
    status TEXT NOT NULL DEFAULT 'pending',    -- pending | running | completed | failed
    node_ids JSONB NOT NULL DEFAULT '[]',
    error TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at TIMESTAMPTZ
);
CREATE INDEX ingestion_jobs_project_idx ON ingestion_jobs(project_id, created_at);

CREATE TABLE knowledge_nodes (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id TEXT NOT NULL,
    project_id UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    type TEXT NOT NULL,                        -- document | chunk
    parent_id UUID REFERENCES knowledge_nodes(id) ON DELETE CASCADE,
    title TEXT,
    text TEXT NOT NULL,
    metadata JSONB NOT NULL DEFAULT '{}',
    embedding_id TEXT,                         -- ID in Chroma
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX knowledge_nodes_project_type_idx ON knowledge_nodes(project_id, type);
CREATE INDEX knowledge_nodes_parent_idx ON knowledge_nodes(parent_id);
```

Migrations managed by Alembic.

---

## 11. Error Handling

| Scenario | Handling |
|---|---|
| Provider network/rate-limit/auth error | Caught in provider layer → wrapped as `ProviderError(code, message)` → emitted as `chat.error` WS event |
| API request validation fails | FastAPI Pydantic 422 (default) |
| Ingestion failure | `ingestion_jobs.status = 'failed'`, `error` populated, frontend toast on next poll |
| LM Studio unreachable | Router falls back to Anthropic if `project.privacy_level == 'cloud_ok'`; else `chat.error` with actionable message ("LM Studio at {url} unreachable; project is local-only") |
| WebSocket client disconnects mid-stream | Server cancels via `asyncio.CancelledError`; partial assistant message is **not** persisted |
| Embedding model load failure on startup | API fails to start with clear error; `/health` reports unready |
| Postgres/Redis unreachable on startup | API fails to start; docker-compose depends_on ensures order |

All errors logged with `structlog` in JSON format with correlation IDs (one per WS message).

---

## 12. Testing

### Backend (`pytest` + `pytest-asyncio`)

- **Provider abstraction** — each provider has a fake transport; tests verify event normalization (token order, done payload, error wrapping)
- **Model router** — parameterized tests for each policy path (override / local_only / default)
- **Chunker** — golden-input/golden-output: known markdown + PDF text → expected chunk boundaries
- **Embedding service** — verifies batched output shape; smoke test with 2-3 short strings
- **Retriever** — in-memory `VectorStore` fake; verifies top-k, project_id scoping, deduplication
- **Prompt registry** — every shipped template gets a smoke render with realistic vars + `StrictUndefined` raises on missing vars
- **API integration** — `httpx.AsyncClient` against the app with stubbed providers; happy path for: create project, ingest markdown, search, basic chat (single message, drained stream)

### Frontend (Vitest + React Testing Library)

- **`useAtlasChat` hook** — mock `WebSocket`; verify token accumulation, `done` finalization, reconnect on close
- **Chat panel render** — streaming message renders progressively; copy button works
- **Sidebar render** — projects list + active highlight + new-project modal opens
- **RAG drawer render** — opens/closes; renders nodes from a `rag.context` event

### Coverage philosophy

Phase 1 prioritizes covering the parts that are easy to break silently — the provider event normalization and the retrieval ranking. UI tests focus on the structural behaviors (does the WebSocket plumb through, do components mount), not pixel detail. No browser e2e; manual smoke through the actual UI before declaring Phase 1 done.

---

## 13. Repo Layout

```
atlas-agent/
├── apps/
│   ├── api/
│   │   ├── pyproject.toml
│   │   ├── main.py
│   │   ├── deps.py                # FastAPI dependency providers
│   │   ├── routers/
│   │   │   ├── projects.py
│   │   │   ├── knowledge.py
│   │   │   ├── models.py
│   │   │   └── ws.py
│   │   └── tests/
│   └── web/
│       ├── package.json
│       ├── vite.config.ts
│       ├── tsconfig.json
│       ├── tailwind.config.ts
│       ├── index.html
│       └── src/
│           ├── main.tsx
│           ├── App.tsx
│           ├── routes/
│           ├── components/
│           ├── hooks/
│           ├── stores/
│           └── lib/
├── packages/
│   ├── atlas-core/
│   │   ├── pyproject.toml
│   │   ├── atlas_core/
│   │   │   ├── __init__.py
│   │   │   ├── config.py
│   │   │   ├── models/
│   │   │   ├── providers/
│   │   │   ├── prompts/
│   │   │   └── agent/
│   │   └── tests/
│   └── atlas-knowledge/
│       ├── pyproject.toml
│       ├── atlas_knowledge/
│       │   ├── __init__.py
│       │   ├── models/
│       │   ├── ingestion/
│       │   ├── embeddings/
│       │   ├── vector/
│       │   └── retrieval/
│       └── tests/
├── infra/
│   ├── docker-compose.yml
│   ├── postgres/
│   │   └── init.sql
│   └── alembic/
│       ├── env.py
│       └── versions/
├── pyproject.toml                 # uv workspace root
├── uv.lock
├── .env.example
├── .gitignore
├── README.md
└── docs/
    ├── atlas_design_document.md
    ├── atlas_design_addendum.md
    └── superpowers/specs/
```

---

## 14. Definition of Done

Phase 1 is complete when all of the following are true:

1. `docker-compose up` brings up Postgres + Redis + API + frontend cleanly on a fresh machine, with no manual steps beyond copying `.env.example` to `.env` and filling in the Anthropic API key.
2. The frontend loads at `http://localhost:3000` and shows the sidebar.
3. I can create a project via the UI ("New Project" modal).
4. I can upload a PDF or paste a markdown note into a project; the ingestion job completes; the resulting chunks appear queryable.
5. I can chat with that project: send a message, see streaming tokens render, see the RAG sources appear in the right drawer.
6. I can switch the active model between Anthropic and LM Studio per session via the model dropdown, and the streaming works for both.
7. `uv run pytest` passes from the repo root (covers all three Python packages).
8. `pnpm test` passes in `frontend/`.
9. README has setup instructions verified by running them on a clean checkout.

---

*ATLAS Phase 1 — Foundation Design · 2026-04-26*
