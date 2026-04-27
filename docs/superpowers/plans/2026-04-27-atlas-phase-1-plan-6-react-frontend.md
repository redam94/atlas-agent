# ATLAS Phase 1 — Plan 6: React Frontend Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the Phase 1 React frontend (`apps/web/`) plus the `infra/docker-compose.yml` stack so the whole ATLAS system runs on a clean checkout. Closes Phase 1.

**Architecture:** Vite + React 19 + TypeScript + Tailwind v4 + shadcn/ui + Zustand (UI state) + React Query (server state) + native WebSocket. One bespoke hook (`useAtlasChat`) wraps the WS protocol. Per-project session ids in localStorage; conversations rehydrate via a new `GET /api/v1/sessions/{id}/messages` endpoint. Citations get a `text_preview` field added to `build_rag_context` so the RAG drawer can render previews without an extra round-trip. Vertical slices: skeleton → projects → chat → markdown → RAG → ingest → docker.

**Tech Stack:**
- Frontend: React 19, Vite 5, TypeScript strict, Tailwind v4, shadcn/ui, Zustand, TanStack Query v5, React Router v7, react-markdown + remark-gfm, shiki, Vitest + React Testing Library + jsdom, ESLint flat config, Prettier with Tailwind plugin, pnpm.
- Backend: FastAPI, SQLAlchemy async, existing `Message` Pydantic model + `message_from_orm` converter, existing routers pattern.
- Infra: Docker Compose, postgres:16-alpine, redis:7-alpine, nginx:alpine, node:20-alpine.

**Authoritative spec:** `docs/superpowers/specs/2026-04-27-atlas-phase-1-plan-6-react-frontend-design.md`.

**Branch:** `feat/phase-1-plan-6-react-frontend` (already created and the design spec is committed to it).

**Important contract details discovered during planning** (these are accurate as of `main` at the time the plan was written — verify with `grep` if the implementer wants to be sure):
- The existing `Message` Pydantic model in `packages/atlas-core/atlas_core/models/messages.py` is reused for the new endpoint's response. No new `MessageRead` schema is needed despite what the design spec calls it. The existing `message_from_orm` converter in `packages/atlas-core/atlas_core/db/converters.py` does the ORM→Pydantic mapping.
- The existing FastAPI session dep is `get_session` (yields `AsyncSession`), not `get_db`. Mounted in `apps/api/atlas_api/main.py` with prefix `/api/v1`. Routers live in `apps/api/atlas_api/routers/`.
- The existing knowledge ingest endpoints are split: `POST /api/v1/knowledge/ingest` (JSON body, markdown only — `source_type` must be `"markdown"`) and `POST /api/v1/knowledge/ingest/pdf` (multipart with `project_id` Form field + `file` UploadFile). The frontend `IngestModal` calls one or the other based on the active tab.
- `ProjectCreate` requires `name` (1-200 chars) and `default_model` (non-empty string). `description`, `privacy_level` (default `cloud_ok`), `enabled_plugins` (default `[]`) are optional.
- `ChatRequest` (the WS payload) requires `text` (1-32k chars) and `project_id`. Optional `model_override`, `rag_enabled` (default `true`), `top_k_context` (default 8), `temperature` (default 0.7).
- `StreamEventType` wire values: `chat.token`, `chat.tool_use`, `chat.tool_result`, `rag.context`, `chat.done`, `chat.error`. There is **no** `chat.start` — the frontend appends a blank assistant message optimistically on `send()`, not in response to a server event.
- The current `rag.context` payload's `citations` list contains `{id, title, score, chunk_id}` — no chunk preview text. Task A0 below extends `build_rag_context` to add `text_preview` (first 200 chars) so the RAG drawer can render useful cards.

---

## File Map

**Backend additions:**
- Create: `apps/api/atlas_api/routers/sessions.py`
- Create: `apps/api/atlas_api/tests/test_sessions_router.py`
- Modify: `apps/api/atlas_api/main.py` (mount sessions router)
- Modify: `packages/atlas-knowledge/atlas_knowledge/retrieval/builder.py` (add `text_preview` to citations)
- Modify: `packages/atlas-knowledge/atlas_knowledge/tests/test_retrieval_builder.py` (assert new field)

**Frontend (`apps/web/`):**
- Create: `package.json`, `pnpm-workspace.yaml` (root, if not present), `tsconfig.json`, `tsconfig.app.json`, `tsconfig.node.json`, `vite.config.ts`, `tailwind.config.ts`, `postcss.config.js`, `eslint.config.js`, `.prettierrc.json`, `vitest.config.ts`, `index.html`
- Create: `src/main.tsx`, `src/App.tsx`, `src/index.css`
- Create: `src/lib/api.ts`, `src/lib/ws-protocol.ts`, `src/lib/session-storage.ts`
- Create: `src/stores/atlas-store.ts`
- Create: `src/hooks/use-projects.ts`, `src/hooks/use-models.ts`, `src/hooks/use-session-messages.ts`, `src/hooks/use-atlas-chat.ts`, `src/hooks/use-ingest-job.ts`
- Create: `src/routes/index.tsx`, `src/routes/project.tsx`
- Create: `src/components/sidebar/` (ProjectList, NewProjectModal, ProjectMenu)
- Create: `src/components/chat/` (ChatPanel, MessageList, Message, Composer, ModelPicker)
- Create: `src/components/chat/markdown/` (MarkdownRenderer, CodeBlock)
- Create: `src/components/chat/tool-use/` (ToolUseCard)
- Create: `src/components/rag/` (RagDrawer, CitationCard)
- Create: `src/components/ingest/` (IngestModal)
- Create: `src/components/ui/` (shadcn-generated primitives)
- Create: `src/tests/use-atlas-chat.test.ts`, `ws-protocol.test.ts`, `rag-drawer.test.tsx`, `ingest-modal.test.tsx`, `markdown-renderer.test.tsx`
- Create: `Dockerfile`, `nginx.conf`, `.dockerignore`

**Infra:**
- Create: `infra/docker-compose.yml`
- Create: `infra/postgres/init.sql` (if not already present — verify before creating)
- Create: `apps/api/Dockerfile`, `apps/api/.dockerignore`
- Create: `.env.example` (root)

**Doc updates:**
- Modify: `README.md` (add Quickstart)
- Modify: `docs/superpowers/specs/2026-04-26-atlas-phase-1-foundation-design.md` (one-line: `frontend/` → `apps/web/` in §13; note model picker placement in §8)

---

## Task A0: Extend `build_rag_context` citations with `text_preview`

**Files:**
- Modify: `packages/atlas-knowledge/atlas_knowledge/retrieval/builder.py`
- Modify: `packages/atlas-knowledge/atlas_knowledge/tests/test_retrieval_builder.py`

The current citation dict is `{id, title, score, chunk_id}`. The RAG drawer needs a preview snippet so users can tell sources apart at a glance. Add a `text_preview` field — the first 200 characters of the chunk text, with a single trailing ellipsis if truncated.

- [ ] **Step 1: Add a failing test for `text_preview`**

In `packages/atlas-knowledge/atlas_knowledge/tests/test_retrieval_builder.py`, add this test next to the existing ones:

```python
def test_build_rag_context_includes_text_preview() -> None:
    long_text = "abcdefgh" * 30  # 240 chars > 200
    short_text = "short text"
    chunks = [
        _make_scored("doc1", long_text, "Doc 1", 0.9),
        _make_scored("doc2", short_text, "Doc 2", 0.8),
    ]
    ctx = build_rag_context(chunks)

    assert ctx.citations[0]["text_preview"] == long_text[:200] + "…"
    assert ctx.citations[1]["text_preview"] == short_text
```

If the existing test file lacks a `_make_scored` helper, find the helper that the existing tests use — they all build `ScoredChunk` instances somehow — and reuse it. The pattern is something like:

```python
from uuid import uuid4
from atlas_knowledge.models.nodes import KnowledgeNode, KnowledgeNodeType
from atlas_knowledge.models.retrieval import ScoredChunk
from datetime import datetime, timezone

def _make_scored(node_id: str, text: str, parent_title: str | None, score: float) -> ScoredChunk:
    return ScoredChunk(
        chunk=KnowledgeNode(
            id=uuid4(),
            user_id="matt",
            project_id=uuid4(),
            type=KnowledgeNodeType.CHUNK,
            text=text,
            created_at=datetime.now(timezone.utc),
        ),
        score=score,
        parent_title=parent_title,
    )
```

Inspect the existing tests in the file to confirm the helper signature, and either reuse the existing helper or define this one once at the top of the file.

- [ ] **Step 2: Run test, confirm it fails**

```
uv run pytest packages/atlas-knowledge/atlas_knowledge/tests/test_retrieval_builder.py::test_build_rag_context_includes_text_preview -v
```

Expected: FAIL with `KeyError: 'text_preview'` or assertion error.

- [ ] **Step 3: Add `text_preview` to the citation dict**

In `packages/atlas-knowledge/atlas_knowledge/retrieval/builder.py`, modify the citations append block:

```python
        preview_source = sc.chunk.text or ""
        text_preview = preview_source[:200] + "…" if len(preview_source) > 200 else preview_source
        citations.append(
            {
                "id": idx,
                "title": title,
                "score": sc.score,
                "chunk_id": str(sc.chunk.id),
                "text_preview": text_preview,
            }
        )
```

- [ ] **Step 4: Run all retrieval-builder tests; confirm they pass**

```
uv run pytest packages/atlas-knowledge/atlas_knowledge/tests/test_retrieval_builder.py -v
```

Expected: all pass, including the new one and the existing four.

- [ ] **Step 5: Commit**

```
git add packages/atlas-knowledge/atlas_knowledge/retrieval/builder.py packages/atlas-knowledge/atlas_knowledge/tests/test_retrieval_builder.py
git commit -m "feat(atlas-knowledge): add text_preview to build_rag_context citations

The RAG drawer in the React frontend (Plan 6) renders citation cards
with a snippet so users can tell sources apart without an extra fetch.
First 200 chars of the chunk text, single ellipsis if truncated."
```

---

## Task A1: Add `GET /api/v1/sessions/{session_id}/messages` endpoint (TDD)

**Files:**
- Create: `apps/api/atlas_api/routers/sessions.py`
- Create: `apps/api/atlas_api/tests/test_sessions_router.py`
- Modify: `apps/api/atlas_api/main.py`

The frontend rehydrates per-project conversations on mount. Returns `[]` for sessions that don't exist yet (the frontend mints `session_id` client-side, so a missing row is normal). Returns `403` if the session belongs to another `user_id`.

- [ ] **Step 1: Write failing tests in `test_sessions_router.py`**

```python
"""Tests for GET /api/v1/sessions/{session_id}/messages."""

from uuid import uuid4

import pytest
from atlas_core.db.orm import MessageORM, ProjectORM, SessionORM
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_list_messages_empty_for_unknown_session(api_client: AsyncClient) -> None:
    """Missing session row → 200 [], not 404. Frontend mints session_ids before WS connect."""
    response = await api_client.get(f"/api/v1/sessions/{uuid4()}/messages")
    assert response.status_code == 200
    assert response.json() == []


@pytest.mark.asyncio
async def test_list_messages_returns_in_created_at_order(api_client: AsyncClient, db_session) -> None:
    project = ProjectORM(user_id="matt", name="P", default_model="claude-sonnet-4-6")
    db_session.add(project)
    await db_session.flush()

    session = SessionORM(user_id="matt", project_id=project.id)
    db_session.add(session)
    await db_session.flush()

    # Insert in scrambled order; expect chronological response.
    db_session.add(MessageORM(user_id="matt", session_id=session.id, role="user", content="first"))
    await db_session.flush()
    db_session.add(MessageORM(user_id="matt", session_id=session.id, role="assistant", content="second"))
    await db_session.flush()
    db_session.add(MessageORM(user_id="matt", session_id=session.id, role="user", content="third"))
    await db_session.flush()
    await db_session.commit()

    response = await api_client.get(f"/api/v1/sessions/{session.id}/messages")
    assert response.status_code == 200
    contents = [m["content"] for m in response.json()]
    assert contents == ["first", "second", "third"]


@pytest.mark.asyncio
async def test_list_messages_403_for_other_user(api_client: AsyncClient, db_session) -> None:
    project = ProjectORM(user_id="someone-else", name="X", default_model="claude-sonnet-4-6")
    db_session.add(project)
    await db_session.flush()
    session = SessionORM(user_id="someone-else", project_id=project.id)
    db_session.add(session)
    await db_session.flush()
    await db_session.commit()

    response = await api_client.get(f"/api/v1/sessions/{session.id}/messages")
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_list_messages_invalid_uuid_422(api_client: AsyncClient) -> None:
    response = await api_client.get("/api/v1/sessions/not-a-uuid/messages")
    assert response.status_code == 422
```

The fixtures `api_client` and `db_session` already exist in the test suite (`projects_router`, `knowledge_router`, etc. all use them). If imports differ in this codebase (e.g., a `conftest.py` lives at `apps/api/atlas_api/tests/conftest.py`), look at `test_projects_router.py` for the exact pattern and mirror it.

- [ ] **Step 2: Run tests, confirm they fail**

```
uv run pytest apps/api/atlas_api/tests/test_sessions_router.py -v
```

Expected: FAIL with 404 from FastAPI (no route registered yet).

- [ ] **Step 3: Implement the router**

Create `apps/api/atlas_api/routers/sessions.py`:

```python
"""GET /api/v1/sessions/{session_id}/messages — used by the frontend to rehydrate
per-project conversations on chat-view mount.

Returns ``[]`` when the session row does not exist: the frontend mints
session_ids client-side before any WS connection, so "no row yet" is the
normal first-load state, not an error.
"""

from uuid import UUID

from atlas_core.config import AtlasConfig
from atlas_core.db.converters import message_from_orm
from atlas_core.db.orm import MessageORM, SessionORM
from atlas_core.models.messages import Message
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from atlas_api.deps import get_session, get_settings

router = APIRouter(tags=["sessions"])


@router.get("/sessions/{session_id}/messages", response_model=list[Message])
async def list_messages(
    session_id: UUID,
    db: AsyncSession = Depends(get_session),
    settings: AtlasConfig = Depends(get_settings),
) -> list[Message]:
    session_row = await db.get(SessionORM, session_id)
    if session_row is None:
        return []
    if session_row.user_id != settings.user_id:
        raise HTTPException(status_code=403, detail="forbidden")
    result = await db.execute(
        select(MessageORM)
        .where(MessageORM.session_id == session_id)
        .order_by(MessageORM.created_at.asc())
    )
    return [message_from_orm(row) for row in result.scalars().all()]
```

- [ ] **Step 4: Mount the router in `main.py`**

In `apps/api/atlas_api/main.py`, add the import and `include_router` call next to the existing ones:

```python
from atlas_api.routers import sessions as sessions_router
# ...
app.include_router(sessions_router.router, prefix="/api/v1")
```

Place it adjacent to `projects_router`, `models_router`, `ws_chat`, `knowledge_router` for tidiness.

- [ ] **Step 5: Run tests, confirm they pass**

```
uv run pytest apps/api/atlas_api/tests/test_sessions_router.py -v
```

Expected: all four PASS.

- [ ] **Step 6: Run the full backend test suite to confirm no regressions**

```
uv run pytest
```

Expected: all pass.

- [ ] **Step 7: Commit**

```
git add apps/api/atlas_api/routers/sessions.py apps/api/atlas_api/tests/test_sessions_router.py apps/api/atlas_api/main.py
git commit -m "feat(atlas-api): add GET /sessions/{id}/messages for chat rehydration

Plan 6's frontend stores session_id per project in localStorage and
rehydrates on mount. Empty list (not 404) on missing session row, since
the frontend mints session_ids before any WS connection. 403 for
sessions owned by another user_id."
```

---

## Task B1: Scaffold `apps/web/` with Vite + TypeScript + pnpm

**Files:**
- Create: `apps/web/package.json`
- Create: `apps/web/tsconfig.json`, `apps/web/tsconfig.app.json`, `apps/web/tsconfig.node.json`
- Create: `apps/web/vite.config.ts`
- Create: `apps/web/index.html`
- Create: `apps/web/src/main.tsx`
- Create: `apps/web/src/App.tsx`
- Create: `apps/web/src/vite-env.d.ts`

This task lays down the bare Vite + TS app and verifies `pnpm dev` proxies `/api/v1/health` → `localhost:8000`. No styling, no React Query, no router yet.

- [ ] **Step 1: Create `apps/web/package.json`**

```json
{
  "name": "@atlas/web",
  "private": true,
  "version": "0.0.0",
  "type": "module",
  "scripts": {
    "dev": "vite --port 5173",
    "build": "tsc -b && vite build",
    "preview": "vite preview --port 4173",
    "lint": "eslint .",
    "typecheck": "tsc -b --noEmit",
    "test": "vitest run",
    "test:watch": "vitest"
  },
  "dependencies": {
    "@tanstack/react-query": "^5.59.0",
    "react": "^19.0.0",
    "react-dom": "^19.0.0",
    "react-router-dom": "^7.0.0",
    "zustand": "^5.0.0"
  },
  "devDependencies": {
    "@types/react": "^19.0.0",
    "@types/react-dom": "^19.0.0",
    "@vitejs/plugin-react": "^4.3.0",
    "typescript": "^5.6.0",
    "vite": "^5.4.0"
  }
}
```

