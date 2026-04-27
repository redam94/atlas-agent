# ATLAS Phase 1 — Plan 5: Wire RAG into Chat WebSocket

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Implements:** `docs/superpowers/specs/2026-04-27-atlas-phase-1-plan-5-rag-in-chat-design.md`.

**Goal:** Working RAG-augmented chat. With knowledge ingested via Plan 4, a `chat.message` WS event triggers retrieval, emits a `rag.context` event with citations, injects the retrieved chunks as a second system message, and persists citations on the assistant `MessageORM` row.

**Architecture:** One pure renderer (`build_rag_context(scored_chunks) -> RagContext`) added to `atlas_knowledge.retrieval`. `chat_ws` in `atlas-api` gains a `Retriever` dependency, calls retrieval between history-load and prompt-assembly, emits the event before the first token, and inserts the rendered XML block as a second `system` message between persona and history. Empty results / `rag_enabled=false` skip everything cleanly.

**Tech Stack:** existing — `atlas_knowledge.Retriever` (Plan 4) · `httpx_ws` test transport (Plan 3) · `FakeProvider` + `FakeEmbedder` for test isolation. No new dependencies.

---

## File Structure

```
atlas-agent/
├── apps/api/atlas_api/
│   ├── ws/
│   │   └── chat.py                                    # MODIFIED (Retriever dep,
│   │                                                  #          retrieval step,
│   │                                                  #          rag.context event,
│   │                                                  #          context message injection,
│   │                                                  #          assistant rag_context persist)
│   └── tests/
│       └── test_ws_chat_rag.py                        # NEW (3 WS integration tests)
└── packages/atlas-knowledge/atlas_knowledge/
    ├── retrieval/
    │   ├── __init__.py                                # MODIFIED (re-export build_rag_context)
    │   ├── builder.py                                 # NEW (build_rag_context + XML template)
    │   └── retriever.py                               # unchanged
    └── tests/
        └── test_retrieval_builder.py                  # NEW (3 unit tests)
```

**Responsibility per new module:**

- `retrieval/builder.py` — `build_rag_context(scored: list[ScoredChunk]) -> RagContext`. Pure function: no I/O, no async, no DB. Renders the XML prompt block from §3 of the spec and the parallel `citations` list. Handles XML escaping of titles + chunk text. Empty input → empty `RagContext`.
- `routers/knowledge.py` — *unchanged*. Plan 4 already exposes ingestion/search.
- `ws/chat.py` — gains a single new dependency (`Retriever`), one new helper that wraps the retrieval+emit+inject sequence, and one assignment on `assistant_row.rag_context`.

---

## Task 1: Add `build_rag_context` renderer (TDD)

**Files:**
- Create: `packages/atlas-knowledge/atlas_knowledge/retrieval/builder.py`
- Create: `packages/atlas-knowledge/atlas_knowledge/tests/test_retrieval_builder.py`
- Modify: `packages/atlas-knowledge/atlas_knowledge/retrieval/__init__.py`

- [ ] **Step 1: Write the failing tests**

`packages/atlas-knowledge/atlas_knowledge/tests/test_retrieval_builder.py`:

