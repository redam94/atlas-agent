# ATLAS Phase 1 — Plan 6: React Frontend Design

**Status:** Draft · 2026-04-27
**Amends:** `docs/superpowers/specs/2026-04-26-atlas-phase-1-foundation-design.md` §8 (frontend), §9 (API surface), §13 (file layout), §14 (DoD)
**Predecessors:** Plans 1–5 merged (foundation, projects API, chat WS, knowledge layer, RAG-in-chat)
**Closes:** Phase 1

---

## 1. Purpose

Plan 6 is the final Phase 1 slice: ship a React frontend that consumes every API surface built in Plans 1–5, plus a `docker-compose` stack so the whole system comes up on a clean checkout. After this plan merges, the Foundation Definition of Done is satisfied and ATLAS is ready for Phase 2 (Knowledge Graph).

The architecture is locked by Foundation §8 — Vite + React 19 + TypeScript + Tailwind v4 + shadcn/ui + Zustand + React Query + native WebSocket. This document fixes the open scope and design questions and decomposes the work for an implementation plan.

---

## 2. Scope

### In scope
- `apps/web/` Vite + TypeScript app: routes, components, hooks, stores, lib, tests.
- Project sidebar with create / rename / delete (consumes existing `/projects/*`).
- Chat panel with streaming tokens, markdown rendering, code-block syntax highlighting, copy-on-message, inline tool-use card scaffolding.
- Per-session model picker above the input bar.
- RAG drawer driven by `rag.context` events; auto-opens on first context per session.
- Ingestion modal (PDF tab + markdown tab) with job polling.
- Per-project conversation persistence: `localStorage` maps `project_id → session_id`; new backend endpoint rehydrates messages on chat mount.
- One new backend endpoint (`GET /api/v1/sessions/{session_id}/messages`) and Pydantic `MessageRead` schema.
- `infra/docker-compose.yml` bringing up Postgres + Redis + api + web; web Dockerfile (multi-stage node→nginx) and nginx config; api Dockerfile if not already present; root `.env.example`.
- README "Quickstart" section covering both docker-compose and local-dev paths.
- Vitest + React Testing Library: ~5 unit tests on the highest-risk units; pytest tests for the new sessions endpoint.

### Out of scope (deferred to later phases)
- Conversation list / multi-session UI per project. Phase 2 alongside Knowledge Explorer.
- Knowledge node browser (list + delete chunks). Phase 2 alongside Cytoscape graph view.
- Regenerate / branch / edit-message controls. Phase 2.
- Authentication UI. `user_id` stays hardcoded `"matt"` end-to-end.
- Audio mode, plugin views, settings page (icon is a stub).
- Browser e2e tests, MSW. Smoke is the DoD walk-through on Matt's machine.
- CSS theming beyond the shadcn default + a single shiki theme. No dark-mode toggle.

---

## 3. Architecture

### 3.1 Module layout