(Tailwind, shadcn deps, ESLint, Vitest, react-markdown, shiki are added in later tasks. Keep this task's `package.json` minimal so install is fast and reasoning is local.)

- [ ] **Step 2: Create the three tsconfigs**

`apps/web/tsconfig.json` (root references file):
```json
{
  "files": [],
  "references": [
    { "path": "./tsconfig.app.json" },
    { "path": "./tsconfig.node.json" }
  ]
}
```

`apps/web/tsconfig.app.json`:
```json
{
  "compilerOptions": {
    "target": "ES2022",
    "useDefineForClassFields": true,
    "lib": ["ES2022", "DOM", "DOM.Iterable"],
    "module": "ESNext",
    "moduleResolution": "Bundler",
    "moduleDetection": "force",
    "allowImportingTsExtensions": true,
    "verbatimModuleSyntax": true,
    "skipLibCheck": true,
    "esModuleInterop": true,
    "resolveJsonModule": true,
    "jsx": "react-jsx",
    "noEmit": true,
    "strict": true,
    "noUnusedLocals": true,
    "noUnusedParameters": true,
    "noFallthroughCasesInSwitch": true,
    "noUncheckedSideEffectImports": true,
    "baseUrl": ".",
    "paths": {
      "@/*": ["src/*"]
    }
  },
  "include": ["src"]
}
```

`apps/web/tsconfig.node.json`:
```json
{
  "compilerOptions": {
    "target": "ES2022",
    "lib": ["ES2023"],
    "module": "ESNext",
    "moduleResolution": "Bundler",
    "moduleDetection": "force",
    "skipLibCheck": true,
    "strict": true,
    "noEmit": true
  },
  "include": ["vite.config.ts", "vitest.config.ts"]
}
```

- [ ] **Step 3: Create `apps/web/vite.config.ts`**

```ts
import path from "node:path";
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
  server: {
    port: 5173,
    proxy: {
      "/api": { target: "http://localhost:8000", changeOrigin: true },
      "/ws": { target: "ws://localhost:8000", ws: true, changeOrigin: true },
    },
  },
});
```

- [ ] **Step 4: Create `apps/web/index.html`**

```html
<!doctype html>
<html lang="en">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>ATLAS</title>
  </head>
  <body>
    <div id="root"></div>
    <script type="module" src="/src/main.tsx"></script>
  </body>
</html>
```

- [ ] **Step 5: Create `apps/web/src/main.tsx`**

```tsx
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { App } from "./App";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
```

- [ ] **Step 6: Create `apps/web/src/App.tsx`**

```tsx
export function App() {
  return (
    <div style={{ fontFamily: "system-ui", padding: 24 }}>
      <h1>ATLAS</h1>
      <p>Phase 1 frontend scaffold up.</p>
    </div>
  );
}
```

- [ ] **Step 7: Create `apps/web/src/vite-env.d.ts`**

```ts
/// <reference types="vite/client" />
```

- [ ] **Step 8: Install and verify dev server boots**

```
cd apps/web && pnpm install && pnpm typecheck && pnpm build
```

Expected: typecheck passes, build emits `dist/`.

Then start the API in one terminal (`uv run uvicorn atlas_api.main:app --reload --port 8000`) and run `pnpm dev` from `apps/web/`. Open `http://localhost:5173` — "ATLAS" should render. Verify the proxy by `curl http://localhost:5173/api/v1/health` and confirm a 200 response.

If running without the backend, the page should still render — the proxy just 502s requests to `/api/*` until the API is up. That's expected.

- [ ] **Step 9: Commit**

```
git add apps/web/package.json apps/web/tsconfig*.json apps/web/vite.config.ts apps/web/index.html apps/web/src/
git commit -m "feat(atlas-web): scaffold Vite + React 19 + TS app

apps/web/ baseline: Vite dev proxies /api and /ws to :8000, strict
TypeScript, react-router and react-query installed (used in later
tasks), Hello-ATLAS smoke page renders."
```

---

## Task B2: Configure Tailwind v4 + shadcn-init

**Files:**
- Modify: `apps/web/package.json` (add deps)
- Create: `apps/web/tailwind.config.ts`
- Create: `apps/web/postcss.config.js`
- Create: `apps/web/src/index.css`
- Create: `apps/web/components.json` (shadcn config)
- Create: `apps/web/src/lib/cn.ts`
- Modify: `apps/web/src/main.tsx` (import index.css)
- Create: `apps/web/src/components/ui/button.tsx` (single shadcn primitive as smoke)

Tailwind v4 + shadcn-cli's compatibility was flagged as a risk in the design spec. If the shadcn CLI fails to write Tailwind v4-compatible classes during this task, the documented fallback is to drop to Tailwind v3.4 (set `tailwindcss: ^3.4.0`, swap `@tailwind base/components/utilities` directives in `index.css`, drop `@tailwindcss/vite`). The button below is a known-good first test case.

- [ ] **Step 1: Add deps to `apps/web/package.json`**

Add to `dependencies`:
```
"class-variance-authority": "^0.7.0",
"clsx": "^2.1.0",
"lucide-react": "^0.460.0",
"tailwind-merge": "^2.5.0",
"tailwindcss-animate": "^1.0.7"
```

Add to `devDependencies`:
```
"@tailwindcss/vite": "^4.0.0-beta.7",
"autoprefixer": "^10.4.0",
"postcss": "^8.4.0",
"tailwindcss": "^4.0.0-beta.7"
```

Run `pnpm install`.

- [ ] **Step 2: Add the Tailwind Vite plugin to `vite.config.ts`**

```ts
import path from "node:path";
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  // ...rest unchanged
});
```

- [ ] **Step 3: Create `apps/web/src/index.css`**

```css
@import "tailwindcss";

@theme {
  --color-background: 0 0% 100%;
  --color-foreground: 240 10% 4%;
  --color-primary: 240 5.9% 10%;
  --color-primary-foreground: 0 0% 98%;
  --color-secondary: 240 4.8% 95.9%;
  --color-secondary-foreground: 240 5.9% 10%;
  --color-muted: 240 4.8% 95.9%;
  --color-muted-foreground: 240 3.8% 46.1%;
  --color-accent: 240 4.8% 95.9%;
  --color-accent-foreground: 240 5.9% 10%;
  --color-destructive: 0 84.2% 60.2%;
  --color-destructive-foreground: 0 0% 98%;
  --color-border: 240 5.9% 90%;
  --color-input: 240 5.9% 90%;
  --color-ring: 240 5.9% 10%;
  --radius: 0.5rem;
}

html, body, #root { height: 100%; }
body { margin: 0; }
```

- [ ] **Step 4: Update `apps/web/src/main.tsx` to import `index.css`**

Add `import "./index.css";` at the top.

- [ ] **Step 5: Create `apps/web/src/lib/cn.ts`**

```ts
import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]): string {
  return twMerge(clsx(inputs));
}
```

- [ ] **Step 6: Create `apps/web/components.json` for shadcn**

```json
{
  "$schema": "https://ui.shadcn.com/schema.json",
  "style": "default",
  "rsc": false,
  "tsx": true,
  "tailwind": {
    "config": "tailwind.config.ts",
    "css": "src/index.css",
    "baseColor": "neutral",
    "cssVariables": true
  },
  "aliases": {
    "components": "@/components",
    "utils": "@/lib/cn",
    "ui": "@/components/ui",
    "lib": "@/lib",
    "hooks": "@/hooks"
  },
  "iconLibrary": "lucide"
}
```

- [ ] **Step 7: Create `apps/web/tailwind.config.ts` (kept minimal — Tailwind v4 prefers config-in-CSS)**

```ts
import type { Config } from "tailwindcss";

const config: Config = {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
};

export default config;
```

- [ ] **Step 8: Hand-write a Button shadcn primitive**

Don't run the shadcn CLI in this task — it has known friction with Tailwind v4 betas. Hand-write the canonical Button so the rest of the plan can rely on it:

`apps/web/src/components/ui/button.tsx`:

```tsx
import * as React from "react";
import { Slot } from "@radix-ui/react-slot";
import { cva, type VariantProps } from "class-variance-authority";
import { cn } from "@/lib/cn";

const buttonVariants = cva(
  "inline-flex items-center justify-center gap-2 whitespace-nowrap rounded-md text-sm font-medium ring-offset-background transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:pointer-events-none disabled:opacity-50 [&_svg]:pointer-events-none [&_svg]:size-4 [&_svg]:shrink-0",
  {
    variants: {
      variant: {
        default: "bg-primary text-primary-foreground hover:bg-primary/90",
        destructive: "bg-destructive text-destructive-foreground hover:bg-destructive/90",
        outline: "border border-input bg-background hover:bg-accent hover:text-accent-foreground",
        secondary: "bg-secondary text-secondary-foreground hover:bg-secondary/80",
        ghost: "hover:bg-accent hover:text-accent-foreground",
        link: "text-primary underline-offset-4 hover:underline",
      },
      size: {
        default: "h-10 px-4 py-2",
        sm: "h-9 rounded-md px-3",
        lg: "h-11 rounded-md px-8",
        icon: "h-10 w-10",
      },
    },
    defaultVariants: { variant: "default", size: "default" },
  },
);

export interface ButtonProps
  extends React.ButtonHTMLAttributes<HTMLButtonElement>,
    VariantProps<typeof buttonVariants> {
  asChild?: boolean;
}

export const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(
  ({ className, variant, size, asChild = false, ...props }, ref) => {
    const Comp = asChild ? Slot : "button";
    return <Comp className={cn(buttonVariants({ variant, size, className }))} ref={ref} {...props} />;
  },
);
Button.displayName = "Button";

export { buttonVariants };
```

Add `@radix-ui/react-slot` to dependencies (`pnpm add @radix-ui/react-slot`).

- [ ] **Step 9: Smoke-test the Button in `App.tsx`**

```tsx
import { Button } from "@/components/ui/button";

export function App() {
  return (
    <div className="p-6 space-y-4">
      <h1 className="text-2xl font-semibold">ATLAS</h1>
      <Button>Hello</Button>
    </div>
  );
}
```

- [ ] **Step 10: `pnpm dev`, verify the styled button renders**

Open `http://localhost:5173`. The button should be filled with the primary color and have hover styles.

If Tailwind v4 misbehaves, drop to v3.4: `pnpm remove @tailwindcss/vite tailwindcss && pnpm add -D tailwindcss@^3.4.0`, replace `@import "tailwindcss";` in `index.css` with the three v3 directives (`@tailwind base; @tailwind components; @tailwind utilities;`), add a fuller `tailwind.config.ts` extending colors via CSS vars, remove `tailwindcss()` from `vite.config.ts`, and run `pnpm dev` again. Document the deviation in the commit message.

- [ ] **Step 11: Commit**

```
git add apps/web/package.json apps/web/pnpm-lock.yaml apps/web/vite.config.ts apps/web/tailwind.config.ts apps/web/postcss.config.js apps/web/components.json apps/web/src/
git commit -m "feat(atlas-web): wire Tailwind v4 + first shadcn Button primitive"
```

(If you fell back to Tailwind v3, use a commit message that says so explicitly.)

---

## Task B3: Configure ESLint + Prettier + Vitest

**Files:**
- Modify: `apps/web/package.json` (deps)
- Create: `apps/web/eslint.config.js`
- Create: `apps/web/.prettierrc.json`
- Create: `apps/web/vitest.config.ts`
- Create: `apps/web/src/tests/setup.ts`
- Create: `apps/web/src/tests/sanity.test.ts` (placeholder so the test runner has something to run)

- [ ] **Step 1: Add deps**

Add to `devDependencies`:
```
"@testing-library/dom": "^10.4.0",
"@testing-library/jest-dom": "^6.6.0",
"@testing-library/react": "^16.0.0",
"@testing-library/user-event": "^14.5.0",
"@typescript-eslint/eslint-plugin": "^8.0.0",
"@typescript-eslint/parser": "^8.0.0",
"@vitest/ui": "^2.1.0",
"eslint": "^9.10.0",
"eslint-plugin-react-hooks": "^5.0.0",
"eslint-plugin-react-refresh": "^0.4.0",
"globals": "^15.0.0",
"jsdom": "^25.0.0",
"prettier": "^3.3.0",
"prettier-plugin-tailwindcss": "^0.6.0",
"typescript-eslint": "^8.0.0",
"vitest": "^2.1.0"
```

Run `pnpm install`.

- [ ] **Step 2: Create `apps/web/eslint.config.js`**

```js
import js from "@eslint/js";
import globals from "globals";
import reactHooks from "eslint-plugin-react-hooks";
import reactRefresh from "eslint-plugin-react-refresh";
import tseslint from "typescript-eslint";

export default tseslint.config(
  { ignores: ["dist", "node_modules"] },
  {
    extends: [js.configs.recommended, ...tseslint.configs.recommended],
    files: ["**/*.{ts,tsx}"],
    languageOptions: {
      ecmaVersion: 2022,
      globals: globals.browser,
    },
    plugins: {
      "react-hooks": reactHooks,
      "react-refresh": reactRefresh,
    },
    rules: {
      ...reactHooks.configs.recommended.rules,
      "react-refresh/only-export-components": ["warn", { allowConstantExport: true }],
      "@typescript-eslint/no-unused-vars": ["warn", { argsIgnorePattern: "^_" }],
    },
  },
);
```

(Add `@eslint/js` to devDependencies and `pnpm install` again if not already present.)

- [ ] **Step 3: Create `apps/web/.prettierrc.json`**

```json
{
  "semi": true,
  "singleQuote": false,
  "trailingComma": "all",
  "printWidth": 100,
  "plugins": ["prettier-plugin-tailwindcss"]
}
```

- [ ] **Step 4: Create `apps/web/vitest.config.ts`**

```ts
import path from "node:path";
import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: { "@": path.resolve(__dirname, "./src") },
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/tests/setup.ts"],
    css: true,
  },
});
```

- [ ] **Step 5: Create `apps/web/src/tests/setup.ts`**

```ts
import "@testing-library/jest-dom/vitest";
import { afterEach } from "vitest";
import { cleanup } from "@testing-library/react";

afterEach(() => cleanup());
```

- [ ] **Step 6: Create a sanity test `apps/web/src/tests/sanity.test.ts`**

```ts
import { describe, expect, it } from "vitest";

describe("vitest", () => {
  it("runs", () => {
    expect(1 + 1).toBe(2);
  });
});
```

- [ ] **Step 7: Run lint, typecheck, test**

```
cd apps/web && pnpm lint && pnpm typecheck && pnpm test
```

Expected: all pass. The sanity test runs in jsdom.

- [ ] **Step 8: Commit**

```
git add apps/web/package.json apps/web/pnpm-lock.yaml apps/web/eslint.config.js apps/web/.prettierrc.json apps/web/vitest.config.ts apps/web/src/tests/
git commit -m "feat(atlas-web): wire ESLint flat config + Prettier + Vitest jsdom"
```

---

## Task B4: Add `lib/api.ts` + `lib/ws-protocol.ts` + protocol unit tests

**Files:**
- Create: `apps/web/src/lib/api.ts`
- Create: `apps/web/src/lib/ws-protocol.ts`
- Create: `apps/web/src/tests/ws-protocol.test.ts`

`api.ts` is a tiny `fetch` wrapper: same-origin URLs, JSON encode/decode, throws on non-2xx with the parsed error body. `ws-protocol.ts` mirrors `atlas_core.models.messages.StreamEventType` as a discriminated union plus type guards. The test file is one of the spec's five Vitest tests.

- [ ] **Step 1: Write `lib/api.ts`**

```ts
export class ApiError extends Error {
  constructor(public readonly status: number, public readonly body: unknown, message: string) {
    super(message);
    this.name = "ApiError";
  }
}

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  const init: RequestInit = {
    method,
    headers: body !== undefined ? { "content-type": "application/json" } : undefined,
    body: body !== undefined ? JSON.stringify(body) : undefined,
  };
  const res = await fetch(path, init);
  if (!res.ok) {
    let parsed: unknown = null;
    try {
      parsed = await res.json();
    } catch {
      // body wasn't JSON; ignore.
    }
    throw new ApiError(res.status, parsed, `${method} ${path} → ${res.status}`);
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

export const api = {
  get: <T>(path: string) => request<T>("GET", path),
  post: <T>(path: string, body?: unknown) => request<T>("POST", path, body),
  patch: <T>(path: string, body?: unknown) => request<T>("PATCH", path, body),
  delete: <T = void>(path: string) => request<T>("DELETE", path),
  postForm: async <T>(path: string, form: FormData): Promise<T> => {
    const res = await fetch(path, { method: "POST", body: form });
    if (!res.ok) {
      let parsed: unknown = null;
      try { parsed = await res.json(); } catch { /* */ }
      throw new ApiError(res.status, parsed, `POST ${path} → ${res.status}`);
    }
    return (await res.json()) as T;
  },
};
```

- [ ] **Step 2: Write `lib/ws-protocol.ts`**

```ts
// Mirrors atlas_core.models.messages.StreamEventType. If the backend
// adds a new event, add it here AND a guard, AND extend the test file.

export type Citation = {
  id: number;
  title: string;
  score: number;
  chunk_id: string;
  text_preview: string;
};

export type StreamEvent =
  | { type: "chat.token"; payload: { token: string }; sequence: number }
  | { type: "chat.tool_use"; payload: { name: string; arguments: Record<string, unknown>; id?: string }; sequence: number }
  | { type: "chat.tool_result"; payload: { id?: string; result: unknown }; sequence: number }
  | { type: "rag.context"; payload: { citations: Citation[] }; sequence: number }
  | { type: "chat.done"; payload: Record<string, unknown>; sequence: number }
  | { type: "chat.error"; payload: { code: string; message: string }; sequence: number };

export type ChatMessageOut = {
  type: "chat.message";
  payload: {
    text: string;
    project_id: string;
    model_override?: string;
    rag_enabled?: boolean;
    top_k_context?: number;
    temperature?: number;
  };
};

export function parseStreamEvent(raw: string): StreamEvent | null {
  let data: unknown;
  try { data = JSON.parse(raw); } catch { return null; }
  if (typeof data !== "object" || data === null) return null;
  const obj = data as Record<string, unknown>;
  if (typeof obj.type !== "string" || typeof obj.sequence !== "number") return null;
  if (typeof obj.payload !== "object" || obj.payload === null) return null;
  switch (obj.type) {
    case "chat.token":
    case "chat.tool_use":
    case "chat.tool_result":
    case "rag.context":
    case "chat.done":
    case "chat.error":
      return obj as unknown as StreamEvent;
    default:
      return null;
  }
}

export function isToken(e: StreamEvent): e is Extract<StreamEvent, { type: "chat.token" }> {
  return e.type === "chat.token";
}
export function isRagContext(e: StreamEvent): e is Extract<StreamEvent, { type: "rag.context" }> {
  return e.type === "rag.context";
}
export function isToolUse(e: StreamEvent): e is Extract<StreamEvent, { type: "chat.tool_use" }> {
  return e.type === "chat.tool_use";
}
export function isToolResult(e: StreamEvent): e is Extract<StreamEvent, { type: "chat.tool_result" }> {
  return e.type === "chat.tool_result";
}
export function isDone(e: StreamEvent): e is Extract<StreamEvent, { type: "chat.done" }> {
  return e.type === "chat.done";
}
export function isError(e: StreamEvent): e is Extract<StreamEvent, { type: "chat.error" }> {
  return e.type === "chat.error";
}
```

- [ ] **Step 3: Write `tests/ws-protocol.test.ts`**

```ts
import { describe, expect, it } from "vitest";
import { parseStreamEvent, isToken, isRagContext, isError } from "@/lib/ws-protocol";

describe("parseStreamEvent", () => {
  it("returns null for non-JSON", () => {
    expect(parseStreamEvent("not json")).toBeNull();
  });

  it("returns null for missing fields", () => {
    expect(parseStreamEvent(JSON.stringify({ type: "chat.token" }))).toBeNull();
    expect(parseStreamEvent(JSON.stringify({ payload: {}, sequence: 0 }))).toBeNull();
  });

  it("returns null for unknown event types", () => {
    expect(
      parseStreamEvent(JSON.stringify({ type: "made.up", payload: {}, sequence: 0 })),
    ).toBeNull();
  });

  it("parses every documented event", () => {
    const cases = [
      { type: "chat.token", payload: { token: "hi" }, sequence: 0 },
      { type: "chat.tool_use", payload: { name: "x", arguments: {} }, sequence: 1 },
      { type: "chat.tool_result", payload: { result: null }, sequence: 2 },
      {
        type: "rag.context",
        payload: { citations: [{ id: 1, title: "T", score: 0.9, chunk_id: "x", text_preview: "..." }] },
        sequence: 3,
      },
      { type: "chat.done", payload: {}, sequence: 4 },
      { type: "chat.error", payload: { code: "x", message: "y" }, sequence: 5 },
    ];
    for (const c of cases) {
      const parsed = parseStreamEvent(JSON.stringify(c));
      expect(parsed).not.toBeNull();
      expect(parsed?.type).toBe(c.type);
    }
  });

  it("type guards narrow correctly", () => {
    const tok = parseStreamEvent(JSON.stringify({ type: "chat.token", payload: { token: "a" }, sequence: 0 }));
    expect(tok && isToken(tok)).toBe(true);
    expect(tok && isToken(tok) && tok.payload.token).toBe("a");

    const rag = parseStreamEvent(JSON.stringify({
      type: "rag.context",
      payload: { citations: [] },
      sequence: 0,
    }));
    expect(rag && isRagContext(rag)).toBe(true);

    const err = parseStreamEvent(JSON.stringify({
      type: "chat.error",
      payload: { code: "bad", message: "boom" },
      sequence: 0,
    }));
    expect(err && isError(err)).toBe(true);
  });
});
```

- [ ] **Step 4: Run tests, confirm they pass**

```
cd apps/web && pnpm test
```

Expected: 5 protocol tests pass + the sanity test = 6 total passing.

- [ ] **Step 5: Commit**

```
git add apps/web/src/lib/api.ts apps/web/src/lib/ws-protocol.ts apps/web/src/tests/ws-protocol.test.ts
git commit -m "feat(atlas-web): add api fetch wrapper + WS StreamEvent type guards (TDD)"
```

---

## Task B5: Add zustand store + React Query provider + router scaffold

**Files:**
- Create: `apps/web/src/stores/atlas-store.ts`
- Modify: `apps/web/src/main.tsx` (wrap with QueryClientProvider + RouterProvider)
- Modify: `apps/web/src/App.tsx` (becomes the layout shell)
- Create: `apps/web/src/routes/index.tsx` (placeholder)
- Create: `apps/web/src/routes/project.tsx` (placeholder)

- [ ] **Step 1: Create `src/stores/atlas-store.ts`**

```ts
import { create } from "zustand";

type AtlasState = {
  auth: { user_id: "matt" };
  ui: {
    sidebar_collapsed: boolean;
    rag_drawer_open: boolean;
    rag_drawer_auto_opened_for_session: Record<string, boolean>;
  };
  models: {
    selected_id_per_session: Record<string, string>;
  };
  toggleSidebar: () => void;
  setRagDrawerOpen: (open: boolean) => void;
  markRagDrawerAutoOpened: (session_id: string) => void;
  setSelectedModel: (session_id: string, model_id: string) => void;
};

export const useAtlasStore = create<AtlasState>((set) => ({
  auth: { user_id: "matt" },
  ui: {
    sidebar_collapsed: false,
    rag_drawer_open: false,
    rag_drawer_auto_opened_for_session: {},
  },
  models: { selected_id_per_session: {} },
  toggleSidebar: () =>
    set((s) => ({ ui: { ...s.ui, sidebar_collapsed: !s.ui.sidebar_collapsed } })),
  setRagDrawerOpen: (open) =>
    set((s) => ({ ui: { ...s.ui, rag_drawer_open: open } })),
  markRagDrawerAutoOpened: (session_id) =>
    set((s) => ({
      ui: {
        ...s.ui,
        rag_drawer_auto_opened_for_session: {
          ...s.ui.rag_drawer_auto_opened_for_session,
          [session_id]: true,
        },
      },
    })),
  setSelectedModel: (session_id, model_id) =>
    set((s) => ({
      models: {
        selected_id_per_session: {
          ...s.models.selected_id_per_session,
          [session_id]: model_id,
        },
      },
    })),
}));
```

- [ ] **Step 2: Create placeholder `src/routes/index.tsx`**

```tsx
export function IndexRoute() {
  return <div className="p-6">Select a project from the sidebar.</div>;
}
```

- [ ] **Step 3: Create placeholder `src/routes/project.tsx`**

```tsx
import { useParams } from "react-router-dom";

export function ProjectRoute() {
  const { id } = useParams<{ id: string }>();
  return <div className="p-6">Project: {id}</div>;
}
```

- [ ] **Step 4: Rewrite `src/App.tsx` as the shell**

```tsx
import { Outlet } from "react-router-dom";

export function App() {
  return (
    <div className="flex h-screen">
      <aside className="w-64 border-r bg-muted/30 p-4">
        <div className="font-semibold">ATLAS</div>
        {/* Sidebar will be replaced in Task C2. */}
      </aside>
      <main className="flex-1 overflow-hidden">
        <Outlet />
      </main>
    </div>
  );
}
```

- [ ] **Step 5: Rewrite `src/main.tsx` to mount providers + router**

```tsx
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { createBrowserRouter, RouterProvider } from "react-router-dom";
import { App } from "./App";
import { IndexRoute } from "./routes";
import { ProjectRoute } from "./routes/project";
import "./index.css";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: { staleTime: 30_000, refetchOnWindowFocus: false },
  },
});

const router = createBrowserRouter([
  {
    path: "/",
    element: <App />,
    children: [
      { index: true, element: <IndexRoute /> },
      { path: "projects/:id", element: <ProjectRoute /> },
    ],
  },
]);

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>
  </StrictMode>,
);
```

Add `apps/web/src/routes/index.ts` re-export so the import works:

```ts
// apps/web/src/routes/index.ts
export { IndexRoute } from "./index.tsx";
```

(Or rename `index.tsx` → `index.tsx` and import directly. Either is fine.)

- [ ] **Step 6: `pnpm dev`, verify routes**

`http://localhost:5173/` should show "ATLAS" sidebar plus "Select a project from the sidebar." `http://localhost:5173/projects/abc` should show "Project: abc".

- [ ] **Step 7: `pnpm typecheck && pnpm lint && pnpm test`**

All pass.

- [ ] **Step 8: Commit**

```
git add apps/web/src/
git commit -m "feat(atlas-web): zustand store + React Query + router scaffold"
```

---

## Task C1: Add `use-projects` hooks (React Query)

**Files:**
- Create: `apps/web/src/hooks/use-projects.ts`

- [ ] **Step 1: Write the file**

```ts
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";

export type Project = {
  id: string;
  user_id: string;
  name: string;
  description: string | null;
  status: "active" | "paused" | "archived";
  privacy_level: "cloud_ok" | "local_only";
  default_model: string;
  enabled_plugins: string[];
  created_at: string;
  updated_at: string;
};

export type ProjectCreateBody = {
  name: string;
  description?: string;
  privacy_level?: "cloud_ok" | "local_only";
  default_model: string;
  enabled_plugins?: string[];
};

export type ProjectUpdateBody = Partial<ProjectCreateBody> & { status?: Project["status"] };

const KEY = ["projects"] as const;

export function useProjects() {
  return useQuery({
    queryKey: KEY,
    queryFn: () => api.get<Project[]>("/api/v1/projects"),
  });
}

export function useProject(id: string | undefined) {
  return useQuery({
    queryKey: [...KEY, id],
    queryFn: () => api.get<Project>(`/api/v1/projects/${id}`),
    enabled: Boolean(id),
  });
}

export function useCreateProject() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: ProjectCreateBody) => api.post<Project>("/api/v1/projects", body),
    onSuccess: () => qc.invalidateQueries({ queryKey: KEY }),
  });
}

export function useUpdateProject(id: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: ProjectUpdateBody) => api.patch<Project>(`/api/v1/projects/${id}`, body),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: KEY });
      qc.invalidateQueries({ queryKey: [...KEY, id] });
    },
  });
}

export function useDeleteProject() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.delete(`/api/v1/projects/${id}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: KEY }),
  });
}
```

- [ ] **Step 2: `pnpm typecheck`** — passes.

- [ ] **Step 3: Commit**

```
git add apps/web/src/hooks/use-projects.ts
git commit -m "feat(atlas-web): add use-projects React Query hooks"
```

---

## Task C2: Build the sidebar with project list + active highlighting

**Files:**
- Create: `apps/web/src/components/sidebar/sidebar.tsx`
- Create: `apps/web/src/components/sidebar/project-list.tsx`
- Modify: `apps/web/src/App.tsx` (replace placeholder sidebar)

- [ ] **Step 1: Write `project-list.tsx`**

```tsx
import { Link, useParams } from "react-router-dom";
import { useProjects } from "@/hooks/use-projects";
import { cn } from "@/lib/cn";