```python
"""Tests for build_rag_context — pure renderer, no I/O."""
from datetime import UTC, datetime
from uuid import uuid4

from atlas_knowledge.models.nodes import KnowledgeNode, KnowledgeNodeType
from atlas_knowledge.models.retrieval import ScoredChunk
from atlas_knowledge.retrieval.builder import build_rag_context


def _scored(text: str, *, title: str | None = "Doc", parent_title: str | None = None, score: float = 0.8) -> ScoredChunk:
    chunk = KnowledgeNode(
        id=uuid4(),
        user_id="matt",
        project_id=uuid4(),
        type=KnowledgeNodeType.CHUNK,
        parent_id=uuid4(),
        title=title,
        text=text,
        created_at=datetime.now(UTC),
    )
    return ScoredChunk(chunk=chunk, score=score, parent_title=parent_title)


def test_build_rag_context_empty_input():
    ctx = build_rag_context([])
    assert ctx.rendered == ""
    assert ctx.citations == []


def test_build_rag_context_single_chunk():
    sc = _scored("hello world", title="Notes", score=0.91)
    ctx = build_rag_context([sc])
    assert "<source id=\"1\" title=\"Notes\">hello world</source>" in ctx.rendered
    assert ctx.rendered.startswith("<context>")
    assert "</context>" in ctx.rendered
    assert "Cite as [1]" in ctx.rendered
    assert len(ctx.citations) == 1
    cite = ctx.citations[0]
    assert cite["id"] == 1
    assert cite["title"] == "Notes"
    assert cite["score"] == 0.91
    assert cite["chunk_id"] == str(sc.chunk.id)


def test_build_rag_context_prefers_parent_title_over_chunk_title():
    sc = _scored("body", title="chunk-only", parent_title="Parent Doc")
    ctx = build_rag_context([sc])
    assert "title=\"Parent Doc\"" in ctx.rendered
    assert ctx.citations[0]["title"] == "Parent Doc"


def test_build_rag_context_falls_back_to_untitled():
    sc = _scored("body", title=None, parent_title=None)
    ctx = build_rag_context([sc])
    assert "title=\"Untitled\"" in ctx.rendered
    assert ctx.citations[0]["title"] == "Untitled"


def test_build_rag_context_xml_escapes_title_and_text():
    sc = _scored("a < b & c > d", title="Title <evil> & \"quoted\"")
    ctx = build_rag_context([sc])
    # rendered side: escaped
    assert "Title &lt;evil&gt; &amp; &quot;quoted&quot;" in ctx.rendered
    assert "a &lt; b &amp; c &gt; d" in ctx.rendered
    assert "<evil>" not in ctx.rendered
    # citations side: raw, since JSON does its own escaping
    assert ctx.citations[0]["title"] == "Title <evil> & \"quoted\""


def test_build_rag_context_assigns_contiguous_one_indexed_ids():
    chunks = [_scored(f"chunk {i}", title=f"T{i}", score=0.5 - i * 0.01) for i in range(3)]
    ctx = build_rag_context(chunks)
    ids = [c["id"] for c in ctx.citations]
    assert ids == [1, 2, 3]
    # rendered side has matching id attrs
    for i in (1, 2, 3):
        assert f"<source id=\"{i}\"" in ctx.rendered
```

Run: `uv run pytest packages/atlas-knowledge/atlas_knowledge/tests/test_retrieval_builder.py -v`
Expected: ImportError on `build_rag_context`.

- [ ] **Step 2: Implement `builder.py`**

`packages/atlas-knowledge/atlas_knowledge/retrieval/builder.py`:

```python
"""Render a list of retrieved ScoredChunks into a RagContext (prompt block + citations).

The rendered block is the text injected as a separate ``system`` message in the
LLM prompt. The citations list mirrors the rendered IDs and is what flows over
the ``rag.context`` WS event and persists in ``MessageORM.rag_context`` on the
assistant row.
"""
from __future__ import annotations

from xml.sax.saxutils import escape as xml_escape

from atlas_knowledge.models.retrieval import RagContext, ScoredChunk

_PROMPT_FOOTER = "\n\nUse the sources above to answer when relevant. Cite as [1], [2]."


def build_rag_context(scored: list[ScoredChunk]) -> RagContext:
    """Render scored chunks into a RagContext.

    Empty input → ``RagContext(rendered="", citations=[])`` so the WS handler
    can use a single truthiness check (``if rag_ctx.citations``) before emitting
    the event or injecting the prompt block.
    """
    if not scored:
        return RagContext(rendered="", citations=[])

    rendered_sources: list[str] = []
    citations: list[dict] = []
    for idx, sc in enumerate(scored, start=1):
        title = sc.parent_title or sc.chunk.title or "Untitled"
        # XML-escape both title (attribute) and text (element body).
        # `quotes=True` so `"` becomes `&quot;` inside the title attribute.
        rendered_sources.append(
            f'<source id="{idx}" title="{xml_escape(title, {chr(34): "&quot;"})}">'
            f"{xml_escape(sc.chunk.text)}"
            f"</source>"
        )
        citations.append(
            {
                "id": idx,
                "title": title,            # raw — JSON serialization handles its own escaping
                "score": sc.score,
                "chunk_id": str(sc.chunk.id),
            }
        )

    rendered = "<context>\n" + "\n".join(rendered_sources) + "\n</context>" + _PROMPT_FOOTER
    return RagContext(rendered=rendered, citations=citations)
