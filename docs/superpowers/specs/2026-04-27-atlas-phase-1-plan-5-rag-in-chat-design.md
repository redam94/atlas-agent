# ATLAS Phase 1 — Plan 5: Wire RAG into Chat WebSocket — Design

**Status:** approved 2026-04-27
**Implements:** `docs/superpowers/specs/2026-04-26-atlas-phase-1-foundation-design.md` §6 (RAG flow), §8 (chat WS protocol — the `rag.context` event), §10 (`messages.rag_context` column).

**Predecessors:** Plan 3 (chat WS without RAG, MERGED PR #3), Plan 4 (knowledge layer: ingestion + retrieval, MERGED PR #4).

## Goal

A working RAG-augmented chat. With knowledge ingested (Plan 4 endpoints), a `chat.message` WS event triggers retrieval, emits a `rag.context` event with citations, injects the chunks into the LLM prompt, and persists the citations on the assistant message row.

## Scaffolding already in place

- `ChatRequest.rag_enabled: bool = True` and `ChatRequest.top_k_context: int = 8` — both unused by Plan 3, honored by Plan 5.
- `StreamEventType.RAG_CONTEXT = "rag.context"` enum value.
- `MessageORM.rag_context` JSONB column.
- `Retriever` (Plan 4) with `retrieve(RetrievalQuery) -> RetrievalResult`.
- `RagContext` Pydantic model with `rendered: str` and `citations: list[dict]`.
- `app.state.retriever` populated in lifespan + `get_retriever` dependency.

## §1 Architecture

Plan 5 adds one pure function (`build_rag_context`) and modifies the chat WS handler. No new files in atlas-core. No new endpoints.

```
packages/atlas-knowledge/atlas_knowledge/retrieval/
├── builder.py                     # NEW — build_rag_context() + XML template
└── retriever.py                   # unchanged

packages/atlas-knowledge/atlas_knowledge/tests/
└── test_retrieval_builder.py      # NEW

apps/api/atlas_api/ws/chat.py      # MODIFIED — Retriever dep + retrieval step
                                   #            + rag.context event emit
                                   #            + context message injection
                                   #            + assistant.rag_context persist

apps/api/atlas_api/tests/test_ws_chat_rag.py  # NEW (or appended to test_ws_chat.py)
```

**Why a builder module instead of inlining in `chat.py`:**
`chat.py` is already the longest file in atlas-api. Rendering chunks → prompt block is domain logic that future agent-loop / batch / CLI callers will need. Lives next to `Retriever` which is already in `atlas_knowledge.retrieval`.

## §2 Per-turn data flow (with `rag_enabled=true`)

```
client → chat.message
  ├─ resolve project (existing)
  ├─ ensure session (existing)
  ├─ load recent message history (existing)
  ├─ NEW: retriever.retrieve(RetrievalQuery(project_id, text, top_k_context))
  ├─ NEW: if chunks non-empty:
  │       ├─ rag_ctx = build_rag_context(chunks)
  │       ├─ emit rag.context event {citations: [...]}
  │       └─ inject rag_ctx.rendered as second system message
  ├─ persist user message row (existing)
  ├─ provider.stream(messages=[persona_system, rag_system?, ...history, user])
  │   └─ for each event: emit chat.token / chat.tool_use / chat.tool_result
  ├─ persist assistant row WITH rag_context=citations (NEW field)
  ├─ persist model_usage row (existing)
  └─ emit chat.done
```

**Event ordering:** the `rag.context` event MUST fire before the first `chat.token` event. Empty-chunks case skips the event entirely (decided in brainstorm Q3) — frontend infers "no relevant context" from absence.

**`rag_enabled=false`:** retrieval is skipped entirely. No event, no injection, `assistant_row.rag_context = None`.

## §3 The rendered prompt block

`build_rag_context(scored: list[ScoredChunk]) -> RagContext` produces:

```
<context>
<source id="1" title="{escaped_title_1}">{chunk_text_1}</source>
<source id="2" title="{escaped_title_2}">{chunk_text_2}</source>
...
</context>

Use the sources above to answer when relevant. Cite as [1], [2].
```

- `id` is 1-indexed, contiguous.
- `title` is XML-escaped (`&` → `&amp;`, `<` → `&lt;`, `>` → `&gt;`, `"` → `&quot;`).
- `chunk_text` is XML-escaped likewise (titles and chunk bodies are user-supplied via ingestion).
- Title comes from `ScoredChunk.parent_title` if set, else `ScoredChunk.chunk.title`, else `"Untitled"`.

The block is sent to the LLM as a separate `system` message inserted between the persona system message and the message history. This keeps the persona prompt cache-friendly across turns (the chunks change, the persona doesn't) and gives the LLM a clear "these are reference materials" signal.

The `citations` list (parallel to the rendered IDs):

```python
[
  {"id": 1, "title": "Doc Title", "score": 0.87, "chunk_id": "<uuid-str>"},
  {"id": 2, "title": "Other Doc", "score": 0.74, "chunk_id": "<uuid-str>"},
]
```

This list is what the WS emits in the `rag.context` event payload AND what gets persisted on `assistant_row.rag_context`. `RagContext.rendered` is for the prompt path only — never sent to the client, never stored.

## §4 WS event payload

```json
{
  "type": "rag.context",
  "payload": {
    "citations": [
      {"id": 1, "title": "Doc Title", "score": 0.87, "chunk_id": "..."},
      {"id": 2, "title": "Other Doc", "score": 0.74, "chunk_id": "..."}
    ]
  },
  "sequence": <monotonic int>
}
```

## §5 Testing

Three WS integration tests using the existing pattern from `test_ws_chat.py` (`aconnect_ws`, `ASGIWebSocketTransport`, `FakeProvider`). New fixture: `FakeEmbedder` + tmp `ChromaVectorStore` + `Retriever` injected via `app.dependency_overrides[get_retriever]`.

1. **happy path** — pre-seed 2 chunks for project P via `vector_store.upsert(...)`, send `chat.message` (`rag_enabled=true`, default `top_k_context=8`). Assert:
   - first non-`chat.token` event after connection is `rag.context`
   - citations payload has 2 entries with `id`, `title`, `score`, `chunk_id`
   - `chat.token` events stream as before (FakeProvider's tokens)
   - `chat.done` arrives
   - assistant row's `rag_context` JSONB matches the emitted citations

2. **rag_enabled=false** — same setup but `payload.rag_enabled: false`. Assert no `rag.context` event arrives between connection and `chat.done`. Assistant row's `rag_context` is `None`.

3. **empty knowledge base** — no chunks seeded. Assert no `rag.context` event. Tokens stream normally. Assistant row's `rag_context` is `None`.

Plus three unit tests for `build_rag_context`:
- empty input → `RagContext(rendered="", citations=[])`
- single chunk → renders one `<source>` + one citation
- title with `<` and `&` → properly escaped in `rendered`, raw in `citations.title` (citations is JSON, not XML)

## §6 Out of scope (deferred)

- **Token-budget truncation of the context block.** With `top_k_context=8` and 512-word chunks, worst case is ~4K tokens — fits any Phase 1 model. Phase 2 can add a budget once we have real usage signal.
- **Hybrid retrieval (BM25 + dense).** Plan 4's dense-only Retriever stays.
- **Reranking / dedup.** Trust top-K cosine ordering.
- **Per-chunk highlighting in the UI** (which sentence came from which `[N]`). Frontend concern — Plan 6.
- **Multi-project retrieval.** Always scoped to `req.project_id`.
- **Streaming the `rag.context` event progressively.** Single emit, all citations at once.

## §7 Definition of Done

1. `build_rag_context` exists in `atlas_knowledge.retrieval.builder` with 3 unit tests passing.
2. `chat_ws` accepts `Retriever` via DI (`get_retriever`).
3. With `rag_enabled=true` and chunks present: `rag.context` event fires before first `chat.token`, citations payload populated, context injected as second system message, assistant row's `rag_context` JSONB matches.
4. With `rag_enabled=false`: no retrieval, no event, no injection, `rag_context` is `None`.
5. With empty knowledge base: no event, normal token stream, `rag_context` is `None`.
6. All Plan 4 tests still pass (160). All new tests pass (~6 added).
7. `uv run ruff check .` + `ruff format --check .` clean.
8. Live smoke (manual): ingest a markdown file via `/api/v1/knowledge/ingest`, then via `wscat` send a `chat.message` referencing that content, observe `rag.context` event in the stream and citations on the persisted assistant message.