export function ProjectList() {
  const { id: activeId } = useParams<{ id: string }>();
  const { data, isLoading, error } = useProjects();

  if (isLoading) return <div className="text-sm text-muted-foreground">Loading…</div>;
  if (error) return <div className="text-sm text-destructive">Failed to load projects.</div>;
  if (!data || data.length === 0) {
    return <div className="text-sm text-muted-foreground">No projects yet.</div>;
  }

  return (
    <ul className="space-y-1">
      {data
        .filter((p) => p.status !== "archived")
        .map((p) => (
          <li key={p.id}>
            <Link
              to={`/projects/${p.id}`}
              className={cn(
                "block rounded-md px-2 py-1.5 text-sm hover:bg-accent",
                activeId === p.id && "bg-accent font-medium",
              )}
            >
              {p.name}
            </Link>
          </li>
        ))}
    </ul>
  );
}
```

- [ ] **Step 2: Write `sidebar.tsx`**

```tsx
import { ProjectList } from "./project-list";

export function Sidebar() {
  return (
    <aside className="flex w-64 flex-col border-r bg-muted/30">
      <div className="flex h-12 items-center px-4 font-semibold">ATLAS</div>
      <div className="flex-1 overflow-y-auto px-2 py-2">
        <div className="mb-1 px-2 text-xs uppercase tracking-wider text-muted-foreground">
          Projects
        </div>
        <ProjectList />
        {/* NewProjectButton mounts here in Task C3 */}
      </div>
      <div className="border-t p-3 text-xs text-muted-foreground">⚙ Settings</div>
    </aside>
  );
}
```

- [ ] **Step 3: Update `App.tsx`**

```tsx
import { Outlet } from "react-router-dom";
import { Sidebar } from "@/components/sidebar/sidebar";

export function App() {
  return (
    <div className="flex h-screen">
      <Sidebar />
      <main className="flex flex-1 flex-col overflow-hidden">
        <Outlet />
      </main>
    </div>
  );
}
```

- [ ] **Step 4: Verify in browser**

`pnpm dev`. With backend running and at least one project in the DB (create one via curl if needed: `curl -X POST localhost:8000/api/v1/projects -H 'content-type: application/json' -d '{"name":"Test","default_model":"claude-sonnet-4-6"}'`), the sidebar should list it.

- [ ] **Step 5: Commit**

```
git add apps/web/src/components/sidebar/ apps/web/src/App.tsx
git commit -m "feat(atlas-web): sidebar with live project list + active highlight"
```

---

## Task C3: Build NewProjectModal + create flow

**Files:**
- Create: `apps/web/src/components/sidebar/new-project-modal.tsx`
- Create: `apps/web/src/components/ui/dialog.tsx` (hand-written shadcn Dialog primitive)
- Create: `apps/web/src/components/ui/input.tsx`
- Create: `apps/web/src/components/ui/label.tsx`
- Create: `apps/web/src/components/ui/textarea.tsx`
- Modify: `apps/web/src/components/sidebar/sidebar.tsx` (mount the modal trigger)
- Modify: `apps/web/package.json` (add `@radix-ui/react-dialog`, `@radix-ui/react-label`)

- [ ] **Step 1: Add deps**

```
pnpm add @radix-ui/react-dialog @radix-ui/react-label
```

- [ ] **Step 2: Hand-write the shadcn Dialog primitive**

`apps/web/src/components/ui/dialog.tsx`:

```tsx
import * as React from "react";
import * as DialogPrimitive from "@radix-ui/react-dialog";
import { X } from "lucide-react";
import { cn } from "@/lib/cn";

const Dialog = DialogPrimitive.Root;
const DialogTrigger = DialogPrimitive.Trigger;
const DialogPortal = DialogPrimitive.Portal;

const DialogOverlay = React.forwardRef<
  React.ElementRef<typeof DialogPrimitive.Overlay>,
  React.ComponentPropsWithoutRef<typeof DialogPrimitive.Overlay>
>(({ className, ...props }, ref) => (
  <DialogPrimitive.Overlay
    ref={ref}
    className={cn("fixed inset-0 z-50 bg-black/50 data-[state=open]:animate-in data-[state=closed]:animate-out data-[state=closed]:fade-out-0 data-[state=open]:fade-in-0", className)}
    {...props}
  />
));
DialogOverlay.displayName = "DialogOverlay";

const DialogContent = React.forwardRef<
  React.ElementRef<typeof DialogPrimitive.Content>,
  React.ComponentPropsWithoutRef<typeof DialogPrimitive.Content>