```

(`xml.sax.saxutils.escape` handles `&`, `<`, `>` by default. The extra dict `{chr(34): "&quot;"}` adds the double-quote escape needed inside attribute values. `chr(34)` is `"` — written this way to avoid quoting headaches inside the f-string.)

- [ ] **Step 3: Update `retrieval/__init__.py`**

Replace contents:

```python
"""Retrieval pipeline."""

from atlas_knowledge.retrieval.builder import build_rag_context
from atlas_knowledge.retrieval.retriever import Retriever

__all__ = ["Retriever", "build_rag_context"]
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest packages/atlas-knowledge/atlas_knowledge/tests/test_retrieval_builder.py -v`
Expected: 6 passed.

- [ ] **Step 5: Commit**

```bash
git add packages/atlas-knowledge/atlas_knowledge/retrieval/builder.py \
        packages/atlas-knowledge/atlas_knowledge/retrieval/__init__.py \
        packages/atlas-knowledge/atlas_knowledge/tests/test_retrieval_builder.py
git commit -m "feat(atlas-knowledge): add build_rag_context renderer (XML prompt block + citations)"
```

---

## Task 2: Wire `Retriever` + retrieval step into the chat WS handler

**Files:**
- Modify: `apps/api/atlas_api/ws/chat.py`

The current handler structure (line numbers approximate, from main as of merge of Plan 4):
- Lines 100–134: `_handle_chat_message` — resolves project, ensures session, loads history, builds system prompt, assembles messages, persists user, routes provider.
- Lines 247–256: `_assemble_messages(system_prompt, history, new_user_text)` — builds the list passed to the provider.
- Line 22: imports `StreamEventType` (already includes `RAG_CONTEXT`).
- Line 31: imports from `atlas_api.deps` (already includes `get_session`, `get_settings`, `get_model_router`).

You will:
1. Add imports for `Retriever`, `RetrievalQuery`, `build_rag_context`, `get_retriever`.
2. Add a `Retriever = Depends(get_retriever)` parameter to the `chat_ws` function and thread it through to `_handle_chat_message`.
3. Add a retrieval block right after history-load and before user-message persistence.
4. Modify `_assemble_messages` to accept an optional `rag_block: str | None`.
5. Set `assistant_row.rag_context = citations` (the list) when retrieval ran.

- [ ] **Step 1: Add new imports near existing atlas-knowledge imports... wait, there are none yet in chat.py. Add a fresh block.**

In `apps/api/atlas_api/ws/chat.py`, locate the block of imports (around lines 18–31). Add these lines (alphabetized inside their group):

```python
from atlas_knowledge.models.retrieval import RetrievalQuery
from atlas_knowledge.retrieval.builder import build_rag_context
from atlas_knowledge.retrieval.retriever import Retriever
```

And update the existing `from atlas_api.deps import ...` to include `get_retriever`:

```python
from atlas_api.deps import get_model_router, get_retriever, get_session, get_settings
```

- [ ] **Step 2: Add `Retriever` parameter to `chat_ws`**

Modify the `chat_ws` signature (around line 41–47) from:

```python
@router.websocket("/ws/{session_id}")
async def chat_ws(
    websocket: WebSocket,
    session_id: UUID,
    db: AsyncSession = Depends(get_session),
    model_router: ModelRouter = Depends(get_model_router),
    settings: AtlasConfig = Depends(get_settings),
) -> None:
```

to:

```python
@router.websocket("/ws/{session_id}")
async def chat_ws(
    websocket: WebSocket,
    session_id: UUID,
    db: AsyncSession = Depends(get_session),
    model_router: ModelRouter = Depends(get_model_router),
    retriever: Retriever = Depends(get_retriever),
    settings: AtlasConfig = Depends(get_settings),
) -> None:
```

Then update the inner call to `_handle_chat_message` (around line 85–87) — add `retriever` to the args:

```python
sequence = await _handle_chat_message(
    websocket, session_id, req, db, model_router, retriever, settings, sequence
)
```

- [ ] **Step 3: Update `_handle_chat_message` signature**