```
apps/web/
├── package.json                   pnpm
├── vite.config.ts                 dev proxy /api → :8000, /ws → :8000 (ws:true)
├── tsconfig.json                  strict, verbatimModuleSyntax
├── tailwind.config.ts             tailwind v4 + shadcn tokens
├── postcss.config.js
├── eslint.config.js               flat config, @typescript-eslint, react-hooks, react-refresh
├── vitest.config.ts               jsdom
├── index.html
├── Dockerfile                     multi-stage: node:20-alpine build → nginx:alpine
├── nginx.conf                     SPA fallback + proxy /api + /ws → api:8000
└── src/
    ├── main.tsx                   QueryClientProvider + RouterProvider
    ├── App.tsx                    layout shell
    ├── routes/
    │   ├── index.tsx              redirect to first project or render "create your first" panel
    │   └── project.tsx            /projects/:id chat view
    ├── components/
    │   ├── sidebar/               ProjectList, NewProjectButton, ProjectMenu (rename/delete)
    │   ├── chat/                  ChatPanel, MessageList, Message, Composer, ModelPicker
    │   ├── chat/markdown/         MarkdownRenderer, CodeBlock (shiki)
    │   ├── chat/tool-use/         ToolUseCard
    │   ├── rag/                   RagDrawer, CitationCard
    │   ├── ingest/                IngestModal (PDF tab + markdown tab)
    │   └── ui/                    shadcn primitives
    ├── hooks/
    │   ├── use-atlas-chat.ts      WS hook
    │   ├── use-projects.ts        React Query: list/get/create/update/delete
    │   ├── use-session-messages.ts  rehydrates prior conversation
    │   ├── use-ingest-job.ts      polls /knowledge/jobs/{id}
    │   └── use-models.ts
    ├── stores/
    │   └── atlas-store.ts         zustand: { auth, ui, models }
    ├── lib/
    │   ├── api.ts                 fetch wrapper, base URL from env
    │   ├── ws-protocol.ts         StreamEvent type guards (mirrors atlas_core)
    │   └── session-storage.ts     localStorage: project_id → session_id
    └── tests/
        ├── use-atlas-chat.test.ts
        ├── rag-drawer.test.tsx
        ├── ingest-modal.test.tsx
        ├── markdown-renderer.test.tsx
        └── ws-protocol.test.ts
```

### 3.2 Boundaries

- `lib/` is pure TypeScript, no React imports. Unit-testable in isolation.
- `hooks/` wraps `lib/` for React consumers.
- `components/` reads state via hooks and the zustand store, never imports `lib/` directly.
- `stores/atlas-store.ts` holds **only** UI state (`sidebar_collapsed`, `rag_drawer_open`) plus the auth stub and per-session model selection. Server state — projects, messages, models, ingestion jobs — lives in React Query so caching, refetch, and invalidation are handled uniformly.

### 3.3 Stack-vs-spec deviations

The Foundation spec used `frontend/` at the repo root in §13 file layout. This plan places the app at `apps/web/` to match the existing `apps/api/` convention. Spec §13 is updated inline (one-line edit) when this plan executes.

The Foundation spec mentioned settings/model dropdown "at bottom of sidebar" in §8 layout. This plan places the model picker **above the chat input** (per-session, near the action) to match the design-doc §7 mock and standard chat UX. Spec §8 is updated inline.

---

## 4. State and Data Flow

### 4.1 Zustand store

```ts
{
  auth: { user_id: "matt" },                                  // hardcoded; matches API stub
  ui: { sidebar_collapsed: boolean, rag_drawer_open: boolean },
  models: { selected_id_per_session: Record<string, string> } // session_id → model_id
}
```

Project list, current project, conversation messages, ingestion jobs, available models — all React Query, **not** zustand. Anything that round-trips to the server is server state.

### 4.2 React Query hooks

| Hook | Endpoint | Notes |
|---|---|---|
| `useProjects()` | `GET /api/v1/projects` | invalidated by mutations below |
| `useProject(id)` | `GET /api/v1/projects/{id}` | 404 → toast, redirect `/` |
| `useCreateProject()` | `POST /api/v1/projects` | invalidates `["projects"]` |
| `useUpdateProject()` | `PATCH /api/v1/projects/{id}` | invalidates `["projects"]`, `["projects", id]` |
| `useDeleteProject()` | `DELETE /api/v1/projects/{id}` | invalidates `["projects"]` |
| `useModels()` | `GET /api/v1/models` | static-ish; long staleTime |
| `useSessionMessages(session_id)` | `GET /api/v1/sessions/{id}/messages` (NEW) | runs once on chat mount |
| `useStartIngest()` | `POST /api/v1/knowledge/ingest` | returns `job_id` |
| `useIngestJob(job_id)` | `GET /api/v1/knowledge/jobs/{id}` | polls 1s while `pending|running` |
| `useDeleteNode(node_id)` | `DELETE /api/v1/knowledge/nodes/{id}` | used by RAG drawer "remove this source" |

### 4.3 `useAtlasChat(session_id, project_id, model_id)`

The one bespoke hook. Returns:

```ts
{
  messages: Message[]              // hydrated from useSessionMessages on mount, then live-appended
  rag_context: Citation[] | null   // last rag.context payload
  is_streaming: boolean
  error: { code, message } | null
  send(text: string): void
  cancel(): void                   // closes & reopens the WS to abort an in-flight stream
}
```

WebSocket URL is `ws(s)://<origin>/api/v1/ws/${session_id}` (vite proxy and nginx both forward `/ws`). Reconnect is exponential backoff with caps `1s, 2s, 4s, 8s, 16s, 30s`, reset on a successful connection. A reconnect during streaming finalizes the partial assistant message with a "(disconnected)" trailer rather than throwing the partial text away.

### 4.4 WS event mapping

Mirrors `atlas_core.models.messages.StreamEventType`:

The backend never emits a "stream start" event — the first signal of a new assistant turn on the wire is `rag.context` (when RAG fires) or `chat.token` (when it doesn't). The hook appends a blank assistant message **optimistically on `send()`** and flips `is_streaming=true` at the same point. The composer is disabled while `is_streaming` is true.

| Event (wire value) | Hook action | UI effect |
|---|---|---|
| `rag.context` | set `rag_context` | drawer auto-opens (first time per session) |
| `chat.token` | append `payload.token` to last message | streamed text, smooth append |
| `chat.tool_use` / `chat.tool_result` | push as inline tool-use card on last message | collapsed card; not emitted in Phase 1 |
| `chat.done` | finalize last message; `is_streaming=false` | enable composer |
| `chat.error` | set `error`; `is_streaming=false` | inline toast under composer |

`ws-protocol.ts` defines a discriminated union for `StreamEvent` with type guards. This is the single source of truth on the frontend and is unit-tested against fixtures pulled from the backend's stream-event schema.

### 4.5 Session lifecycle

- On `/projects/:id` mount, `lib/session-storage` looks up `project_id` in `localStorage`. Absent → mint UUID, store. Present → reuse.
- The WS connects to `/ws/{session_id}`.
- `useSessionMessages(session_id)` runs once on mount to rehydrate prior chat.
- Switching projects swaps session_ids; the WS is torn down and reopened with the new id.
- "Clear conversation" UX is **not** in scope. Workaround: delete the localStorage entry from devtools. Multi-conversation UI lands in Phase 2.

---

## 5. Routes and Key UI Surfaces

### 5.1 Routes (React Router v7)

- `/` — `index.tsx`. Reads project list. Empty → render "Create your first project" panel with `NewProjectModal` trigger. Non-empty → redirect to `/projects/{first_id}`.
- `/projects/:id` — `project.tsx`. Chat view. `useProject(id)` 404 → toast and redirect to `/`.

No `/projects/new` route — creation is a modal.

### 5.2 Layout shell

```
┌─────────────────────────────────────────────────────┐
│ [≡] ATLAS                          [📚 Sources]     │
├──────────┬──────────────────────────────────────────┤
│ SIDEBAR  │  ┌───────────────────────────────────┐  │
│          │  │ MessageList                       │  │
│ Projects │  │  - rendered markdown              │  │
│ • CircleK│  │  - copy on hover                  │  │
│   GeoLift│  │  - inline tool-use cards          │  │
│ + New    │  └───────────────────────────────────┘  │
│  ─────   │  ┌───────────────────────────────────┐  │
│  ⚙       │  │ [Model: claude-sonnet ▾] [+ Add]  │  │
│          │  │ [textarea — Cmd+Enter to send]    │  │
└──────────┴──────────────────────────────────────────┘
                                          ▲ RagDrawer
```

### 5.3 Sidebar

- Project list, active highlighted, hover reveals `…` menu (rename, delete, with confirm).
- "+ New Project" → `NewProjectModal` (name + description, calls `useCreateProject`, navigates on success).
- Bottom: settings icon, no-op stub in Phase 1.

### 5.4 Chat panel

- `MessageList` — unvirtualized in Phase 1. Virtualization deferred until message counts hurt.
- `Message` — user messages right-aligned with subtle tint; assistant messages full-width left-aligned. Assistant content rendered through `MarkdownRenderer` (`react-markdown` + `remark-gfm`) with `CodeBlock` (shiki, single theme tied to Tailwind tokens).
- Copy button on hover, top-right of bubble.
- `ToolUseCard` — collapsed by default; expand to show JSON args + result. No-op in Phase 1 but wired so Phase 3 plugins activate it without UI work.
- `Composer` — textarea + `ModelPicker` (above input, per-session) + "+ Add" button (opens `IngestModal`) + send button. Cmd/Ctrl+Enter sends. Disabled while `is_streaming`.

### 5.5 RagDrawer

- shadcn `Sheet`, slides in from the right.
- Toggle in the top bar (📚 Sources, badge with citation count).
- Auto-opens on the **first** `rag.context` event of the session; subsequent toggles are user-controlled (state in zustand).
- Renders `CitationCard`s: title, similarity score, chunk preview (first 200 chars), source path/URL. Click expands the full chunk text.
- Empty state: "No sources used yet. Upload knowledge to get RAG-grounded answers." with `IngestModal` trigger.

### 5.6 IngestModal

- Tabs: **PDF** (file input + drag-drop) and **Text/Markdown** (textarea + optional title field).
- Submits via `useStartIngest` → returns `job_id` → switches to in-progress view that polls `useIngestJob(job_id)` every 1s.
- Done: "Ingested N chunks" + close. Failed: error message + retry button.
- Modal can be closed mid-job; polling continues in the background and a completion toast appears on success.

---

## 6. Backend Additions

One new endpoint, one new schema, four new tests.

### 6.1 `GET /api/v1/sessions/{session_id}/messages`

```python
# apps/api/atlas_api/routers/sessions.py  (new module, follows existing routers/*.py pattern)
@router.get("/sessions/{session_id}/messages")
async def list_messages(
    session_id: UUID,
    db: AsyncSession = Depends(get_db),
    settings: AtlasConfig = Depends(get_settings),
) -> list[MessageRead]:
    session = await db.get(SessionORM, session_id)
    if session is None:
        return []
    if session.user_id != settings.user_id:
        raise HTTPException(403)
    rows = await db.scalars(
        select(MessageORM)
        .where(MessageORM.session_id == session_id)
        .order_by(MessageORM.created_at.asc())
    )
    return [MessageRead.model_validate(r) for r in rows]
```

The router is mounted under `/api/v1` in `atlas_api.main`, matching `projects.py` and `knowledge.py`. Auth follows the existing pattern: `settings.user_id` from `AtlasConfig` (hardcoded `"matt"` in Phase 1) — no separate `get_user_id` dep.

**Empty list, not 404, on missing session.** The frontend mints `session_id` client-side before any WS connection. On a fresh project, the row doesn't exist yet — that's normal, not an error. Returning `[]` collapses "no session yet" and "session has no messages" into one branch.

### 6.2 `MessageRead` schema

```python
# packages/atlas-core/atlas_core/models/messages.py
class MessageRead(BaseModel):
    id: UUID
    session_id: UUID
    role: Literal["user", "assistant", "system"]
    content: str
    created_at: datetime
    model_id: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    model_config = ConfigDict(from_attributes=True)
```

Mirrors `MessageORM` columns. Lives next to `ChatRequest` and `StreamEvent`.

### 6.3 Tests

`apps/api/atlas_api/tests/test_sessions_router.py` (matches `test_projects_router.py` / `test_knowledge_router.py` naming):
1. Empty / nonexistent session → `200 []`.
2. Session with three messages → returned in `created_at` ascending order.
3. Session belonging to a different `user_id` → `403`.
4. Non-UUID path → `422` (FastAPI default; sanity test).

### 6.4 No CORS changes

Vite dev proxy and nginx production proxy both keep the frontend same-origin with the API. The existing CORS middleware stays as-is.

---

## 7. Docker, Dev Experience, Tooling

### 7.1 New repo files

```
infra/
├── docker-compose.yml
└── postgres/init.sql            (already specified in foundation spec; confirm or create)

apps/web/Dockerfile              multi-stage: node:20-alpine build → nginx:alpine
apps/web/nginx.conf              SPA fallback + proxy /api + /ws → api:8000
apps/api/Dockerfile              uv sync + uvicorn (create if absent)
.env.example                     root level
```

### 7.2 `docker-compose.yml` shape

```yaml
services:
  postgres:
    image: postgres:16-alpine
    environment:
      POSTGRES_USER: ${POSTGRES_USER}
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD}
      POSTGRES_DB: ${POSTGRES_DB}
    volumes:
      - postgres_data:/var/lib/postgresql/data
      - ./postgres/init.sql:/docker-entrypoint-initdb.d/init.sql
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U ${POSTGRES_USER}"]
  redis:
    image: redis:7-alpine
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
  api:
    build: { context: ../, dockerfile: apps/api/Dockerfile }
    env_file: ../.env
    depends_on:
      postgres: { condition: service_healthy }
      redis:    { condition: service_healthy }
    ports: ["8000:8000"]
  web:
    build: { context: ../apps/web, dockerfile: Dockerfile }
    depends_on: [api]
    ports: ["3000:80"]
volumes:
  postgres_data: {}
```

### 7.3 Local dev (no Docker)

```
# terminal 1
uv run uvicorn atlas_api.main:app --reload --port 8000
# terminal 2
cd apps/web && pnpm dev   # Vite on :5173, proxies /api and /ws to :8000
```

`vite.config.ts`:

```ts
server: {
  proxy: {
    '/api': { target: 'http://localhost:8000', changeOrigin: true },
    '/ws':  { target: 'ws://localhost:8000', ws: true, changeOrigin: true },
  },
}
```

### 7.4 Production-like run

`docker-compose up` from `infra/`. Web on `:3000` (nginx). API on `:8000`. nginx proxies `/api` and `/ws` to `api:8000`, so the browser sees one origin.

### 7.5 `.env.example`

```
ANTHROPIC_API_KEY=
LM_STUDIO_BASE_URL=http://host.docker.internal:1234/v1
POSTGRES_USER=atlas
POSTGRES_PASSWORD=atlas
POSTGRES_DB=atlas
DATABASE_URL=postgresql+asyncpg://atlas:atlas@postgres:5432/atlas
REDIS_URL=redis://redis:6379/0
USER_ID=matt
```

### 7.6 Frontend tooling

- ESLint flat config: `@typescript-eslint`, `react-hooks`, `react-refresh`.
- Prettier with Tailwind class-sort plugin.
- TypeScript strict, `verbatimModuleSyntax`, no `any` (lint-enforced).
- Vitest + React Testing Library + jsdom.
- shadcn CLI initialized to write into `src/components/ui/`.
- Scripts: `pnpm dev`, `pnpm build`, `pnpm test`, `pnpm lint`, `pnpm typecheck`.

---

## 8. Testing Strategy

### 8.1 Frontend (Vitest + RTL, ~5 tests)

The principle from foundation spec §11 — "structural behaviors, not pixel detail, no e2e" — is preserved. The five tests cover what is easy to break silently and hard to catch by eye.

1. **`use-atlas-chat.test.ts`** — fake `WebSocket`. Cases: token accumulation appends to last message; `chat.done` finalizes and clears `is_streaming`; `error` event populates error and clears streaming; `rag.context` populates citations; reconnect after unexpected close (assert backoff schedule with fake timers).
2. **`ws-protocol.test.ts`** — type-guard correctness for each `StreamEventType`. Catches drift between frontend type defs and `atlas_core.models.messages`.
3. **`rag-drawer.test.tsx`** — zero-state render; citation card render with title/score/preview; chunk text expand on click.
4. **`ingest-modal.test.tsx`** — tab switching preserves form state; submit triggers `useStartIngest`; polling shows progress; `status=complete` shows "Ingested N chunks"; `status=failed` shows error and retry.
5. **`markdown-renderer.test.tsx`** — fenced code block with shiki highlighting; GFM table renders; raw HTML sanitized (no `<script>` execution).

### 8.2 Backend (pytest)

Four tests for `GET /api/v1/sessions/{id}/messages` per §6.3. No other backend tests touched.

### 8.3 Smoke

DoD walk-through on Matt's machine: docker-compose up, create project, ingest, chat, switch model, refresh. This is the e2e — it catches integration issues that unit tests structurally cannot.

---

## 9. Build Order (Vertical Slices)

Each slice ends in a commit-able state where the system runs. Partial completion still produces a usable artifact.

1. **Skeleton** — scaffold `apps/web/` (Vite + TS + Tailwind v4 + shadcn + ESLint + Vitest + pnpm). App boots to "Hello ATLAS" at `:5173`. `/api/v1/health` proxy works. Backend `GET /sessions/{id}/messages` lands here with its four tests.
2. **Projects** — zustand store, React Query, `useProjects` family, sidebar, `NewProjectModal`, route redirect. End state: create a project, click into it, see a placeholder chat panel.
3. **Chat (no markdown, no RAG)** — `useAtlasChat` with WS connect/send/token/done/error, plain-text rendering, `ModelPicker` above input, session_id in localStorage, prior-message rehydration. End state: send a message, watch tokens stream, refresh, see conversation persist.
4. **Markdown polish** — `MarkdownRenderer` + `CodeBlock` (shiki) + copy button + `ToolUseCard` scaffolding. End state: assistant messages render properly.
5. **RAG drawer** — `rag.context` event handling, `RagDrawer`, `CitationCard`, auto-open on first context. End state: see grounded citations as a chat happens.
6. **Ingest modal** — `IngestModal` with both tabs, `useStartIngest`, `useIngestJob` polling. End state: ingest a PDF or markdown note from the UI.
7. **Docker + README** — api Dockerfile (if missing), web Dockerfile + nginx config, `infra/docker-compose.yml`, root `.env.example`, README quickstart. End state: `docker-compose up` works on a clean clone.

---

## 10. Definition of Done

Phase 1 closes when all of the following are true (extends Foundation §14):

1. From a clean checkout: `cp .env.example .env`, fill in `ANTHROPIC_API_KEY`, `cd infra && docker-compose up` brings up Postgres + Redis + api + web cleanly.
2. `localhost:3000` loads, sidebar visible.
3. Create a project via "+ New Project" modal.
4. Upload a PDF and paste a markdown note via "+ Add" modal; both ingestion jobs reach `status=complete`; chunks appear in `GET /api/v1/knowledge/search`.
5. Chat with the project: stream tokens render, citations appear in the RAG drawer, refresh, conversation rehydrates from the new sessions endpoint.
6. Switch the model in the input-bar dropdown between Anthropic and an LM Studio model; both stream end-to-end.
7. `uv run pytest` passes from repo root (covers all three Python packages plus the new sessions tests).
8. `pnpm test`, `pnpm lint`, `pnpm typecheck`, `pnpm build` all pass in `apps/web/`.
9. README quickstart verified by running it on a clean clone.

---

## 11. Risks and Open Items

- **shadcn + Tailwind v4 compatibility**: shadcn's CLI generates Tailwind v3-style classes by default. Plan 6 must verify the shadcn-Tailwind-v4 path on first scaffold; if there's friction, the fallback is Tailwind v3.4 (one-line spec deviation, noted at execution time).
- **shiki bundle size**: shiki is heavy. Lazy-load it only inside `CodeBlock` so non-code chats don't pay the cost.
- **WebSocket reconnect during streaming**: the "(disconnected)" trailer is a UX choice that needs to feel right; first cut is a fixed string, polish if it looks bad.
- **`host.docker.internal` for LM Studio**: works on Docker Desktop (Mac/Windows). On native Linux Docker, would need `--add-host=host.docker.internal:host-gateway` in compose. Phase 1 documents the Mac path; Linux is a follow-up if anyone runs into it.

---

*ATLAS Phase 1 — Plan 6: React Frontend Design · 2026-04-27*