>(({ className, children, ...props }, ref) => (
  <DialogPortal>
    <DialogOverlay />
    <DialogPrimitive.Content
      ref={ref}
      className={cn(
        "fixed left-[50%] top-[50%] z-50 grid w-full max-w-lg translate-x-[-50%] translate-y-[-50%] gap-4 border bg-background p-6 shadow-lg sm:rounded-lg",
        className,
      )}
      {...props}
    >
      {children}
      <DialogPrimitive.Close className="absolute right-4 top-4 rounded-sm opacity-70 hover:opacity-100">
        <X className="h-4 w-4" />
      </DialogPrimitive.Close>
    </DialogPrimitive.Content>
  </DialogPortal>
));
DialogContent.displayName = "DialogContent";

const DialogHeader = ({ className, ...props }: React.HTMLAttributes<HTMLDivElement>) => (
  <div className={cn("flex flex-col gap-1.5", className)} {...props} />
);

const DialogFooter = ({ className, ...props }: React.HTMLAttributes<HTMLDivElement>) => (
  <div className={cn("flex flex-row justify-end gap-2", className)} {...props} />
);

const DialogTitle = React.forwardRef<
  React.ElementRef<typeof DialogPrimitive.Title>,
  React.ComponentPropsWithoutRef<typeof DialogPrimitive.Title>
>(({ className, ...props }, ref) => (
  <DialogPrimitive.Title ref={ref} className={cn("text-lg font-semibold", className)} {...props} />
));
DialogTitle.displayName = "DialogTitle";

const DialogDescription = React.forwardRef<
  React.ElementRef<typeof DialogPrimitive.Description>,
  React.ComponentPropsWithoutRef<typeof DialogPrimitive.Description>
>(({ className, ...props }, ref) => (
  <DialogPrimitive.Description ref={ref} className={cn("text-sm text-muted-foreground", className)} {...props} />
));
DialogDescription.displayName = "DialogDescription";

export {
  Dialog,
  DialogTrigger,
  DialogContent,
  DialogHeader,
  DialogFooter,
  DialogTitle,
  DialogDescription,
};
```

- [ ] **Step 3: Hand-write Input, Label, Textarea**

`apps/web/src/components/ui/input.tsx`:
```tsx
import * as React from "react";
import { cn } from "@/lib/cn";

export const Input = React.forwardRef<HTMLInputElement, React.InputHTMLAttributes<HTMLInputElement>>(
  ({ className, ...props }, ref) => (
    <input
      ref={ref}
      className={cn(
        "flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm ring-offset-background file:border-0 file:bg-transparent file:text-sm file:font-medium placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-50",
        className,
      )}
      {...props}
    />
  ),
);
Input.displayName = "Input";
```

`apps/web/src/components/ui/label.tsx`:
```tsx
import * as React from "react";
import * as LabelPrimitive from "@radix-ui/react-label";
import { cn } from "@/lib/cn";

export const Label = React.forwardRef<
  React.ElementRef<typeof LabelPrimitive.Root>,
  React.ComponentPropsWithoutRef<typeof LabelPrimitive.Root>
>(({ className, ...props }, ref) => (
  <LabelPrimitive.Root ref={ref} className={cn("text-sm font-medium", className)} {...props} />
));
Label.displayName = "Label";
```

`apps/web/src/components/ui/textarea.tsx`:
```tsx
import * as React from "react";
import { cn } from "@/lib/cn";

export const Textarea = React.forwardRef<
  HTMLTextAreaElement,
  React.TextareaHTMLAttributes<HTMLTextAreaElement>
>(({ className, ...props }, ref) => (
  <textarea
    ref={ref}
    className={cn(
      "flex min-h-[80px] w-full rounded-md border border-input bg-background px-3 py-2 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-50",
      className,
    )}
    {...props}
  />
));
Textarea.displayName = "Textarea";
```

- [ ] **Step 4: Write `new-project-modal.tsx`**

```tsx
import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { Plus } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { useCreateProject } from "@/hooks/use-projects";