Modify the function signature (around line 100–108) from:

```python
async def _handle_chat_message(
    websocket: WebSocket,
    session_id: UUID,
    req: ChatRequest,
    db: AsyncSession,
    model_router: ModelRouter,
    settings: AtlasConfig,
    sequence: int,
) -> int:
```

to:

```python
async def _handle_chat_message(
    websocket: WebSocket,
    session_id: UUID,
    req: ChatRequest,
    db: AsyncSession,
    model_router: ModelRouter,
    retriever: Retriever,
    settings: AtlasConfig,
    sequence: int,
) -> int:
```

- [ ] **Step 4: Add the retrieval block**

In `_handle_chat_message`, after the existing history-load (around line 132 — `history_rows = await _load_recent_messages(...)`) AND before `system_prompt = prompt_builder.build(...)`, insert:

```python
    # 3b. Optionally retrieve RAG context. Skipped if rag_enabled=false or
    # if the knowledge base for this project has no relevant chunks.
    rag_block: str | None = None
    rag_citations: list[dict] | None = None
    if req.rag_enabled:
        rag_result = await retriever.retrieve(
            RetrievalQuery(
                project_id=project.id,
                text=req.text,
                top_k=req.top_k_context,
            )
        )
        if rag_result.chunks:
            rag_ctx = build_rag_context(rag_result.chunks)
            rag_block = rag_ctx.rendered
            rag_citations = rag_ctx.citations
            sequence = await _send(
                websocket,
                StreamEventType.RAG_CONTEXT,
                {"citations": rag_citations},
                sequence,
            )
```

- [ ] **Step 5: Pass `rag_block` into message assembly**

Modify the call to `_assemble_messages` (around line 134) from:

```python
    model_messages = _assemble_messages(system_prompt, history_rows, req.text)
```

to:

```python
    model_messages = _assemble_messages(system_prompt, history_rows, req.text, rag_block=rag_block)
```

- [ ] **Step 6: Update `_assemble_messages` to accept the rag block**

Modify `_assemble_messages` (around lines 247–256) from:

```python
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
```

to:

```python
def _assemble_messages(
    system_prompt: str,
    history: list[MessageORM],
    new_user_text: str,
    *,
    rag_block: str | None = None,
) -> list[dict]:
    out: list[dict] = [{"role": "system", "content": system_prompt}]
    if rag_block:
        # Second system message — keeps the persona prompt (cache-friendly,
        # stable across turns) separate from the per-turn retrieved context.
        out.append({"role": "system", "content": rag_block})
    for row in history:
        out.append({"role": row.role, "content": row.content})
    out.append({"role": "user", "content": new_user_text})
    return out
```

- [ ] **Step 7: Persist `rag_context` on the assistant row**

In `_handle_chat_message`, find where `assistant_row` is constructed (around line 185–192) and add the `rag_context` field:

```python
    assistant_row = MessageORM(
        user_id=settings.user_id,
        session_id=session_id,
        role=MessageRole.ASSISTANT.value,
        content=full_assistant_text,
        rag_context=rag_citations,
        model=provider.spec.model_id,
        token_count=(usage or {}).get("output_tokens"),
    )
```

(`rag_citations` is `None` when retrieval was skipped or returned 0 chunks. The DB column is nullable JSONB, so `None` round-trips fine.)

- [ ] **Step 8: Smoke-import the modified module**

Run:
```bash
uv run python -c "from atlas_api.ws import chat; print('ok')"
```
Expected: `ok`. (Catches typos / missing imports before running tests.)

- [ ] **Step 9: Patch existing WS test fixture to override `get_retriever`**

The existing `set_overrides` fixture in `apps/api/atlas_api/tests/test_ws_chat.py` overrides `get_session`, `get_model_router`, `get_settings` but not `get_retriever`. After Step 4 wires retrieval into the handler with `rag_enabled=True` defaulting on, every existing WS test will hit `get_retriever`, and since httpx-ws's `ASGIWebSocketTransport` doesn't run the lifespan, `app.state.retriever` won't exist → AttributeError.

Fix the fixture in-place. Modify `apps/api/atlas_api/tests/test_ws_chat.py`:

1. Add imports near the existing atlas-knowledge-adjacent imports (after the `FakeProvider` line):