export function NewProjectModal() {
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [defaultModel, setDefaultModel] = useState("claude-sonnet-4-6");
  const create = useCreateProject();
  const navigate = useNavigate();

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!name.trim()) return;
    const project = await create.mutateAsync({
      name: name.trim(),
      description: description.trim() || undefined,
      default_model: defaultModel,
    });
    setOpen(false);
    setName("");
    setDescription("");
    navigate(`/projects/${project.id}`);
  };

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button variant="ghost" size="sm" className="w-full justify-start">
          <Plus className="h-4 w-4" />
          New Project
        </Button>
      </DialogTrigger>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>New Project</DialogTitle>
          <DialogDescription>Projects scope your knowledge and conversations.</DialogDescription>
        </DialogHeader>
        <form onSubmit={submit} className="space-y-4">
          <div className="space-y-2">
            <Label htmlFor="np-name">Name</Label>
            <Input
              id="np-name"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="CircleK MMM"
              required
              autoFocus
            />
          </div>
          <div className="space-y-2">
            <Label htmlFor="np-description">Description</Label>
            <Textarea
              id="np-description"
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              placeholder="Optional notes about scope or stakeholders"
            />
          </div>
          <div className="space-y-2">
            <Label htmlFor="np-model">Default model</Label>
            <Input
              id="np-model"
              value={defaultModel}
              onChange={(e) => setDefaultModel(e.target.value)}
              placeholder="claude-sonnet-4-6"
              required
            />
          </div>
          {create.isError && (
            <div className="text-sm text-destructive">
              {create.error instanceof Error ? create.error.message : "Failed to create project"}
            </div>
          )}
          <DialogFooter>
            <Button type="button" variant="ghost" onClick={() => setOpen(false)}>
              Cancel
            </Button>
            <Button type="submit" disabled={create.isPending || !name.trim()}>
              {create.isPending ? "Creating…" : "Create"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
```

- [ ] **Step 5: Mount in `sidebar.tsx`**

Insert below `<ProjectList />`:

```tsx
<div className="mt-2">
  <NewProjectModal />
</div>
```

(And `import { NewProjectModal } from "./new-project-modal";`)

- [ ] **Step 6: Verify in browser**

Click "+ New Project", fill in the form, submit. The new project should appear in the sidebar; you should be navigated to `/projects/{id}`.

- [ ] **Step 7: `pnpm typecheck && pnpm lint && pnpm test`**

All pass.

- [ ] **Step 8: Commit**

```
git add apps/web/package.json apps/web/pnpm-lock.yaml apps/web/src/
git commit -m "feat(atlas-web): NewProjectModal + Dialog/Input/Label/Textarea primitives"
```

---

## Task C4: Add ProjectMenu (rename + delete) + index route redirect

**Files:**
- Create: `apps/web/src/components/sidebar/project-menu.tsx`
- Create: `apps/web/src/components/ui/dropdown-menu.tsx` (hand-written shadcn primitive)
- Modify: `apps/web/src/components/sidebar/project-list.tsx` (add menu trigger)
- Modify: `apps/web/src/routes/index.tsx` (redirect)
- Modify: `apps/web/package.json` (add `@radix-ui/react-dropdown-menu`)

- [ ] **Step 1: Add dep**

```
pnpm add @radix-ui/react-dropdown-menu
```

- [ ] **Step 2: Hand-write `dropdown-menu.tsx`**

```tsx
import * as React from "react";
import * as DropdownMenuPrimitive from "@radix-ui/react-dropdown-menu";
import { cn } from "@/lib/cn";

const DropdownMenu = DropdownMenuPrimitive.Root;
const DropdownMenuTrigger = DropdownMenuPrimitive.Trigger;

const DropdownMenuContent = React.forwardRef<
  React.ElementRef<typeof DropdownMenuPrimitive.Content>,
  React.ComponentPropsWithoutRef<typeof DropdownMenuPrimitive.Content>
>(({ className, sideOffset = 4, ...props }, ref) => (
  <DropdownMenuPrimitive.Portal>
    <DropdownMenuPrimitive.Content
      ref={ref}
      sideOffset={sideOffset}
      className={cn(
        "z-50 min-w-[8rem] overflow-hidden rounded-md border bg-popover p-1 text-popover-foreground shadow-md",
        className,
      )}
      {...props}
    />
  </DropdownMenuPrimitive.Portal>
));
DropdownMenuContent.displayName = "DropdownMenuContent";

const DropdownMenuItem = React.forwardRef<
  React.ElementRef<typeof DropdownMenuPrimitive.Item>,
  React.ComponentPropsWithoutRef<typeof DropdownMenuPrimitive.Item>
>(({ className, ...props }, ref) => (
  <DropdownMenuPrimitive.Item
    ref={ref}
    className={cn(
      "relative flex cursor-pointer select-none items-center rounded-sm px-2 py-1.5 text-sm outline-none focus:bg-accent focus:text-accent-foreground data-[disabled]:pointer-events-none data-[disabled]:opacity-50",
      className,
    )}
    {...props}
  />
));
DropdownMenuItem.displayName = "DropdownMenuItem";

export { DropdownMenu, DropdownMenuTrigger, DropdownMenuContent, DropdownMenuItem };
```

Add CSS variables for popover to `index.css`:
```css
@theme {
  /* ...existing... */
  --color-popover: 0 0% 100%;
  --color-popover-foreground: 240 10% 4%;
}
```

- [ ] **Step 3: Write `project-menu.tsx`**

```tsx
import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { MoreHorizontal } from "lucide-react";
import {
  DropdownMenu,
  DropdownMenuTrigger,
  DropdownMenuContent,
  DropdownMenuItem,
} from "@/components/ui/dropdown-menu";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogFooter,
  DialogTitle,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { useUpdateProject, useDeleteProject, type Project } from "@/hooks/use-projects";

export function ProjectMenu({ project }: { project: Project }) {
  const [renameOpen, setRenameOpen] = useState(false);
  const [deleteOpen, setDeleteOpen] = useState(false);
  const [name, setName] = useState(project.name);
  const update = useUpdateProject(project.id);
  const remove = useDeleteProject();
  const navigate = useNavigate();

  const submitRename = async (e: React.FormEvent) => {
    e.preventDefault();
    await update.mutateAsync({ name: name.trim() });
    setRenameOpen(false);
  };
  const confirmDelete = async () => {
    await remove.mutateAsync(project.id);
    setDeleteOpen(false);
    navigate("/");
  };

  return (
    <>
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <button
            className="opacity-0 group-hover:opacity-100 hover:bg-accent rounded p-1"
            onClick={(e) => e.stopPropagation()}
          >
            <MoreHorizontal className="h-3.5 w-3.5" />
          </button>
        </DropdownMenuTrigger>
        <DropdownMenuContent>
          <DropdownMenuItem onSelect={() => setRenameOpen(true)}>Rename</DropdownMenuItem>
          <DropdownMenuItem
            className="text-destructive focus:text-destructive"
            onSelect={() => setDeleteOpen(true)}
          >
            Delete
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>

      <Dialog open={renameOpen} onOpenChange={setRenameOpen}>
        <DialogContent>
          <DialogHeader><DialogTitle>Rename project</DialogTitle></DialogHeader>
          <form onSubmit={submitRename} className="space-y-4">
            <Input value={name} onChange={(e) => setName(e.target.value)} autoFocus />
            <DialogFooter>
              <Button variant="ghost" type="button" onClick={() => setRenameOpen(false)}>Cancel</Button>
              <Button type="submit" disabled={update.isPending}>Save</Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>

      <Dialog open={deleteOpen} onOpenChange={setDeleteOpen}>
        <DialogContent>
          <DialogHeader><DialogTitle>Archive "{project.name}"?</DialogTitle></DialogHeader>
          <p className="text-sm text-muted-foreground">
            This is a soft delete — the project is hidden from the list but the data is preserved.
          </p>
          <DialogFooter>
            <Button variant="ghost" onClick={() => setDeleteOpen(false)}>Cancel</Button>
            <Button variant="destructive" onClick={confirmDelete} disabled={remove.isPending}>
              {remove.isPending ? "Archiving…" : "Archive"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}
```

- [ ] **Step 4: Mount the menu in `project-list.tsx`**

Wrap each project list item to use `group` and add the menu trigger:

```tsx
<li key={p.id} className="group flex items-center gap-1">
  <Link
    to={`/projects/${p.id}`}
    className={cn(
      "flex-1 rounded-md px-2 py-1.5 text-sm hover:bg-accent",
      activeId === p.id && "bg-accent font-medium",
    )}
  >
    {p.name}
  </Link>
  <ProjectMenu project={p} />
</li>
```

(And `import { ProjectMenu } from "./project-menu";`)

- [ ] **Step 5: Update `routes/index.tsx` to redirect to first project or render empty state**

```tsx
import { Navigate } from "react-router-dom";
import { useProjects } from "@/hooks/use-projects";

export function IndexRoute() {
  const { data, isLoading } = useProjects();
  if (isLoading) return <div className="p-6 text-sm text-muted-foreground">Loading…</div>;
  const active = data?.find((p) => p.status !== "archived");
  if (active) return <Navigate to={`/projects/${active.id}`} replace />;
  return (
    <div className="p-6">
      <p className="text-sm text-muted-foreground">
        No projects yet. Click "+ New Project" in the sidebar to create one.
      </p>
    </div>
  );
}
```

- [ ] **Step 6: Verify**

Reload `/` — redirects to first project. Hover a project in the sidebar, click `…`, rename, then delete (archive). Confirm.

- [ ] **Step 7: Commit**

```
git add apps/web/package.json apps/web/pnpm-lock.yaml apps/web/src/
git commit -m "feat(atlas-web): project rename/delete menu + index redirect"
```

---

## Task D1: Add `lib/session-storage.ts`

**Files:**
- Create: `apps/web/src/lib/session-storage.ts`

- [ ] **Step 1: Write the file**

```ts
const KEY = "atlas.session_ids.v1";

function readMap(): Record<string, string> {
  try {
    const raw = localStorage.getItem(KEY);
    if (!raw) return {};
    const parsed = JSON.parse(raw);
    return typeof parsed === "object" && parsed !== null ? parsed : {};
  } catch {
    return {};
  }
}

function writeMap(map: Record<string, string>): void {
  try {
    localStorage.setItem(KEY, JSON.stringify(map));
  } catch {
    // Quota or privacy mode — degrade silently; the user gets a non-persistent session.
  }
}

export function getOrCreateSessionId(project_id: string): string {
  const map = readMap();
  if (map[project_id]) return map[project_id];
  const id = crypto.randomUUID();
  writeMap({ ...map, [project_id]: id });
  return id;
}

export function clearSessionId(project_id: string): void {
  const map = readMap();
  delete map[project_id];
  writeMap(map);
}
```

- [ ] **Step 2: Commit**

```
git add apps/web/src/lib/session-storage.ts
git commit -m "feat(atlas-web): add per-project session_id storage"
```

---

## Task D2: Add `use-session-messages` and `use-models` hooks

**Files:**
- Create: `apps/web/src/hooks/use-session-messages.ts`
- Create: `apps/web/src/hooks/use-models.ts`

- [ ] **Step 1: Write `use-session-messages.ts`**

```ts
import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";

export type SessionMessage = {
  id: string;
  user_id: string;
  session_id: string;
  role: "user" | "assistant" | "system";
  content: string;
  tool_calls: Array<Record<string, unknown>> | null;
  rag_context: Array<Record<string, unknown>> | null;
  model: string | null;
  token_count: number | null;
  created_at: string;
};

export function useSessionMessages(session_id: string | undefined) {
  return useQuery({
    queryKey: ["sessions", session_id, "messages"],
    queryFn: () => api.get<SessionMessage[]>(`/api/v1/sessions/${session_id}/messages`),
    enabled: Boolean(session_id),
    staleTime: Infinity, // we manage the cache by appending live tokens through useAtlasChat
  });
}
```

- [ ] **Step 2: Write `use-models.ts`**

```ts
import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";

export type ModelSpec = {
  provider: string;
  model_id: string;
  context_window: number;
  supports_tools: boolean;
  supports_streaming: boolean;
};

export function useModels() {
  return useQuery({
    queryKey: ["models"],
    queryFn: () => api.get<ModelSpec[]>("/api/v1/models"),
    staleTime: 5 * 60_000,
  });
}
```

- [ ] **Step 3: `pnpm typecheck`** — passes.

- [ ] **Step 4: Commit**

```
git add apps/web/src/hooks/use-session-messages.ts apps/web/src/hooks/use-models.ts
git commit -m "feat(atlas-web): add use-session-messages + use-models React Query hooks"
```

---

## Task D3: Implement `useAtlasChat` hook with TDD

**Files:**
- Create: `apps/web/src/hooks/use-atlas-chat.ts`
- Create: `apps/web/src/tests/use-atlas-chat.test.ts`

This is the largest task. The hook is the heart of the chat UI. TDD with a fake `WebSocket`.

- [ ] **Step 1: Write the failing test file**

```ts
// apps/web/src/tests/use-atlas-chat.test.ts
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { useAtlasChat } from "@/hooks/use-atlas-chat";

class FakeWS {
  static instances: FakeWS[] = [];
  readyState = 0; // CONNECTING
  url: string;
  onopen: ((ev: Event) => void) | null = null;
  onmessage: ((ev: MessageEvent) => void) | null = null;
  onclose: ((ev: CloseEvent) => void) | null = null;
  onerror: ((ev: Event) => void) | null = null;
  sent: string[] = [];

  constructor(url: string) {
    this.url = url;
    FakeWS.instances.push(this);
    queueMicrotask(() => {
      this.readyState = 1; // OPEN
      this.onopen?.(new Event("open"));
    });
  }
  send(data: string) { this.sent.push(data); }
  close() {
    this.readyState = 3;
    this.onclose?.(new CloseEvent("close", { code: 1000 }));
  }
  emit(payload: unknown) {
    this.onmessage?.(new MessageEvent("message", { data: JSON.stringify(payload) }));
  }
  closeUnexpected() {
    this.readyState = 3;
    this.onclose?.(new CloseEvent("close", { code: 1006, wasClean: false }));
  }
}

const createWrapper = () => {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={qc}>{children}</QueryClientProvider>
  );
};

beforeEach(() => {
  // @ts-expect-error swap global WebSocket
  globalThis.WebSocket = FakeWS;
  FakeWS.instances = [];
});

afterEach(() => {
  vi.useRealTimers();
});

describe("useAtlasChat", () => {
  it("appends a blank assistant message on send and accumulates tokens", async () => {
    const { result } = renderHook(
      () => useAtlasChat({ session_id: "S1", project_id: "P1", model_id: "claude-sonnet-4-6" }),
      { wrapper: createWrapper() },
    );

    await waitFor(() => expect(FakeWS.instances.length).toBe(1));
    const ws = FakeWS.instances[0];
    await waitFor(() => expect(ws.readyState).toBe(1));

    act(() => result.current.send("hi"));

    expect(result.current.is_streaming).toBe(true);
    expect(result.current.messages.at(-1)).toMatchObject({ role: "assistant", content: "" });

    act(() => ws.emit({ type: "chat.token", payload: { token: "He" }, sequence: 0 }));
    act(() => ws.emit({ type: "chat.token", payload: { token: "llo" }, sequence: 1 }));

    expect(result.current.messages.at(-1)?.content).toBe("Hello");
  });

  it("finalizes on chat.done and clears is_streaming", async () => {
    const { result } = renderHook(
      () => useAtlasChat({ session_id: "S2", project_id: "P1", model_id: "m" }),
      { wrapper: createWrapper() },
    );
    await waitFor(() => expect(FakeWS.instances.length).toBe(1));
    const ws = FakeWS.instances[0];
    await waitFor(() => expect(ws.readyState).toBe(1));

    act(() => result.current.send("q"));
    act(() => ws.emit({ type: "chat.token", payload: { token: "ok" }, sequence: 0 }));
    act(() => ws.emit({ type: "chat.done", payload: {}, sequence: 1 }));

    expect(result.current.is_streaming).toBe(false);
    expect(result.current.messages.at(-1)?.content).toBe("ok");
  });

  it("captures rag.context citations", async () => {
    const { result } = renderHook(
      () => useAtlasChat({ session_id: "S3", project_id: "P1", model_id: "m" }),
      { wrapper: createWrapper() },
    );
    await waitFor(() => expect(FakeWS.instances.length).toBe(1));
    const ws = FakeWS.instances[0];
    await waitFor(() => expect(ws.readyState).toBe(1));

    act(() => result.current.send("q"));
    act(() =>
      ws.emit({
        type: "rag.context",
        payload: {
          citations: [{ id: 1, title: "T", score: 0.9, chunk_id: "x", text_preview: "..." }],
        },
        sequence: 0,
      }),
    );

    expect(result.current.rag_context).toHaveLength(1);
    expect(result.current.rag_context?.[0].title).toBe("T");
  });

  it("surfaces chat.error and clears streaming", async () => {
    const { result } = renderHook(
      () => useAtlasChat({ session_id: "S4", project_id: "P1", model_id: "m" }),
      { wrapper: createWrapper() },
    );
    await waitFor(() => expect(FakeWS.instances.length).toBe(1));
    const ws = FakeWS.instances[0];
    await waitFor(() => expect(ws.readyState).toBe(1));

    act(() => result.current.send("q"));
    act(() => ws.emit({ type: "chat.error", payload: { code: "x", message: "boom" }, sequence: 0 }));

    expect(result.current.error).toEqual({ code: "x", message: "boom" });
    expect(result.current.is_streaming).toBe(false);
  });

  it("reconnects with backoff on unexpected close", async () => {
    vi.useFakeTimers();
    const { result } = renderHook(
      () => useAtlasChat({ session_id: "S5", project_id: "P1", model_id: "m" }),
      { wrapper: createWrapper() },
    );
    await vi.runAllTimersAsync();
    expect(FakeWS.instances.length).toBe(1);

    act(() => FakeWS.instances[0].closeUnexpected());
    // First retry after ~1s
    await act(async () => { await vi.advanceTimersByTimeAsync(1100); });
    expect(FakeWS.instances.length).toBe(2);

    act(() => FakeWS.instances[1].closeUnexpected());
    // Second retry after ~2s
    await act(async () => { await vi.advanceTimersByTimeAsync(2100); });
    expect(FakeWS.instances.length).toBe(3);

    void result;
  });
});
```

- [ ] **Step 2: Run, confirm tests fail (no hook yet)**

```
cd apps/web && pnpm test src/tests/use-atlas-chat.test.ts
```

Expected: import error or runtime error.

- [ ] **Step 3: Implement `use-atlas-chat.ts`**

```ts
import { useCallback, useEffect, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  parseStreamEvent,
  isToken,
  isRagContext,
  isToolUse,
  isToolResult,
  isDone,
  isError,
  type Citation,
  type ChatMessageOut,
} from "@/lib/ws-protocol";
import { useSessionMessages, type SessionMessage } from "@/hooks/use-session-messages";

export type ToolCard = {
  id: string;
  name?: string;
  arguments?: Record<string, unknown>;
  result?: unknown;
};

export type ChatMessage = {
  client_id: string;
  role: "user" | "assistant" | "system";
  content: string;
  tool_cards?: ToolCard[];
  finalized: boolean;
};

const BACKOFF_MS = [1000, 2000, 4000, 8000, 16000, 30000];

export function useAtlasChat(opts: {
  session_id: string;
  project_id: string;
  model_id: string | undefined;
}) {
  const { session_id, project_id, model_id } = opts;
  const queryClient = useQueryClient();
  const { data: persisted } = useSessionMessages(session_id);

  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [rag_context, setRagContext] = useState<Citation[] | null>(null);
  const [is_streaming, setStreaming] = useState(false);
  const [error, setError] = useState<{ code: string; message: string } | null>(null);

  const wsRef = useRef<WebSocket | null>(null);
  const retryRef = useRef(0);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const aliveRef = useRef(true);

  // Hydrate messages from persisted on first load. We replace, not merge,
  // because persisted is the authoritative server state for this session.
  useEffect(() => {
    if (!persisted) return;
    setMessages(
      persisted.map(
        (m: SessionMessage): ChatMessage => ({
          client_id: m.id,
          role: m.role,
          content: m.content,
          finalized: true,
        }),
      ),
    );
  }, [persisted]);

  const connect = useCallback(() => {
    if (!aliveRef.current) return;
    const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
    const url = `${proto}//${window.location.host}/api/v1/ws/${session_id}`;
    const ws = new WebSocket(url);
    wsRef.current = ws;

    ws.onopen = () => {
      retryRef.current = 0;
    };

    ws.onmessage = (ev) => {
      const event = parseStreamEvent(typeof ev.data === "string" ? ev.data : "");
      if (!event) return;

      if (isToken(event)) {
        setMessages((prev) => {
          const last = prev[prev.length - 1];
          if (!last || last.role !== "assistant" || last.finalized) return prev;
          const updated = { ...last, content: last.content + event.payload.token };
          return [...prev.slice(0, -1), updated];
        });
        return;
      }
      if (isRagContext(event)) {
        setRagContext(event.payload.citations);
        return;
      }
      if (isToolUse(event)) {
        const id = event.payload.id ?? crypto.randomUUID();
        setMessages((prev) => {
          const last = prev[prev.length - 1];
          if (!last || last.role !== "assistant") return prev;
          const cards = last.tool_cards ?? [];
          return [
            ...prev.slice(0, -1),
            { ...last, tool_cards: [...cards, { id, name: event.payload.name, arguments: event.payload.arguments }] },
          ];
        });
        return;
      }
      if (isToolResult(event)) {
        const id = event.payload.id;
        setMessages((prev) => {
          const last = prev[prev.length - 1];
          if (!last || !last.tool_cards) return prev;
          const updated = last.tool_cards.map((c) =>
            c.id === id ? { ...c, result: event.payload.result } : c,
          );
          return [...prev.slice(0, -1), { ...last, tool_cards: updated }];
        });
        return;
      }
      if (isDone(event)) {
        setStreaming(false);
        setMessages((prev) => {
          const last = prev[prev.length - 1];
          if (!last || last.finalized) return prev;
          return [...prev.slice(0, -1), { ...last, finalized: true }];
        });
        // The new assistant turn was persisted server-side; refetch to pick up
        // canonical row ids and timestamps.
        queryClient.invalidateQueries({ queryKey: ["sessions", session_id, "messages"] });
        return;
      }
      if (isError(event)) {
        setError(event.payload);
        setStreaming(false);
        setMessages((prev) => {
          const last = prev[prev.length - 1];
          if (!last || last.finalized) return prev;
          return [...prev.slice(0, -1), { ...last, finalized: true }];
        });
        return;
      }
    };

    ws.onclose = (ev) => {
      if (!aliveRef.current) return;
      // Treat any non-1000 close as unexpected and try to reconnect.
      if (ev.code === 1000) return;
      // Finalize a partial message with a "(disconnected)" trailer.
      setMessages((prev) => {
        const last = prev[prev.length - 1];
        if (!last || last.finalized || last.role !== "assistant") return prev;
        return [
          ...prev.slice(0, -1),
          { ...last, content: last.content + "\n\n_(disconnected)_", finalized: true },
        ];
      });
      setStreaming(false);
      const idx = Math.min(retryRef.current, BACKOFF_MS.length - 1);
      const delay = BACKOFF_MS[idx];
      retryRef.current += 1;
      timerRef.current = setTimeout(connect, delay);
    };

    ws.onerror = () => {
      // ignore — onclose handles reconnect
    };
  }, [session_id, queryClient]);

  useEffect(() => {
    aliveRef.current = true;
    connect();
    return () => {
      aliveRef.current = false;
      if (timerRef.current) clearTimeout(timerRef.current);
      wsRef.current?.close(1000);
    };
  }, [connect]);

  const send = useCallback(
    (text: string) => {
      const trimmed = text.trim();
      if (!trimmed) return;
      setError(null);
      setRagContext(null);
      const userMsg: ChatMessage = {
        client_id: crypto.randomUUID(),
        role: "user",
        content: trimmed,
        finalized: true,
      };
      const assistantStub: ChatMessage = {
        client_id: crypto.randomUUID(),
        role: "assistant",
        content: "",
        finalized: false,
      };
      setMessages((prev) => [...prev, userMsg, assistantStub]);
      setStreaming(true);

      const out: ChatMessageOut = {
        type: "chat.message",
        payload: {
          text: trimmed,
          project_id,
          ...(model_id ? { model_override: model_id } : {}),
        },
      };
      wsRef.current?.send(JSON.stringify(out));
    },
    [project_id, model_id],
  );

  const cancel = useCallback(() => {
    wsRef.current?.close(4000, "client_cancel");
    setStreaming(false);
  }, []);

  return { messages, rag_context, is_streaming, error, send, cancel };
}
```

- [ ] **Step 4: Run tests, confirm they pass**

```
pnpm test src/tests/use-atlas-chat.test.ts
```

Expected: all 5 tests PASS.

- [ ] **Step 5: Commit**

```
git add apps/web/src/hooks/use-atlas-chat.ts apps/web/src/tests/use-atlas-chat.test.ts
git commit -m "feat(atlas-web): useAtlasChat hook — WS, token accumulation, reconnect (TDD)"
```

---

## Task D4: Build chat UI components (Composer, ModelPicker, MessageList, Message)

**Files:**
- Create: `apps/web/src/components/chat/composer.tsx`
- Create: `apps/web/src/components/chat/model-picker.tsx`
- Create: `apps/web/src/components/chat/message-list.tsx`
- Create: `apps/web/src/components/chat/message.tsx`
- Create: `apps/web/src/components/ui/select.tsx` (hand-written shadcn primitive)
- Modify: `apps/web/package.json` (add `@radix-ui/react-select`)

- [ ] **Step 1: Add dep**
```
pnpm add @radix-ui/react-select
```

- [ ] **Step 2: Hand-write `select.tsx`**

```tsx
import * as React from "react";
import * as SelectPrimitive from "@radix-ui/react-select";
import { ChevronDown } from "lucide-react";
import { cn } from "@/lib/cn";

const Select = SelectPrimitive.Root;
const SelectValue = SelectPrimitive.Value;

const SelectTrigger = React.forwardRef<
  React.ElementRef<typeof SelectPrimitive.Trigger>,
  React.ComponentPropsWithoutRef<typeof SelectPrimitive.Trigger>
>(({ className, children, ...props }, ref) => (
  <SelectPrimitive.Trigger
    ref={ref}
    className={cn(
      "flex h-9 w-full items-center justify-between rounded-md border border-input bg-background px-3 py-2 text-sm placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-ring [&>span]:line-clamp-1",
      className,
    )}
    {...props}
  >
    {children}
    <SelectPrimitive.Icon asChild><ChevronDown className="h-4 w-4 opacity-50" /></SelectPrimitive.Icon>
  </SelectPrimitive.Trigger>
));
SelectTrigger.displayName = "SelectTrigger";

const SelectContent = React.forwardRef<
  React.ElementRef<typeof SelectPrimitive.Content>,
  React.ComponentPropsWithoutRef<typeof SelectPrimitive.Content>
>(({ className, children, position = "popper", ...props }, ref) => (
  <SelectPrimitive.Portal>
    <SelectPrimitive.Content
      ref={ref}
      position={position}
      className={cn(
        "relative z-50 max-h-96 min-w-[8rem] overflow-hidden rounded-md border bg-popover text-popover-foreground shadow-md",
        position === "popper" && "data-[side=bottom]:translate-y-1",
        className,
      )}
      {...props}
    >
      <SelectPrimitive.Viewport className="p-1">{children}</SelectPrimitive.Viewport>
    </SelectPrimitive.Content>
  </SelectPrimitive.Portal>
));
SelectContent.displayName = "SelectContent";

const SelectItem = React.forwardRef<
  React.ElementRef<typeof SelectPrimitive.Item>,
  React.ComponentPropsWithoutRef<typeof SelectPrimitive.Item>
>(({ className, children, ...props }, ref) => (
  <SelectPrimitive.Item
    ref={ref}
    className={cn(
      "relative flex w-full cursor-pointer select-none items-center rounded-sm py-1.5 pl-2 pr-2 text-sm outline-none focus:bg-accent focus:text-accent-foreground",
      className,
    )}
    {...props}
  >
    <SelectPrimitive.ItemText>{children}</SelectPrimitive.ItemText>
  </SelectPrimitive.Item>
));
SelectItem.displayName = "SelectItem";

export { Select, SelectTrigger, SelectValue, SelectContent, SelectItem };
```

- [ ] **Step 3: Write `model-picker.tsx`**

```tsx
import { useEffect } from "react";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { useModels } from "@/hooks/use-models";
import { useAtlasStore } from "@/stores/atlas-store";

export function ModelPicker({ session_id, default_model }: { session_id: string; default_model: string }) {
  const { data, isLoading } = useModels();
  const selected = useAtlasStore((s) => s.models.selected_id_per_session[session_id]);
  const setSelected = useAtlasStore((s) => s.setSelectedModel);

  // Default the picker to the project's default_model on first mount.
  useEffect(() => {
    if (!selected && default_model) setSelected(session_id, default_model);
  }, [selected, default_model, session_id, setSelected]);

  if (isLoading) return <div className="text-xs text-muted-foreground">Loading models…</div>;

  return (
    <Select value={selected} onValueChange={(v) => setSelected(session_id, v)}>
      <SelectTrigger className="h-8 w-[260px] text-xs">
        <SelectValue placeholder="Select model" />
      </SelectTrigger>
      <SelectContent>
        {data?.map((m) => (
          <SelectItem key={`${m.provider}:${m.model_id}`} value={m.model_id}>
            {m.provider} — {m.model_id}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  );
}
```

- [ ] **Step 4: Write `composer.tsx`**

```tsx
import { useRef, useState, type KeyboardEvent } from "react";
import { Send, Plus } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { ModelPicker } from "./model-picker";

export function Composer(props: {
  session_id: string;
  default_model: string;
  is_streaming: boolean;
  onSend: (text: string) => void;
  onOpenIngest: () => void;
}) {
  const [text, setText] = useState("");
  const ref = useRef<HTMLTextAreaElement>(null);

  const submit = () => {
    if (!text.trim() || props.is_streaming) return;
    props.onSend(text);
    setText("");
  };
  const onKey = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
      e.preventDefault();
      submit();
    }
  };

  return (
    <div className="border-t bg-background">
      <div className="flex items-center gap-2 px-4 pt-2">
        <ModelPicker session_id={props.session_id} default_model={props.default_model} />
        <Button variant="ghost" size="sm" onClick={props.onOpenIngest}>
          <Plus className="h-4 w-4" />
          Add
        </Button>
      </div>
      <div className="flex items-end gap-2 p-4">
        <Textarea
          ref={ref}
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={onKey}
          placeholder="Ask anything (Cmd/Ctrl+Enter to send)…"
          disabled={props.is_streaming}
          rows={3}
        />
        <Button size="icon" onClick={submit} disabled={!text.trim() || props.is_streaming}>
          <Send className="h-4 w-4" />
        </Button>
      </div>
    </div>
  );
}
```

- [ ] **Step 5: Write `message.tsx` (plain text first; markdown rendering lands in Task E1)**

```tsx
import type { ChatMessage } from "@/hooks/use-atlas-chat";
import { cn } from "@/lib/cn";

export function Message({ msg }: { msg: ChatMessage }) {
  const isUser = msg.role === "user";
  return (
    <div className={cn("flex w-full", isUser ? "justify-end" : "justify-start")}>
      <div
        className={cn(
          "max-w-[80%] whitespace-pre-wrap rounded-lg px-4 py-2 text-sm",
          isUser ? "bg-primary text-primary-foreground" : "bg-muted",
        )}
      >
        {msg.content || (msg.role === "assistant" && !msg.finalized ? "…" : "")}
      </div>
    </div>
  );
}
```

- [ ] **Step 6: Write `message-list.tsx`**

```tsx
import { useEffect, useRef } from "react";
import { Message } from "./message";
import type { ChatMessage } from "@/hooks/use-atlas-chat";

export function MessageList({ messages }: { messages: ChatMessage[] }) {
  const bottomRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages]);

  if (messages.length === 0) {
    return (
      <div className="flex h-full items-center justify-center text-sm text-muted-foreground">
        Start a conversation by typing below.
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-3 p-4">
      {messages.map((m) => (
        <Message key={m.client_id} msg={m} />
      ))}
      <div ref={bottomRef} />
    </div>
  );
}
```

- [ ] **Step 7: `pnpm typecheck`** — passes.

- [ ] **Step 8: Commit**

```
git add apps/web/package.json apps/web/pnpm-lock.yaml apps/web/src/
git commit -m "feat(atlas-web): chat composer, model picker, message list (plain text)"
```

---

## Task D5: Build ChatPanel and wire into the project route

**Files:**
- Create: `apps/web/src/components/chat/chat-panel.tsx`
- Modify: `apps/web/src/routes/project.tsx`

- [ ] **Step 1: Write `chat-panel.tsx`**

```tsx
import { useState } from "react";
import { useProject } from "@/hooks/use-projects";
import { useAtlasChat } from "@/hooks/use-atlas-chat";
import { useAtlasStore } from "@/stores/atlas-store";
import { getOrCreateSessionId } from "@/lib/session-storage";
import { MessageList } from "./message-list";
import { Composer } from "./composer";

export function ChatPanel({ project_id }: { project_id: string }) {
  const { data: project, isLoading } = useProject(project_id);
  const session_id = getOrCreateSessionId(project_id);
  const selected_model = useAtlasStore((s) => s.models.selected_id_per_session[session_id]);
  const [ingestOpen, setIngestOpen] = useState(false);

  const chat = useAtlasChat({
    session_id,
    project_id,
    model_id: selected_model,
  });

  if (isLoading || !project) {
    return <div className="p-6 text-sm text-muted-foreground">Loading…</div>;
  }

  return (
    <div className="flex h-full flex-col">
      <div className="flex h-12 items-center justify-between border-b px-4">
        <div className="font-medium">{project.name}</div>
        {/* RAG drawer toggle button mounts here in Task F1. */}
      </div>
      <div className="flex-1 overflow-y-auto">
        <MessageList messages={chat.messages} />
        {chat.error && (
          <div className="mx-4 mb-4 rounded-md border border-destructive/50 bg-destructive/10 p-3 text-sm text-destructive">
            {chat.error.message}
          </div>
        )}
      </div>
      <Composer
        session_id={session_id}
        default_model={project.default_model}
        is_streaming={chat.is_streaming}
        onSend={chat.send}
        onOpenIngest={() => setIngestOpen(true)}
      />
      {/* IngestModal mounts here in Task G2; for now, the Add button is a no-op. */}
      {ingestOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50" onClick={() => setIngestOpen(false)}>
          <div className="rounded-md bg-background p-6 text-sm">Ingest UI lands in Task G2.</div>
        </div>
      )}
    </div>
  );
}
```

- [ ] **Step 2: Update `routes/project.tsx`**

```tsx
import { useParams } from "react-router-dom";
import { ChatPanel } from "@/components/chat/chat-panel";

export function ProjectRoute() {
  const { id } = useParams<{ id: string }>();
  if (!id) return null;
  return <ChatPanel project_id={id} />;
}
```

- [ ] **Step 3: Smoke-test in the browser**

Backend running, `pnpm dev`, log in to a project, send a message. Tokens should stream into the assistant bubble in real time. Refresh the page — the conversation should reload from the new sessions endpoint. Open a different project — fresh session, separate conversation.

If the `/ws` proxy doesn't work (Vite has occasional WS proxy issues), check that `vite.config.ts` `server.proxy` has `ws: true` for `/ws`.

- [ ] **Step 4: `pnpm typecheck && pnpm lint && pnpm test`** — all pass.

- [ ] **Step 5: Commit**

```
git add apps/web/src/
git commit -m "feat(atlas-web): wire ChatPanel into project route — chat works end-to-end"
```

---

## Task E1: Markdown rendering with shiki code blocks (TDD)

**Files:**
- Create: `apps/web/src/components/chat/markdown/markdown-renderer.tsx`
- Create: `apps/web/src/components/chat/markdown/code-block.tsx`
- Create: `apps/web/src/tests/markdown-renderer.test.tsx`
- Modify: `apps/web/src/components/chat/message.tsx` (use MarkdownRenderer for assistant messages)
- Modify: `apps/web/package.json` (add deps)

- [ ] **Step 1: Add deps**
```
pnpm add react-markdown remark-gfm shiki
```

- [ ] **Step 2: Write the failing test `tests/markdown-renderer.test.tsx`**

```tsx
import { describe, expect, it } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { MarkdownRenderer } from "@/components/chat/markdown/markdown-renderer";

describe("MarkdownRenderer", () => {
  it("renders a fenced code block via CodeBlock", async () => {
    const md = "```ts\nconst x = 1;\n```";
    render(<MarkdownRenderer source={md} />);
    // shiki is async; wait for syntax-highlighted output to appear (any <span>
    // styled by shiki carries an inline color style).
    await waitFor(() => {
      const pre = screen.getByRole("region", { name: /code/i });
      expect(pre).toBeInTheDocument();
    });
  });

  it("renders GFM tables", () => {
    const md = "| h1 | h2 |\n| --- | --- |\n| a | b |";
    render(<MarkdownRenderer source={md} />);
    expect(screen.getByText("h1")).toBeInTheDocument();
    expect(screen.getByText("a")).toBeInTheDocument();
  });

  it("does not execute raw HTML script tags", () => {
    const md = "<script>document.title='pwned';</script>hello";
    const before = document.title;
    render(<MarkdownRenderer source={md} />);
    expect(document.title).toBe(before);
    expect(screen.getByText("hello")).toBeInTheDocument();
  });
});
```

- [ ] **Step 3: Run, confirm fails**
```
pnpm test src/tests/markdown-renderer.test.tsx
```
Expected: import error.

- [ ] **Step 4: Implement `code-block.tsx`**

```tsx
import { useEffect, useState } from "react";
import { codeToHtml } from "shiki";

export function CodeBlock({ code, lang }: { code: string; lang?: string }) {
  const [html, setHtml] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    codeToHtml(code, { lang: lang ?? "text", theme: "github-light" })
      .then((out) => { if (!cancelled) setHtml(out); })
      .catch(() => { if (!cancelled) setHtml(null); });
    return () => { cancelled = true; };
  }, [code, lang]);

  if (html === null) {
    return (
      <pre role="region" aria-label="code" className="rounded-md bg-muted p-3 text-xs overflow-x-auto">
        <code>{code}</code>
      </pre>
    );
  }
  return (
    <div
      role="region"
      aria-label="code"
      className="rounded-md overflow-x-auto text-xs [&>pre]:p-3"
      dangerouslySetInnerHTML={{ __html: html }}
    />
  );
}
```

- [ ] **Step 5: Implement `markdown-renderer.tsx`**

```tsx
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { CodeBlock } from "./code-block";

export function MarkdownRenderer({ source }: { source: string }) {
  return (
    <div className="prose prose-sm max-w-none [&_p]:my-1 [&_pre]:my-2">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        // skipHtml prevents inline <script> etc. from being rendered as HTML.
        skipHtml
        components={{
          code(props) {
            const { children, className, ...rest } = props;
            const match = /language-(\w+)/.exec(className ?? "");
            const text = String(children ?? "").replace(/\n$/, "");
            if (match) return <CodeBlock code={text} lang={match[1]} />;
            return (
              <code className="rounded bg-muted px-1 py-0.5 text-xs" {...rest}>
                {children}
              </code>
            );
          },
        }}
      >
        {source}
      </ReactMarkdown>
    </div>
  );
}
```

- [ ] **Step 6: Update `message.tsx` to use MarkdownRenderer for assistant messages**

```tsx
import type { ChatMessage } from "@/hooks/use-atlas-chat";
import { cn } from "@/lib/cn";
import { MarkdownRenderer } from "./markdown/markdown-renderer";

export function Message({ msg }: { msg: ChatMessage }) {
  const isUser = msg.role === "user";
  const empty = !msg.content && msg.role === "assistant" && !msg.finalized;
  return (
    <div className={cn("flex w-full", isUser ? "justify-end" : "justify-start")}>
      <div
        className={cn(
          "max-w-[80%] rounded-lg px-4 py-2 text-sm",
          isUser ? "bg-primary text-primary-foreground whitespace-pre-wrap" : "bg-muted",
        )}
      >
        {isUser ? msg.content : empty ? <span className="opacity-50">…</span> : <MarkdownRenderer source={msg.content} />}
      </div>
    </div>
  );
}
```

- [ ] **Step 7: Run tests, all pass**
```
pnpm test
```

- [ ] **Step 8: Commit**

```
git add apps/web/package.json apps/web/pnpm-lock.yaml apps/web/src/
git commit -m "feat(atlas-web): markdown rendering + shiki code blocks (TDD)"
```

---

## Task E2: Per-message copy button + ToolUseCard scaffold

**Files:**
- Modify: `apps/web/src/components/chat/message.tsx` (add copy button on hover)
- Create: `apps/web/src/components/chat/tool-use/tool-use-card.tsx`
- Modify: `apps/web/src/components/chat/message.tsx` (render tool_cards inline)

- [ ] **Step 1: Write `tool-use-card.tsx`**

```tsx
import { useState } from "react";
import { ChevronRight } from "lucide-react";
import type { ToolCard } from "@/hooks/use-atlas-chat";
import { cn } from "@/lib/cn";

export function ToolUseCard({ card }: { card: ToolCard }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="my-2 rounded-md border bg-background text-xs">
      <button
        className="flex w-full items-center gap-1 px-2 py-1.5 text-left hover:bg-accent"
        onClick={() => setOpen((v) => !v)}
      >
        <ChevronRight className={cn("h-3 w-3 transition-transform", open && "rotate-90")} />
        <span className="font-medium">🔧 {card.name ?? "tool call"}</span>
      </button>
      {open && (
        <div className="border-t p-2 font-mono text-[11px] text-muted-foreground">
          <div className="mb-1">arguments:</div>
          <pre className="whitespace-pre-wrap">{JSON.stringify(card.arguments ?? {}, null, 2)}</pre>
          {card.result !== undefined && (
            <>
              <div className="mt-2 mb-1">result:</div>
              <pre className="whitespace-pre-wrap">{JSON.stringify(card.result, null, 2)}</pre>
            </>
          )}
        </div>
      )}
    </div>
  );
}
```

- [ ] **Step 2: Update `message.tsx` to render copy button + tool cards**

```tsx
import { useState } from "react";
import { Copy, Check } from "lucide-react";
import type { ChatMessage } from "@/hooks/use-atlas-chat";
import { cn } from "@/lib/cn";
import { MarkdownRenderer } from "./markdown/markdown-renderer";
import { ToolUseCard } from "./tool-use/tool-use-card";

export function Message({ msg }: { msg: ChatMessage }) {
  const isUser = msg.role === "user";
  const empty = !msg.content && msg.role === "assistant" && !msg.finalized;
  const [copied, setCopied] = useState(false);

  const onCopy = async () => {
    try {
      await navigator.clipboard.writeText(msg.content);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      // ignore — older browsers w/o clipboard API
    }
  };

  return (
    <div className={cn("group flex w-full", isUser ? "justify-end" : "justify-start")}>
      <div
        className={cn(
          "relative max-w-[80%] rounded-lg px-4 py-2 text-sm",
          isUser ? "bg-primary text-primary-foreground whitespace-pre-wrap" : "bg-muted",
        )}
      >
        {isUser ? (
          msg.content
        ) : empty ? (
          <span className="opacity-50">…</span>
        ) : (
          <>
            <MarkdownRenderer source={msg.content} />
            {msg.tool_cards?.map((c) => <ToolUseCard key={c.id} card={c} />)}
          </>
        )}
        {msg.content && (
          <button
            onClick={onCopy}
            className={cn(
              "absolute -top-2 right-2 rounded-md border bg-background p-1 opacity-0 group-hover:opacity-100 transition",
            )}
            aria-label="Copy message"
          >
            {copied ? <Check className="h-3 w-3" /> : <Copy className="h-3 w-3" />}
          </button>
        )}
      </div>
    </div>
  );
}
```

- [ ] **Step 3: Smoke-test**

Send a message, hover the assistant bubble, click copy. Should copy to clipboard and show a checkmark for ~1.5s.

- [ ] **Step 4: Commit**

```
git add apps/web/src/components/chat/
git commit -m "feat(atlas-web): per-message copy button + ToolUseCard scaffold"
```

---

## Task F1: RAG drawer with auto-open + citation cards (TDD)

**Files:**
- Create: `apps/web/src/components/rag/rag-drawer.tsx`
- Create: `apps/web/src/components/rag/citation-card.tsx`
- Create: `apps/web/src/tests/rag-drawer.test.tsx`
- Create: `apps/web/src/components/ui/sheet.tsx` (hand-written shadcn primitive)
- Modify: `apps/web/src/components/chat/chat-panel.tsx` (mount drawer + auto-open + toggle button)
- Modify: `apps/web/package.json` (no new dep — Dialog primitive doubles as Sheet)

- [ ] **Step 1: Hand-write `sheet.tsx`** (a side-anchored Dialog)

```tsx
import * as React from "react";
import * as SheetPrimitive from "@radix-ui/react-dialog";
import { X } from "lucide-react";
import { cn } from "@/lib/cn";

const Sheet = SheetPrimitive.Root;
const SheetTrigger = SheetPrimitive.Trigger;
const SheetClose = SheetPrimitive.Close;

const SheetOverlay = React.forwardRef<
  React.ElementRef<typeof SheetPrimitive.Overlay>,
  React.ComponentPropsWithoutRef<typeof SheetPrimitive.Overlay>
>(({ className, ...props }, ref) => (
  <SheetPrimitive.Overlay ref={ref} className={cn("fixed inset-0 z-50 bg-black/50", className)} {...props} />
));
SheetOverlay.displayName = "SheetOverlay";

const SheetContent = React.forwardRef<
  React.ElementRef<typeof SheetPrimitive.Content>,
  React.ComponentPropsWithoutRef<typeof SheetPrimitive.Content>
>(({ className, children, ...props }, ref) => (
  <SheetPrimitive.Portal>
    <SheetOverlay />
    <SheetPrimitive.Content
      ref={ref}
      className={cn(
        "fixed inset-y-0 right-0 z-50 flex h-full w-full max-w-md flex-col gap-4 border-l bg-background p-6 shadow-lg",
        className,
      )}
      {...props}
    >
      <SheetPrimitive.Close className="absolute right-4 top-4 rounded-sm opacity-70 hover:opacity-100">
        <X className="h-4 w-4" />
      </SheetPrimitive.Close>
      {children}
    </SheetPrimitive.Content>
  </SheetPrimitive.Portal>
));
SheetContent.displayName = "SheetContent";