```python
from atlas_knowledge.embeddings import FakeEmbedder
from atlas_knowledge.retrieval.retriever import Retriever
from atlas_knowledge.vector.chroma import ChromaVectorStore
```

2. Add `get_retriever` to the existing `from atlas_api.deps import ...` line — it should read:

```python
from atlas_api.deps import get_model_router, get_retriever, get_session, get_settings
```

3. Modify the `set_overrides` fixture signature and body. From:

```python
@pytest_asyncio.fixture
async def set_overrides(db_session, fake_router):
    ...
    app.dependency_overrides[get_session] = _override_session
    app.dependency_overrides[get_model_router] = _override_router
    app.dependency_overrides[get_settings] = _override_settings

    yield fake_router

    app.dependency_overrides.clear()
```

to:

```python
@pytest_asyncio.fixture
async def set_overrides(db_session, fake_router, tmp_path):
    ...
    fake_retriever = Retriever(
        embedder=FakeEmbedder(dim=16),
        vector_store=ChromaVectorStore(persist_dir=str(tmp_path / "chroma"), user_id="matt"),
    )

    app.dependency_overrides[get_session] = _override_session
    app.dependency_overrides[get_model_router] = _override_router
    app.dependency_overrides[get_settings] = _override_settings
    app.dependency_overrides[get_retriever] = lambda: fake_retriever

    yield fake_router

    app.dependency_overrides.clear()
```

(`tmp_path` is a built-in pytest fixture so just adding it as a parameter is enough. With no chunks seeded, retrieval returns empty → RAG path is silent → existing tests behave exactly as before.)

- [ ] **Step 10: Run existing WS tests for regression**

```bash
uv run pytest apps/api/atlas_api/tests/test_ws_chat.py -v 2>&1 | tail -20
```
Expected: all existing tests pass. They don't seed chunks, retrieval returns 0, the RAG path stays silent — observable behavior matches Plan 4.

- [ ] **Step 11: Commit**

```bash
git add apps/api/atlas_api/ws/chat.py apps/api/atlas_api/tests/test_ws_chat.py
git commit -m "feat(atlas-api): wire Retriever + rag.context event into chat WS"
```

---

## Task 3: Integration tests — RAG path through the WS

**Files:**
- Create: `apps/api/atlas_api/tests/test_ws_chat_rag.py`