const SheetHeader = ({ className, ...props }: React.HTMLAttributes<HTMLDivElement>) => (
  <div className={cn("flex flex-col gap-1.5", className)} {...props} />
);
const SheetTitle = React.forwardRef<
  React.ElementRef<typeof SheetPrimitive.Title>,
  React.ComponentPropsWithoutRef<typeof SheetPrimitive.Title>
>(({ className, ...props }, ref) => (
  <SheetPrimitive.Title ref={ref} className={cn("text-lg font-semibold", className)} {...props} />
));
SheetTitle.displayName = "SheetTitle";

export { Sheet, SheetTrigger, SheetClose, SheetContent, SheetHeader, SheetTitle };
```

- [ ] **Step 2: Write the failing test `tests/rag-drawer.test.tsx`**

```tsx
import { describe, expect, it } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { RagDrawer } from "@/components/rag/rag-drawer";
import type { Citation } from "@/lib/ws-protocol";

const cites: Citation[] = [
  { id: 1, title: "Source A", score: 0.91, chunk_id: "a1", text_preview: "alpha preview text" },
  { id: 2, title: "Source B", score: 0.72, chunk_id: "b2", text_preview: "beta preview text" },
];

describe("RagDrawer", () => {
  it("renders empty state when no citations", () => {
    render(<RagDrawer open citations={null} onOpenChange={() => {}} />);
    expect(screen.getByText(/no sources/i)).toBeInTheDocument();
  });

  it("renders one card per citation with title, score, and preview", () => {
    render(<RagDrawer open citations={cites} onOpenChange={() => {}} />);
    expect(screen.getByText("Source A")).toBeInTheDocument();
    expect(screen.getByText("Source B")).toBeInTheDocument();
    expect(screen.getByText(/0\.91/)).toBeInTheDocument();
    expect(screen.getByText(/alpha preview/)).toBeInTheDocument();
  });

  it("expands chunk preview on click", () => {
    render(<RagDrawer open citations={cites} onOpenChange={() => {}} />);
    const card = screen.getByText("Source A").closest("[data-citation-card]")!;
    fireEvent.click(card);
    expect(card).toHaveAttribute("data-expanded", "true");
  });
});
```

- [ ] **Step 3: Run, confirm fails**

```
pnpm test src/tests/rag-drawer.test.tsx
```

- [ ] **Step 4: Implement `citation-card.tsx`**

```tsx
import { useState } from "react";
import type { Citation } from "@/lib/ws-protocol";
import { cn } from "@/lib/cn";

export function CitationCard({ cite }: { cite: Citation }) {
  const [expanded, setExpanded] = useState(false);
  return (
    <div
      data-citation-card
      data-expanded={expanded}
      className={cn(
        "cursor-pointer rounded-md border bg-card p-3 hover:bg-accent",
        expanded && "bg-accent",
      )}
      onClick={() => setExpanded((v) => !v)}
    >
      <div className="flex items-baseline justify-between gap-2">
        <div className="font-medium text-sm">{cite.title}</div>
        <div className="font-mono text-xs text-muted-foreground">{cite.score.toFixed(2)}</div>
      </div>
      <div className={cn("mt-1 text-xs text-muted-foreground", expanded ? "" : "line-clamp-3")}>
        {cite.text_preview}
      </div>
    </div>
  );
}
```

- [ ] **Step 5: Implement `rag-drawer.tsx`**

```tsx
import { Sheet, SheetContent, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import { CitationCard } from "./citation-card";
import type { Citation } from "@/lib/ws-protocol";

export function RagDrawer(props: {
  open: boolean;
  citations: Citation[] | null;
  onOpenChange: (open: boolean) => void;
}) {
  return (
    <Sheet open={props.open} onOpenChange={props.onOpenChange}>
      <SheetContent>
        <SheetHeader>
          <SheetTitle>Sources</SheetTitle>
        </SheetHeader>
        {!props.citations || props.citations.length === 0 ? (
          <div className="text-sm text-muted-foreground">
            No sources used yet. Upload knowledge to get RAG-grounded answers.
          </div>
        ) : (
          <div className="flex flex-col gap-2 overflow-y-auto">
            {props.citations.map((c) => (
              <CitationCard key={c.id} cite={c} />
            ))}
          </div>
        )}
      </SheetContent>
    </Sheet>
  );
}
```

- [ ] **Step 6: Wire into ChatPanel — auto-open on first `rag.context` per session**

Edit `chat-panel.tsx`. Add the RAG drawer + toggle button:

```tsx
import { useEffect } from "react";
import { Library } from "lucide-react";
import { Button } from "@/components/ui/button";
import { RagDrawer } from "@/components/rag/rag-drawer";

// inside ChatPanel(...):
const ragOpen = useAtlasStore((s) => s.ui.rag_drawer_open);
const setRagOpen = useAtlasStore((s) => s.setRagDrawerOpen);
const autoOpened = useAtlasStore((s) => s.ui.rag_drawer_auto_opened_for_session[session_id]);
const markAutoOpened = useAtlasStore((s) => s.markRagDrawerAutoOpened);

useEffect(() => {
  if (chat.rag_context && chat.rag_context.length > 0 && !autoOpened) {
    setRagOpen(true);
    markAutoOpened(session_id);
  }
}, [chat.rag_context, autoOpened, session_id, setRagOpen, markAutoOpened]);

// In the header replace the placeholder with:
<Button variant="ghost" size="sm" onClick={() => setRagOpen(!ragOpen)}>
  <Library className="h-4 w-4" />
  Sources {chat.rag_context ? `(${chat.rag_context.length})` : ""}
</Button>

// At the bottom of the JSX, mount:
<RagDrawer open={ragOpen} citations={chat.rag_context} onOpenChange={setRagOpen} />
```

- [ ] **Step 7: Run all tests** → pass.

- [ ] **Step 8: Smoke-test**

Ingest some markdown into a project (use `curl` for now since IngestModal is Task G2: `curl -X POST localhost:8000/api/v1/knowledge/ingest -H 'content-type: application/json' -d '{"project_id":"<id>","source_type":"markdown","text":"ATLAS uses pgvector. Embeddings live in Chroma."}'`). Send a chat message that triggers retrieval ("How does ATLAS store embeddings?"). The drawer should auto-open with at least one citation card.

- [ ] **Step 9: Commit**

```
git add apps/web/src/
git commit -m "feat(atlas-web): RAG drawer with auto-open + citation cards (TDD)"
```

---

## Task G1: Add `useStartIngest` and `useIngestJob` hooks

**Files:**
- Create: `apps/web/src/hooks/use-ingest-job.ts`

- [ ] **Step 1: Write the file**

```ts
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";

export type IngestionStatus = "pending" | "running" | "completed" | "failed";

export type IngestionJob = {
  id: string;
  user_id: string;
  project_id: string;
  source_type: "markdown" | "pdf";
  source_filename: string | null;
  status: IngestionStatus;
  node_ids: string[];
  error: string | null;
  created_at: string;
  completed_at: string | null;
};

export function useStartMarkdownIngest() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: { project_id: string; text: string; source_filename?: string }) =>
      api.post<IngestionJob>("/api/v1/knowledge/ingest", {
        project_id: body.project_id,
        source_type: "markdown",
        text: body.text,
        source_filename: body.source_filename,
      }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["ingestion-jobs"] }),
  });
}

export function useStartPdfIngest() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (body: { project_id: string; file: File }) => {
      const form = new FormData();
      form.append("project_id", body.project_id);
      form.append("file", body.file);
      return api.postForm<IngestionJob>("/api/v1/knowledge/ingest/pdf", form);
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ["ingestion-jobs"] }),
  });
}

export function useIngestJob(job_id: string | undefined) {
  return useQuery({
    queryKey: ["ingestion-jobs", job_id],
    queryFn: () => api.get<IngestionJob>(`/api/v1/knowledge/jobs/${job_id}`),
    enabled: Boolean(job_id),
    refetchInterval: (query) => {
      const status = query.state.data?.status;
      if (status === "completed" || status === "failed") return false;
      return 1000;
    },
  });
}
```

- [ ] **Step 2: Commit**

```
git add apps/web/src/hooks/use-ingest-job.ts
git commit -m "feat(atlas-web): ingest mutation hooks + polling job query"
```

---

## Task G2: IngestModal with PDF + markdown tabs (TDD)

**Files:**
- Create: `apps/web/src/components/ingest/ingest-modal.tsx`
- Create: `apps/web/src/components/ui/tabs.tsx` (hand-written shadcn primitive)
- Create: `apps/web/src/tests/ingest-modal.test.tsx`
- Modify: `apps/web/src/components/chat/chat-panel.tsx` (replace placeholder modal)
- Modify: `apps/web/package.json` (add `@radix-ui/react-tabs`)

- [ ] **Step 1: Add dep**
```
pnpm add @radix-ui/react-tabs
```

- [ ] **Step 2: Hand-write `tabs.tsx`**

```tsx
import * as React from "react";
import * as TabsPrimitive from "@radix-ui/react-tabs";
import { cn } from "@/lib/cn";

const Tabs = TabsPrimitive.Root;

const TabsList = React.forwardRef<
  React.ElementRef<typeof TabsPrimitive.List>,
  React.ComponentPropsWithoutRef<typeof TabsPrimitive.List>
>(({ className, ...props }, ref) => (
  <TabsPrimitive.List
    ref={ref}
    className={cn("inline-flex h-9 items-center justify-center rounded-md bg-muted p-1 text-muted-foreground", className)}
    {...props}
  />
));
TabsList.displayName = "TabsList";

const TabsTrigger = React.forwardRef<
  React.ElementRef<typeof TabsPrimitive.Trigger>,
  React.ComponentPropsWithoutRef<typeof TabsPrimitive.Trigger>
>(({ className, ...props }, ref) => (
  <TabsPrimitive.Trigger
    ref={ref}
    className={cn(
      "inline-flex items-center justify-center whitespace-nowrap rounded-sm px-3 py-1 text-sm font-medium ring-offset-background transition-all focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring data-[state=active]:bg-background data-[state=active]:text-foreground data-[state=active]:shadow-sm",
      className,
    )}
    {...props}
  />
));
TabsTrigger.displayName = "TabsTrigger";

const TabsContent = React.forwardRef<
  React.ElementRef<typeof TabsPrimitive.Content>,
  React.ComponentPropsWithoutRef<typeof TabsPrimitive.Content>
>(({ className, ...props }, ref) => (
  <TabsPrimitive.Content ref={ref} className={cn("mt-4 focus-visible:outline-none", className)} {...props} />
));
TabsContent.displayName = "TabsContent";

export { Tabs, TabsList, TabsTrigger, TabsContent };
```

- [ ] **Step 3: Write the failing test `tests/ingest-modal.test.tsx`**

```tsx
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { IngestModal } from "@/components/ingest/ingest-modal";

const queries: ReturnType<typeof vi.fn>[] = [];

beforeEach(() => {
  queries.length = 0;
  globalThis.fetch = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    queries.push(vi.fn(() => ({ url, init })));
    if (url.includes("/api/v1/knowledge/ingest") && !url.includes("/pdf")) {
      return new Response(
        JSON.stringify({
          id: "job-1", user_id: "matt", project_id: "p", source_type: "markdown",
          source_filename: null, status: "pending", node_ids: [], error: null,
          created_at: new Date().toISOString(), completed_at: null,
        }),
        { status: 202, headers: { "content-type": "application/json" } },
      );
    }
    if (url.includes("/api/v1/knowledge/jobs/")) {
      return new Response(
        JSON.stringify({
          id: "job-1", user_id: "matt", project_id: "p", source_type: "markdown",
          source_filename: null, status: "completed", node_ids: ["n1", "n2", "n3"], error: null,
          created_at: new Date().toISOString(), completed_at: new Date().toISOString(),
        }),
        { status: 200, headers: { "content-type": "application/json" } },
      );
    }
    return new Response("not mocked", { status: 500 });
  }) as unknown as typeof fetch;
});

afterEach(() => { vi.restoreAllMocks(); });

const wrapper = ({ children }: { children: ReactNode }) => {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return <QueryClientProvider client={qc}>{children}</QueryClientProvider>;
};

describe("IngestModal", () => {
  it("submits markdown and shows completion", async () => {
    const user = userEvent.setup();
    render(<IngestModal open onOpenChange={() => {}} project_id="p" />, { wrapper });

    await user.type(screen.getByLabelText(/markdown/i), "# hello");
    await user.click(screen.getByRole("button", { name: /ingest/i }));

    await waitFor(() => expect(screen.getByText(/ingested 3 chunks/i)).toBeInTheDocument());
  });

  it("preserves form state across tabs", async () => {
    const user = userEvent.setup();
    render(<IngestModal open onOpenChange={() => {}} project_id="p" />, { wrapper });

    await user.type(screen.getByLabelText(/markdown/i), "# hello");

    fireEvent.click(screen.getByRole("tab", { name: /pdf/i }));
    fireEvent.click(screen.getByRole("tab", { name: /markdown/i }));

    expect(screen.getByLabelText(/markdown/i)).toHaveValue("# hello");
  });
});
```

- [ ] **Step 4: Run, confirm fails**
```
pnpm test src/tests/ingest-modal.test.tsx
```

- [ ] **Step 5: Implement `ingest-modal.tsx`**

```tsx
import { useState } from "react";
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { Input } from "@/components/ui/input";
import {
  useStartMarkdownIngest,
  useStartPdfIngest,
  useIngestJob,
  type IngestionJob,
} from "@/hooks/use-ingest-job";