These tests use the established `httpx_ws.aconnect_ws` + `ASGIWebSocketTransport` pattern from `test_ws_chat.py`. They override:
- `get_model_router` → `FakeProvider` returning fixed tokens (so we don't need a real LLM)
- `get_retriever` → `Retriever(FakeEmbedder, ChromaVectorStore(tmp_path))` with chunks pre-seeded directly via `vector_store.upsert(...)` (faster than going through `IngestionService`, and the WS handler doesn't care about Postgres `KnowledgeNodeORM` rows — only the vector store)

- [ ] **Step 1: Write the test file**

`apps/api/atlas_api/tests/test_ws_chat_rag.py`:

```python
"""Integration tests for the RAG path in the chat WebSocket.

Uses FakeEmbedder + tmp Chroma + FakeProvider to exercise the full retrieve →
emit `rag.context` → inject context message → persist citations flow without
touching a real LLM or BGE-small.
"""
from __future__ import annotations

import contextlib
from datetime import UTC, datetime
from uuid import UUID, uuid4

import httpx
import pytest
import pytest_asyncio
from atlas_core.config import AtlasConfig
from atlas_core.db.orm import MessageORM, ProjectORM, SessionORM
from atlas_core.providers import FakeProvider
from atlas_core.providers.registry import ModelRegistry, ModelRouter
from atlas_knowledge.embeddings import FakeEmbedder
from atlas_knowledge.models.nodes import KnowledgeNode, KnowledgeNodeType
from atlas_knowledge.retrieval.retriever import Retriever
from atlas_knowledge.vector.chroma import ChromaVectorStore
from httpx_ws import aconnect_ws
from httpx_ws.transport import ASGIWebSocketTransport
from sqlalchemy import select

from atlas_api.deps import get_model_router, get_retriever, get_session, get_settings
from atlas_api.main import app


# --- fixtures ------------------------------------------------------------


@pytest.fixture
def fake_router():
    fp = FakeProvider(model_id="fake-1", token_chunks=["alpha", " ", "beta"])
    reg = ModelRegistry()
    reg.register(fp)
    return ModelRouter(reg)


@pytest.fixture
def fake_settings():
    return AtlasConfig()  # default user_id="matt", default lmstudio settings unused


@pytest.fixture
def vector_store(tmp_path):
    return ChromaVectorStore(persist_dir=str(tmp_path / "chroma"), user_id="matt")


@pytest.fixture
def fake_embedder():
    return FakeEmbedder(dim=16)


@pytest.fixture
def retriever(vector_store, fake_embedder):
    return Retriever(embedder=fake_embedder, vector_store=vector_store)


@pytest_asyncio.fixture
async def set_overrides(db_session, fake_router, fake_settings, retriever):
    """Wire dependency overrides for this test. db_session is the same per-test
    AsyncSession, and the lifespan-built retriever is replaced by the
    FakeEmbedder-backed one."""

    async def _override_session():
        yield db_session

    app.dependency_overrides[get_session] = _override_session
    app.dependency_overrides[get_model_router] = lambda: fake_router
    app.dependency_overrides[get_settings] = lambda: fake_settings
    app.dependency_overrides[get_retriever] = lambda: retriever
    yield
    for dep in (get_session, get_model_router, get_settings, get_retriever):
        app.dependency_overrides.pop(dep, None)


async def _seed_project_and_chunks(
    db_session,
    vector_store: ChromaVectorStore,
    fake_embedder: FakeEmbedder,
    project_id: UUID,
    texts: list[str],
) -> list[KnowledgeNode]:
    project = ProjectORM(
        id=project_id,
        user_id="matt",
        name="P5",
        default_model="claude-sonnet-4-6",
    )
    db_session.add(project)
    await db_session.flush()

    parent_id = uuid4()
    chunks = [
        KnowledgeNode(
            id=uuid4(),
            user_id="matt",
            project_id=project_id,
            type=KnowledgeNodeType.CHUNK,
            parent_id=parent_id,
            title=f"Source {i}",
            text=text,
            created_at=datetime.now(UTC),
        )
        for i, text in enumerate(texts)
    ]
    embeddings = await fake_embedder.embed_documents(texts)
    await vector_store.upsert(chunks, embeddings)
    return chunks


@contextlib.asynccontextmanager
async def _ws_client():
    """Open a WS to the running ASGI app via httpx-ws + ASGI transport."""
    transport = ASGIWebSocketTransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as http:
        yield http


async def _drain_events_until_done(ws):
    """Receive events until chat.done. Returns the list of events."""
    events: list[dict] = []
    while True:
        evt = await ws.receive_json()
        events.append(evt)
        if evt["type"] == "chat.done":
            return events


# --- tests ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_rag_happy_path_emits_event_and_persists_citations(
    set_overrides, db_session, vector_store, fake_embedder
):
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
    async with _ws_client() as http:
        async with aconnect_ws(f"/api/v1/ws/{session_id}", http) as ws:
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
    # The rag.context event must arrive before the first chat.token.
    assert "rag.context" in types
    rag_idx = types.index("rag.context")
    first_token_idx = types.index("chat.token")
    assert rag_idx < first_token_idx

    # Citations payload structure
    rag_evt = events[rag_idx]
    citations = rag_evt["payload"]["citations"]
    assert len(citations) == 2
    for cite in citations:
        assert set(cite.keys()) >= {"id", "title", "score", "chunk_id"}
        assert cite["chunk_id"] in chunk_ids
    # IDs are 1-indexed contiguous
    assert sorted(c["id"] for c in citations) == [1, 2]

    # Assistant row persists the same citations as JSONB
    rows = (
        await db_session.execute(
            select(MessageORM).where(MessageORM.session_id == session_id)
        )
    ).scalars().all()
    assistant = next(r for r in rows if r.role == "assistant")
    assert assistant.rag_context is not None
    assert len(assistant.rag_context) == 2
    persisted_ids = sorted(c["chunk_id"] for c in assistant.rag_context)
    emitted_ids = sorted(c["chunk_id"] for c in citations)
    assert persisted_ids == emitted_ids


@pytest.mark.asyncio
async def test_rag_disabled_skips_event_and_persists_null(
    set_overrides, db_session, vector_store, fake_embedder
):
    project_id = uuid4()
    await _seed_project_and_chunks(
        db_session,
        vector_store,
        fake_embedder,
        project_id,
        texts=["should be ignored"],
    )

    session_id = uuid4()
    async with _ws_client() as http:
        async with aconnect_ws(f"/api/v1/ws/{session_id}", http) as ws:
            await ws.send_json(
                {
                    "type": "chat.message",
                    "payload": {
                        "text": "hello",
                        "project_id": str(project_id),
                        "rag_enabled": False,
                    },
                }
            )
            events = await _drain_events_until_done(ws)

    types = [e["type"] for e in events]
    assert "rag.context" not in types

    rows = (
        await db_session.execute(
            select(MessageORM).where(MessageORM.session_id == session_id)
        )
    ).scalars().all()
    assistant = next(r for r in rows if r.role == "assistant")
    assert assistant.rag_context is None


@pytest.mark.asyncio
async def test_empty_knowledge_base_skips_event(set_overrides, db_session):
    project_id = uuid4()
    project = ProjectORM(
        id=project_id, user_id="matt", name="P5-empty", default_model="claude-sonnet-4-6"
    )
    db_session.add(project)
    await db_session.flush()

    session_id = uuid4()
    async with _ws_client() as http:
        async with aconnect_ws(f"/api/v1/ws/{session_id}", http) as ws:
            await ws.send_json(
                {
                    "type": "chat.message",
                    "payload": {
                        "text": "anything",
                        "project_id": str(project_id),
                        "rag_enabled": True,
                    },
                }
            )
            events = await _drain_events_until_done(ws)

    types = [e["type"] for e in events]
    assert "rag.context" not in types
    assert "chat.token" in types  # tokens still stream

    rows = (
        await db_session.execute(
            select(MessageORM).where(MessageORM.session_id == session_id)
        )
    ).scalars().all()
    assistant = next(r for r in rows if r.role == "assistant")
    assert assistant.rag_context is None
```

- [ ] **Step 2: Run the new tests**

```bash
uv run pytest apps/api/atlas_api/tests/test_ws_chat_rag.py -v 2>&1 | tail -25
```
Expected: 3 passed.

If a test fails, the most likely culprits:
- **`rag.context` arriving AFTER `chat.token`**: the retrieval block is in the wrong place in `_handle_chat_message`. Move it to before provider streaming starts.
- **Citations not on assistant row**: the `assistant_row = MessageORM(...)` constructor doesn't pass `rag_context=rag_citations`. Re-check Task 2 Step 7.
- **`Retriever` dep not overridden**: confirm `app.dependency_overrides[get_retriever]` is set in `set_overrides` and flushed in cleanup.

- [ ] **Step 3: Run the full suite for regression**

```bash
uv run pytest -q 2>&1 | tail -5
```
Expected: 169 passed (160 from Plan 4 + 6 builder unit + 3 WS-RAG = 169).

- [ ] **Step 4: Commit**

```bash
git add apps/api/atlas_api/tests/test_ws_chat_rag.py
git commit -m "test(atlas-api): cover RAG-enabled / disabled / empty-KB paths through chat WS"
```

---

## Task 4: Lint + format + final review

**Files:** none modified (verification only).

- [ ] **Step 1: Ruff**

```bash
uv run ruff check . && uv run ruff format --check .
```
Expected: clean. If not, run `uv run ruff check --fix . && uv run ruff format .`, then commit `chore: ruff autofix and format`.

- [ ] **Step 2: Route enumeration sanity check**

```bash
ATLAS_DB__DATABASE_URL=postgresql://atlas:atlas@localhost:5432/atlas \
  uv run python -c "from atlas_api.main import app; print(sorted({r.path for r in app.routes if r.path.startswith('/api')}))"
```
Expected: same routes as Plan 4 — adding RAG to the WS handler does NOT add new routes. The list should still contain the 6 knowledge endpoints, the WS path, the models endpoint, and the projects endpoints.

- [ ] **Step 3: Live smoke (manual; Matt runs)**

Start the API:
```bash
uv run uvicorn atlas_api.main:app --host 127.0.0.1 --port 8000
```

In another shell — first ingest some content:
```bash
PROJECT_ID=$(curl -s -X POST http://127.0.0.1:8000/api/v1/projects \
  -H 'Content-Type: application/json' \
  -d '{"name":"RAG Smoke","default_model":"claude-sonnet-4-6"}' \
  | python3 -c "import sys,json;print(json.load(sys.stdin)['id'])")

curl -s -X POST http://127.0.0.1:8000/api/v1/knowledge/ingest \
  -H 'Content-Type: application/json' \
  -d "{\"project_id\":\"$PROJECT_ID\",\"source_type\":\"markdown\",\"text\":\"# ATLAS Notes\\n\\nATLAS uses BGE-small-en-v1.5 embeddings via sentence-transformers, stores them in ChromaDB embedded mode, and chunks with a paragraph-aware sliding window targeting 512 tokens with 128 token overlap.\"}"
```

Then open a WS chat (using `wscat` or `websocat`):
```bash
SESSION_ID=$(uuidgen | tr '[:upper:]' '[:lower:]')
wscat -c "ws://127.0.0.1:8000/api/v1/ws/$SESSION_ID"
```

Inside the WS, paste the JSON message:
```json
{"type":"chat.message","payload":{"text":"What does ATLAS use for embeddings and chunking?","project_id":"<PROJECT_ID>","rag_enabled":true}}
```
(Replace `<PROJECT_ID>` with the value from above.)

Expected event order:
1. `rag.context` event with `citations` containing at least one entry pointing to the ingested chunk.
2. `chat.token` events streaming the answer (which should reference BGE-small / Chroma / 512 tokens).
3. `chat.done`.

- [ ] **Step 4: Verify persistence**

```bash
docker exec atlas-postgres psql -U atlas -d atlas -c "SELECT id, role, LEFT(content, 60) AS preview, rag_context FROM messages ORDER BY created_at DESC LIMIT 4;"
```
Expected: the assistant row's `rag_context` column contains a JSONB array with the citations from the WS event (`id`, `title`, `score`, `chunk_id`).

- [ ] **Step 5: Cleanup smoke data**

```bash
docker exec atlas-postgres psql -U atlas -d atlas -c "DELETE FROM messages WHERE session_id IN (SELECT id FROM sessions WHERE project_id IN (SELECT id FROM projects WHERE name='RAG Smoke'));"
docker exec atlas-postgres psql -U atlas -d atlas -c "DELETE FROM sessions WHERE project_id IN (SELECT id FROM projects WHERE name='RAG Smoke');"
docker exec atlas-postgres psql -U atlas -d atlas -c "DELETE FROM knowledge_nodes WHERE project_id IN (SELECT id FROM projects WHERE name='RAG Smoke');"
docker exec atlas-postgres psql -U atlas -d atlas -c "DELETE FROM ingestion_jobs WHERE project_id IN (SELECT id FROM projects WHERE name='RAG Smoke');"
docker exec atlas-postgres psql -U atlas -d atlas -c "DELETE FROM projects WHERE name='RAG Smoke';"
```

Stop the API (Ctrl-C).

---

## Definition of Done

1. `uv run pytest -q` passes — Plan 4's 160 + 6 builder + 3 WS-RAG = 169.
2. `uv run ruff check .` and `ruff format --check .` clean.
3. `build_rag_context` exists in `atlas_knowledge.retrieval.builder` and is re-exported from `atlas_knowledge.retrieval`.
4. `chat_ws` accepts `Retriever` via `Depends(get_retriever)`.
5. With `rag_enabled=true` and chunks present: `rag.context` event fires before the first `chat.token`; citations payload has `id`/`title`/`score`/`chunk_id`; second system message with the rendered XML block is sent to the LLM; assistant `MessageORM.rag_context` JSONB matches the emitted citations.
6. With `rag_enabled=false`: no retrieval, no event, no context message, `assistant.rag_context` is `None`.
7. With empty knowledge base: no event, normal token stream, `assistant.rag_context` is `None`.

When all DoD items pass, this plan is complete. Plan 6 (React frontend) follows.