export function IngestModal(props: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  project_id: string;
}) {
  const [tab, setTab] = useState<"markdown" | "pdf">("markdown");
  const [markdown, setMarkdown] = useState("");
  const [filename, setFilename] = useState("");
  const [pdfFile, setPdfFile] = useState<File | null>(null);
  const [activeJob, setActiveJob] = useState<IngestionJob | null>(null);

  const startMd = useStartMarkdownIngest();
  const startPdf = useStartPdfIngest();
  const polled = useIngestJob(activeJob?.id);
  const job = polled.data ?? activeJob;

  const submit = async () => {
    if (tab === "markdown") {
      if (!markdown.trim()) return;
      const j = await startMd.mutateAsync({
        project_id: props.project_id,
        text: markdown,
        source_filename: filename.trim() || undefined,
      });
      setActiveJob(j);
    } else {
      if (!pdfFile) return;
      const j = await startPdf.mutateAsync({ project_id: props.project_id, file: pdfFile });
      setActiveJob(j);
    }
  };

  const reset = () => {
    setActiveJob(null);
    setMarkdown("");
    setFilename("");
    setPdfFile(null);
    setTab("markdown");
  };

  const close = () => {
    reset();
    props.onOpenChange(false);
  };

  return (
    <Dialog
      open={props.open}
      onOpenChange={(o) => {
        if (!o) reset();
        props.onOpenChange(o);
      }}
    >
      <DialogContent>
        <DialogHeader><DialogTitle>Add knowledge</DialogTitle></DialogHeader>

        {!job && (
          <Tabs value={tab} onValueChange={(v) => setTab(v as typeof tab)}>
            <TabsList>
              <TabsTrigger value="markdown">Markdown</TabsTrigger>
              <TabsTrigger value="pdf">PDF</TabsTrigger>
            </TabsList>
            <TabsContent value="markdown">
              <div className="space-y-3">
                <div className="space-y-1.5">
                  <Label htmlFor="md-title">Title (optional)</Label>
                  <Input
                    id="md-title"
                    value={filename}
                    onChange={(e) => setFilename(e.target.value)}
                    placeholder="Geo-lift design notes"
                  />
                </div>
                <div className="space-y-1.5">
                  <Label htmlFor="md-text">Markdown</Label>
                  <Textarea
                    id="md-text"
                    value={markdown}
                    onChange={(e) => setMarkdown(e.target.value)}
                    rows={10}
                  />
                </div>
              </div>
            </TabsContent>
            <TabsContent value="pdf">
              <div className="space-y-3">
                <Label htmlFor="pdf-file">PDF file</Label>
                <Input
                  id="pdf-file"
                  type="file"
                  accept="application/pdf"
                  onChange={(e) => setPdfFile(e.target.files?.[0] ?? null)}
                />
              </div>
            </TabsContent>
            <div className="mt-4 flex justify-end gap-2">
              <Button variant="ghost" onClick={() => props.onOpenChange(false)}>Cancel</Button>
              <Button onClick={submit} disabled={startMd.isPending || startPdf.isPending}>
                Ingest
              </Button>
            </div>
          </Tabs>
        )}

        {job && job.status !== "completed" && job.status !== "failed" && (
          <div className="space-y-2 py-4 text-sm">
            <div>Ingesting… ({job.status})</div>
            <div className="text-muted-foreground text-xs">
              You can close this modal — the job continues in the background.
            </div>
            <Button variant="ghost" onClick={close}>Close</Button>
          </div>
        )}
        {job?.status === "completed" && (
          <div className="space-y-3 py-4">
            <div className="text-sm">Ingested {job.node_ids.length} chunks.</div>
            <div className="flex justify-end gap-2">
              <Button variant="ghost" onClick={reset}>Add another</Button>
              <Button onClick={close}>Done</Button>
            </div>
          </div>
        )}
        {job?.status === "failed" && (
          <div className="space-y-3 py-4">
            <div className="text-sm text-destructive">{job.error ?? "Ingestion failed."}</div>
            <div className="flex justify-end gap-2">
              <Button variant="ghost" onClick={close}>Cancel</Button>
              <Button onClick={reset}>Retry</Button>
            </div>
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}
```

- [ ] **Step 6: Replace the placeholder modal in `chat-panel.tsx`**

Replace the temporary `if (ingestOpen) ...` block with:

```tsx
<IngestModal open={ingestOpen} onOpenChange={setIngestOpen} project_id={project_id} />
```

(And `import { IngestModal } from "@/components/ingest/ingest-modal";`)

- [ ] **Step 7: Run all tests → pass**

```
pnpm test
```

- [ ] **Step 8: Smoke-test**

Open the modal from the Composer's "+ Add" button. Paste markdown, click Ingest. The "Ingested N chunks" view should appear in <2s. Then upload a PDF; same flow. Trigger a chat search to verify the new content is discoverable.

- [ ] **Step 9: Commit**

```
git add apps/web/package.json apps/web/pnpm-lock.yaml apps/web/src/
git commit -m "feat(atlas-web): IngestModal with markdown + PDF tabs (TDD)"
```

---

## Task H1: Add `apps/api/Dockerfile`

**Files:**
- Create: `apps/api/Dockerfile`
- Create: `apps/api/.dockerignore`

The api Dockerfile installs uv, syncs the workspace, and runs uvicorn.

- [ ] **Step 1: Verify whether one already exists**

```
ls apps/api/Dockerfile 2>/dev/null
```

If it exists, skip ahead to step 4 (just confirm it works in the compose stack). If not, continue.

- [ ] **Step 2: Create `apps/api/Dockerfile`**

```dockerfile
# syntax=docker/dockerfile:1.7
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS base

ENV UV_LINK_MODE=copy \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Copy the workspace metadata first to maximize Docker layer cache reuse.
COPY pyproject.toml uv.lock ./
COPY apps/api/pyproject.toml apps/api/
COPY packages/atlas-core/pyproject.toml packages/atlas-core/
COPY packages/atlas-knowledge/pyproject.toml packages/atlas-knowledge/

# Sync without dev deps; the workspace resolves all three packages together.
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev

# Copy source after deps so changes don't bust the cache.
COPY apps/api/atlas_api apps/api/atlas_api
COPY packages/atlas-core/atlas_core packages/atlas-core/atlas_core
COPY packages/atlas-knowledge/atlas_knowledge packages/atlas-knowledge/atlas_knowledge

ENV PATH="/app/.venv/bin:$PATH"

EXPOSE 8000
CMD ["uvicorn", "atlas_api.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

- [ ] **Step 3: Create `apps/api/.dockerignore`**

```
**/__pycache__
**/.pytest_cache
**/.mypy_cache
**/.ruff_cache
**/*.pyc
.git
.venv
node_modules
dist
build
```

- [ ] **Step 4: Smoke-build**

```
docker build -f apps/api/Dockerfile -t atlas-api:dev .
```

Expected: builds successfully. The full compose stack runs in Task H3.

- [ ] **Step 5: Commit**

```
git add apps/api/Dockerfile apps/api/.dockerignore
git commit -m "feat(infra): add atlas-api Dockerfile (uv-based, multi-package workspace)"
```

---

## Task H2: Add web Dockerfile + nginx config

**Files:**
- Create: `apps/web/Dockerfile`
- Create: `apps/web/nginx.conf`
- Create: `apps/web/.dockerignore`

Multi-stage build: node:20-alpine builds, nginx:alpine serves with `/api` and `/ws` proxied to the api service.

- [ ] **Step 1: Create `apps/web/Dockerfile`**

```dockerfile
# syntax=docker/dockerfile:1.7
FROM node:20-alpine AS build

WORKDIR /app

# Install pnpm
RUN corepack enable && corepack prepare pnpm@9.12.0 --activate

# Manifest first for cache reuse.
COPY package.json pnpm-lock.yaml* ./
RUN pnpm install --frozen-lockfile

COPY . .
RUN pnpm build

FROM nginx:alpine AS runtime

COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf

EXPOSE 80
CMD ["nginx", "-g", "daemon off;"]
```

- [ ] **Step 2: Create `apps/web/nginx.conf`**

```
server {
    listen 80;
    server_name _;

    root /usr/share/nginx/html;
    index index.html;

    # SPA fallback — let React Router handle unknown paths.
    location / {
        try_files $uri /index.html;
    }

    # API proxy.
    location /api/ {
        proxy_pass http://api:8000/api/;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }

    # WebSocket proxy.
    location /api/v1/ws/ {
        proxy_pass http://api:8000/api/v1/ws/;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "Upgrade";
        proxy_set_header Host $host;
        proxy_read_timeout 3600s;
    }
}
```

- [ ] **Step 3: Create `apps/web/.dockerignore`**

```
node_modules
dist
.vite
.cache
coverage
**/*.log
.git
```

- [ ] **Step 4: Smoke-build**

```
docker build -f apps/web/Dockerfile -t atlas-web:dev apps/web
```

Expected: builds successfully.

- [ ] **Step 5: Commit**

```
git add apps/web/Dockerfile apps/web/nginx.conf apps/web/.dockerignore
git commit -m "feat(atlas-web): Dockerfile + nginx config with /api and /ws proxy"
```

---

## Task H3: Add `infra/docker-compose.yml` and root `.env.example`

**Files:**
- Create: `infra/docker-compose.yml`
- Create: `.env.example` (repo root)
- Create: `infra/postgres/init.sql` (only if it doesn't already exist — verify first)

- [ ] **Step 1: Verify postgres init.sql state**

```
ls infra/postgres/init.sql 2>/dev/null
```

If present, leave it. If absent, the schema lives in alembic — the init script can be empty or just `CREATE EXTENSION IF NOT EXISTS pgcrypto;` if `gen_random_uuid()` isn't already provisioned by the alembic migrations. Inspect the existing alembic migrations under `infra/alembic/versions/` to confirm the schema is created on app startup, then either:
- Skip creating `init.sql` entirely (alembic handles it), or
- Create a minimal `init.sql` that runs only extensions:

  ```sql
  CREATE EXTENSION IF NOT EXISTS pgcrypto;
  ```

Pick the path that matches what the existing app expects — check `apps/api/atlas_api/main.py` for any startup migration logic.

- [ ] **Step 2: Create `.env.example` at the repo root**

```
# === LLM providers ===
ANTHROPIC_API_KEY=
LM_STUDIO_BASE_URL=http://host.docker.internal:1234/v1

# === Database ===
POSTGRES_USER=atlas
POSTGRES_PASSWORD=atlas
POSTGRES_DB=atlas
DATABASE_URL=postgresql+asyncpg://atlas:atlas@postgres:5432/atlas

# === Redis ===
REDIS_URL=redis://redis:6379/0

# === Single-user identity ===
USER_ID=matt
```

- [ ] **Step 3: Create `infra/docker-compose.yml`**

```yaml
name: atlas

services:
  postgres:
    image: postgres:16-alpine
    environment:
      POSTGRES_USER: ${POSTGRES_USER}
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD}
      POSTGRES_DB: ${POSTGRES_DB}
    volumes:
      - postgres_data:/var/lib/postgresql/data
      - ./postgres:/docker-entrypoint-initdb.d:ro
    ports:
      - "5432:5432"
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U $${POSTGRES_USER}"]
      interval: 5s
      timeout: 3s
      retries: 10

  redis:
    image: redis:7-alpine
    ports:
      - "6379:6379"
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 5s
      timeout: 3s
      retries: 10

  api:
    build:
      context: ..
      dockerfile: apps/api/Dockerfile
    env_file: ../.env
    environment:
      DATABASE_URL: ${DATABASE_URL}
      REDIS_URL: ${REDIS_URL}
    extra_hosts:
      - "host.docker.internal:host-gateway"
    depends_on:
      postgres:
        condition: service_healthy
      redis:
        condition: service_healthy
    ports:
      - "8000:8000"

  web:
    build:
      context: ../apps/web
      dockerfile: Dockerfile
    depends_on:
      - api
    ports:
      - "3000:80"

volumes:
  postgres_data: {}
```

- [ ] **Step 4: Smoke-test the stack**

From the repo root:
```
cp .env.example .env
# fill in ANTHROPIC_API_KEY in .env
cd infra
docker compose up --build
```

Expected: postgres becomes healthy, redis becomes healthy, api starts, web starts. Open `http://localhost:3000` — the app loads. Create a project and chat through the docker-compose stack.

`Ctrl+C`, `docker compose down -v` to tear down.

- [ ] **Step 5: Commit**

```
git add infra/docker-compose.yml .env.example infra/postgres/
git commit -m "feat(infra): docker-compose stack — postgres + redis + api + web"
```

---

## Task H4: README quickstart, foundation spec edits, and DoD walkthrough

**Files:**
- Modify: `README.md`
- Modify: `docs/superpowers/specs/2026-04-26-atlas-phase-1-foundation-design.md`

- [ ] **Step 1: Update README**

If `README.md` exists, replace or augment its quickstart section. If it doesn't, create one at the repo root with at minimum:

```markdown
# ATLAS

Self-hosted, AI-native consulting OS. Phase 1 is feature-complete; this README documents the local-dev and docker-compose paths.

## Quickstart (docker-compose)

1. Clone the repo.
2. `cp .env.example .env` and fill in `ANTHROPIC_API_KEY`.
3. `cd infra && docker compose up --build`.
4. Open `http://localhost:3000`.

The first build takes 2–4 minutes.

## Quickstart (local dev)

```
# Terminal 1 — backend
uv sync
uv run uvicorn atlas_api.main:app --reload --port 8000

# Terminal 2 — frontend
cd apps/web
pnpm install
pnpm dev   # http://localhost:5173
```

The Vite dev server proxies `/api` and `/ws` to `localhost:8000`.

## Tests

```
uv run pytest                # backend
cd apps/web && pnpm test     # frontend
```

## Repo layout

- `apps/api/` — FastAPI backend (uvicorn entrypoint at `atlas_api.main:app`).
- `apps/web/` — React 19 + Vite + TS frontend.
- `packages/atlas-core/` — shared Pydantic models, ORM, providers.
- `packages/atlas-knowledge/` — embeddings, vector store, ingestion, retrieval.
- `infra/` — docker-compose, alembic migrations, postgres init.
- `docs/` — design docs, plans, specs.
```

If a README already exists with content beyond a stub, edit only the Quickstart section to point users at the docker-compose path.

- [ ] **Step 2: Update foundation spec inline**

Edit `docs/superpowers/specs/2026-04-26-atlas-phase-1-foundation-design.md`:

- §13 file layout: change `frontend/` (the top-level entry) to `apps/web/`. Reorder so it appears under `apps/`.
- §8 Frontend, "Layout" subsection: where it says "settings/model dropdown at bottom", append a sentence: "(Plan 6 placed the model picker above the chat input bar instead — per-session selection feels more natural near the action.)"

These are minor edits — keep them surgical.

- [ ] **Step 3: Run the full DoD walkthrough**

From a clean clone of the repo (or after `git stash; cd ..; rm -rf checkout-test; git clone ...; cd checkout-test`):

1. `cp .env.example .env`, fill in `ANTHROPIC_API_KEY`.
2. `cd infra && docker compose up --build`. All four services come up.
3. Open `http://localhost:3000`. Sidebar visible.
4. Click "+ New Project". Name "Smoke Test", default model `claude-sonnet-4-6`. Submit. Project appears in sidebar; URL changes to `/projects/<id>`.
5. Open "+ Add" modal. Paste markdown ("ATLAS uses pgvector for embeddings."). Click Ingest. Wait for "Ingested N chunks". Close.
6. Open "+ Add" again, switch to PDF tab, upload a small PDF. Wait for "Ingested N chunks". Close.
7. Confirm both via curl: `curl 'localhost:8000/api/v1/knowledge/search?project_id=<id>&query=embeddings'`. Expect non-empty `chunks`.
8. Send a chat message ("How does ATLAS store embeddings?"). Tokens stream. Sources drawer auto-opens with at least one citation card showing the markdown source.
9. Refresh the page. The conversation rehydrates.
10. In the model picker, switch to an LM Studio model (whichever is listed). Send another message. Tokens stream from the local provider.
11. From the repo root: `uv run pytest` — all pass.
12. From `apps/web/`: `pnpm test && pnpm lint && pnpm typecheck && pnpm build` — all pass.

If any step fails, debug and commit the fix before declaring DoD done.

- [ ] **Step 4: Commit doc updates**

```
git add README.md docs/superpowers/specs/2026-04-26-atlas-phase-1-foundation-design.md
git commit -m "docs: README quickstart + foundation spec frontend-path/model-picker amendments"
```

- [ ] **Step 5: Push the branch and open the PR**

```
git push -u origin feat/phase-1-plan-6-react-frontend
gh pr create --title "feat: Phase 1 Plan 6 — React frontend + docker-compose stack" --body "$(cat <<'EOF'
## Summary
- Adds the Vite + React 19 + TS + Tailwind v4 + shadcn/ui frontend at \`apps/web/\` with project sidebar, streaming chat, markdown + shiki rendering, RAG drawer with auto-open + citation cards, and an ingest modal (PDF + markdown tabs).
- Adds \`GET /api/v1/sessions/{session_id}/messages\` so the frontend can rehydrate per-project conversations from \`localStorage\`-stored session ids.
- Extends \`build_rag_context\` citations with \`text_preview\` so the RAG drawer can render previews without an extra round-trip.
- Adds an \`infra/docker-compose.yml\` stack (postgres + redis + api + web) and Dockerfiles for api and web; root \`.env.example\` and README quickstart make first-run a clean \`cp .env.example .env && docker compose up\`.
- Closes Phase 1 Definition of Done.

## Test plan
- [x] \`uv run pytest\` — all backend tests pass (existing + 4 new sessions tests + 1 new builder test).
- [x] \`pnpm test && pnpm lint && pnpm typecheck && pnpm build\` in \`apps/web/\` — all pass.
- [x] Full DoD walkthrough on a clean clone via \`docker compose up\`.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

---

## Self-Review

Spec coverage:
- §1–§3 architecture, module layout, boundaries → Tasks B1–B5, plus the tasks that fill in components.
- §4 state and data flow (zustand + React Query + useAtlasChat + WS event mapping) → Tasks B5, C1, D2, D3.
- §5 routes and key UI surfaces → Tasks C2–C4, D4–D5, E1–E2, F1, G2.
- §6 backend additions (sessions endpoint, no `MessageRead` because `Message` already exists) → Task A1.
- §6.4 no CORS changes → confirmed in Task H3 (compose puts api + web behind one nginx).
- §7 docker, dev experience, tooling → Tasks B1–B3, H1–H3.
- §8 testing strategy (5 frontend tests + 4 backend tests) → tests are colocated with the tasks (B4, D3, E1, F1, G2 for frontend; A1 for backend).
- §9 build order → matches the Phase A0 → A1 → B1–B5 → C1–C4 → D1–D5 → E1–E2 → F1 → G1–G2 → H1–H4 sequence.
- §10 DoD → Task H4 step 3.
- §11 risks → Tailwind v4 fallback noted in Task B2; shiki bundle size mitigated by lazy-loading inside CodeBlock (Task E1); reconnect trailer specified in `useAtlasChat` (Task D3); `host.docker.internal` mapped via `extra_hosts` for Linux compatibility in Task H3.

Plus one item the spec didn't explicitly enumerate but the implementation surfaced: extending `build_rag_context` with `text_preview` (Task A0). Without this, the RAG drawer's "preview" UX in §5.5 would be impossible without an extra round-trip per citation. Adding it as Task A0 is right because the spec assumed previews were available.

Placeholder scan: no `TBD`, no `TODO`, no "implement later", no "similar to Task N" — code is duplicated where the implementer might land out of order.

Type consistency:
- `Citation` is defined in `lib/ws-protocol.ts` once and reused by `useAtlasChat`, `RagDrawer`, `CitationCard`.
- `ChatMessage` (with `client_id`, `role`, `content`, `tool_cards`, `finalized`) is defined in `use-atlas-chat.ts` and consumed by `Message`, `MessageList`.
- `Project`, `IngestionJob`, `ModelSpec`, `SessionMessage` types in hook files match the backend Pydantic schemas exactly (verified via `grep` during planning).
- `crypto.randomUUID()` is used in three places (`session-storage.ts`, `useAtlasChat.send`, the tool-use handler) — all browser-supported.

No fixes needed.
