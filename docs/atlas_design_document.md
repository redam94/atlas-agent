# ATLAS — Adaptive Task & Learning Assistant System
### Personal AI Consultant Dashboard · System Design Document
**Version:** 0.1.0 · **Status:** Draft · **Author:** Matt

---

## Table of Contents

1. [Vision & Goals](#1-vision--goals)
2. [High-Level Architecture](#2-high-level-architecture)
3. [Backend — FastAPI Service Layer](#3-backend--fastapi-service-layer)
4. [Core Python Libraries](#4-core-python-libraries)
5. [Knowledge Graph RAG System](#5-knowledge-graph-rag-system)
6. [LLM Provider Abstraction](#6-llm-provider-abstraction)
7. [React Frontend](#7-react-frontend)
8. [Audio Assistant](#8-audio-assistant)
9. [Plugin System](#9-plugin-system)
10. [Discord Integration](#10-discord-integration)
11. [Data Models & Schema](#11-data-models--schema)
12. [Infrastructure & Deployment](#12-infrastructure--deployment)
13. [Security & Privacy](#13-security--privacy)
14. [Development Roadmap](#14-development-roadmap)
15. [Open Questions & Decisions](#15-open-questions--decisions)

---

## 1. Vision & Goals

### 1.1 Problem Statement

Managing multiple consulting projects simultaneously generates enormous cognitive overhead: tracking deliverables, synthesizing research across PDFs and web clippings, maintaining project context across conversations, and switching between different stakeholders and domains. Generic AI chat interfaces lack the structured memory and multi-modal tooling needed for a serious consulting practice.

### 1.2 Core Vision

ATLAS is a self-hosted, AI-native operating system for a consulting practice. It combines a persistent, graph-structured knowledge base with a multi-model AI routing layer, project management primitives, and a rich set of integrations (GCP, GitHub, Gmail, Discord) — all accessible through a unified React dashboard and a voice interface.

### 1.3 Design Principles

- **Model Agnosticism** — Switch between local (LM Studio / Gemma 4) and cloud (Claude, GPT-4o, Gemini) models per task without changing prompts or application logic.
- **Knowledge as Graph** — Documents, notes, and web clips are nodes in a graph. Retrieval is context-aware traversal, not naive vector similarity.
- **Streaming First** — All LLM interactions stream tokens to the UI via WebSockets. No waiting for full completions.
- **Plugin-Oriented** — GCP monitoring, GitHub, email, and other integrations are first-class plugins with a uniform interface that any model can invoke as tools.
- **Privacy by Default** — Sensitive project data stays local; cloud LLM calls are opt-in per project.
- **Async Everything** — All I/O-bound work (embeddings, RAG, tool calls) is non-blocking.

### 1.4 Key Capabilities

| Capability | Description |
|---|---|
| Multi-project workspace | Projects have isolated contexts, RAG scopes, and model preferences |
| Graph RAG | PageRank-inspired retrieval over a knowledge graph of documents, notes, and entities |
| Multi-model routing | Route tasks to local (LM Studio) or cloud (OpenAI, Anthropic, Gemini) based on cost/capability/privacy |
| Audio assistant | Wake-word triggered voice input → STT → LLM → TTS with tool use |
| Email integration | Read, draft, and send Gmail/Outlook via agent with context from RAG |
| GCP monitoring | Cloud Monitoring dashboards, log tailing, alerting summary via plugin |
| GitHub integration | PR review assist, issue triage, code search grounded in project RAG |
| Discord bot | Interact with ATLAS from Discord channels; receive alerts and summaries |
| Knowledge ingestion | PDF parsing, web clipping, note capture with automatic graph linking |

---

## 2. High-Level Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                        ATLAS SYSTEM OVERVIEW                        │
└─────────────────────────────────────────────────────────────────────┘

  ┌───────────────────────────────┐     ┌──────────────────────────────┐
  │         React Frontend        │     │       Discord Bot             │
  │  Dashboard · Chat · Voice UI  │     │  discord.py / slash commands  │
  └────────────┬──────────────────┘     └──────────┬───────────────────┘
               │ HTTP / WebSocket                   │ Internal REST
               ▼                                    ▼
  ┌─────────────────────────────────────────────────────────────────┐
  │                      FastAPI Gateway                            │
  │   /chat  /ws  /projects  /knowledge  /plugins  /audio  /admin   │
  │   ┌──────────────────────────────────────────────────────────┐  │
  │   │             Streaming & WebSocket Manager                │  │
  │   └──────────────────────────────────────────────────────────┘  │
  └───────────────────────────┬─────────────────────────────────────┘
                              │
          ┌───────────────────┼───────────────────┐
          ▼                   ▼                   ▼
  ┌──────────────┐   ┌──────────────────┐  ┌─────────────────┐
  │  atlas-core  │   │  atlas-knowledge  │  │  atlas-plugins  │
  │  Agent loop  │   │  Graph RAG engine │  │  GCP/GH/Gmail   │
  │  Model router│   │  Ingestion pipe   │  │  Plugin registry│
  │  Tool runner │   │  Embedding svc    │  │  Tool schemas   │
  └──────┬───────┘   └────────┬─────────┘  └────────┬────────┘
         │                    │                      │
         ▼                    ▼                      ▼
  ┌──────────────────────────────────────────────────────────────┐
  │                      Data & Model Layer                      │
  │                                                              │
  │  ┌────────────┐  ┌──────────────┐  ┌────────────────────┐   │
  │  │  Neo4j /   │  │  ChromaDB /  │  │  PostgreSQL        │   │
  │  │  Kuzu      │  │  Qdrant      │  │  (projects, logs)  │   │
  │  │  (graph)   │  │  (vectors)   │  │                    │   │
  │  └────────────┘  └──────────────┘  └────────────────────┘   │
  │                                                              │
  │  ┌──────────────────────────────────────────────────────┐   │
  │  │              LLM Provider Abstraction                │   │
  │  │  LM Studio · OpenAI · Anthropic · Google Gemini      │   │
  │  └──────────────────────────────────────────────────────┘   │
  └──────────────────────────────────────────────────────────────┘
```

### 2.1 Repository Structure

```
atlas/
├── apps/
│   ├── api/                  # FastAPI application
│   └── discord-bot/          # Discord bot process
├── packages/
│   ├── atlas-core/           # Agent loop, model router, tool runner
│   ├── atlas-knowledge/      # Graph RAG, ingestion, embeddings
│   └── atlas-plugins/        # GCP, GitHub, Gmail, calendar plugins
├── frontend/                 # React + Vite application
├── infra/
│   ├── docker-compose.yml
│   └── k8s/                  # Optional Kubernetes manifests
├── scripts/                  # Dev utilities
└── docs/                     # Extended documentation
```

---

## 3. Backend — FastAPI Service Layer

### 3.1 Technology Stack

| Component | Choice | Rationale |
|---|---|---|
| Web framework | FastAPI + uvicorn | Async-native, OpenAPI auto-docs, streaming support |
| WebSockets | FastAPI WebSockets + `starlette` | Built-in, handles connection lifecycle |
| Task queue | Celery + Redis | Background ingestion, heavy embedding jobs |
| Cache | Redis | Session state, streaming token buffers |
| Auth | FastAPI Users + JWT | Simple self-hosted auth |
| Logging | structlog + OpenTelemetry | Structured logs, trace correlation |

### 3.2 API Surface

```
GET    /health
GET    /api/v1/projects                  List projects
POST   /api/v1/projects                  Create project
GET    /api/v1/projects/{id}             Get project
PATCH  /api/v1/projects/{id}             Update project

POST   /api/v1/chat                      Single-turn (non-streaming)
POST   /api/v1/chat/stream               SSE streaming endpoint
WS     /api/v1/ws/{session_id}           WebSocket chat session

POST   /api/v1/knowledge/ingest          Ingest document/URL/note
GET    /api/v1/knowledge/search          RAG search
GET    /api/v1/knowledge/graph           Graph traversal query
DELETE /api/v1/knowledge/{node_id}       Remove knowledge node

GET    /api/v1/plugins                   List registered plugins
POST   /api/v1/plugins/{name}/invoke     Invoke plugin directly
GET    /api/v1/plugins/{name}/schema     Get plugin tool schema

POST   /api/v1/audio/transcribe          Whisper STT endpoint
POST   /api/v1/audio/synthesize          TTS endpoint

GET    /api/v1/models                    List available models
POST   /api/v1/models/test               Test model connectivity

GET    /api/v1/admin/stats               System stats
```

### 3.3 WebSocket Protocol

WebSockets handle all interactive chat sessions. The protocol uses a simple JSON message schema:

**Client → Server events:**
```json
{ "type": "chat.message",    "payload": { "text": "...", "project_id": "...", "model_override": null } }
{ "type": "chat.interrupt",  "payload": {} }
{ "type": "audio.chunk",     "payload": { "data": "<base64 PCM>" } }
{ "type": "session.config",  "payload": { "project_id": "...", "model": "claude-3-5-sonnet" } }
```

**Server → Client events:**
```json
{ "type": "chat.token",      "payload": { "token": "..." } }
{ "type": "chat.tool_use",   "payload": { "tool": "github_search", "args": {} } }
{ "type": "chat.tool_result","payload": { "tool": "github_search", "result": {} } }
{ "type": "chat.done",       "payload": { "usage": {}, "model": "...", "latency_ms": 240 } }
{ "type": "chat.error",      "payload": { "message": "...", "code": "..." } }
{ "type": "audio.transcript","payload": { "text": "..." } }
{ "type": "rag.context",     "payload": { "nodes": [], "query": "..." } }
```

### 3.4 Streaming Architecture

All LLM streaming is handled via an async generator pipeline:

```python
async def stream_chat(request: ChatRequest, ws: WebSocket):
    # 1. RAG retrieval (non-blocking)
    context_nodes = await knowledge_engine.retrieve(
        query=request.message,
        project_id=request.project_id,
        top_k=8
    )

    # 2. Build augmented prompt with graph context
    prompt = prompt_builder.build(request, context_nodes)

    # 3. Select model and stream
    model = model_router.select(request, project=project)
    async for event in model.stream(prompt, tools=active_tools):
        if event.type == "token":
            await ws.send_json({"type": "chat.token", "payload": {"token": event.data}})
        elif event.type == "tool_call":
            result = await tool_runner.invoke(event.tool, event.args)
            await ws.send_json({"type": "chat.tool_result", "payload": result})

    # 4. Persist conversation turn
    await conversation_store.save_turn(session_id, prompt, full_response)
```

### 3.5 Session & State Management

- Each WebSocket connection maps to a **session** stored in Redis with TTL.
- Sessions carry: `project_id`, `model_preference`, `conversation_history` (last N turns), `active_tool_set`.
- Conversation history is truncated via a sliding window with a summarization fallback when context approaches the model's limit.

---

## 4. Core Python Libraries

### 4.1 `atlas-core`

The central library containing the agent loop, model router, and tool runner.

```
atlas-core/
├── agent/
│   ├── loop.py           # Main ReAct-style agent loop
│   ├── planner.py        # Multi-step task planning
│   └── memory.py         # Working memory / scratchpad
├── models/
│   ├── router.py         # Model selection logic
│   ├── base.py           # Abstract model interface
│   ├── providers/
│   │   ├── anthropic.py
│   │   ├── openai.py
│   │   ├── gemini.py
│   │   └── lmstudio.py   # OpenAI-compatible local endpoint
│   └── pricing.py        # Token cost tracking
├── tools/
│   ├── runner.py         # Async tool execution
│   ├── registry.py       # Tool registration
│   └── schema.py         # JSON Schema <-> Pydantic bridge
├── prompts/
│   ├── builder.py        # Prompt construction
│   ├── templates/        # Jinja2 system prompt templates
│   └── context.py        # Context window management
└── config.py
```

**Agent Loop (ReAct pattern):**

```python
class AtlasAgent:
    async def run(self, task: str, session: Session) -> AsyncIterator[AgentEvent]:
        scratchpad = []
        for step in range(MAX_STEPS):
            prompt = self.prompt_builder.build(task, scratchpad, session)
            async for event in self.model.stream(prompt, tools=self.tool_registry.schemas()):
                yield event
                if event.type == "tool_call":
                    result = await self.tool_runner.invoke(event.tool, event.args)
                    scratchpad.append(ToolResult(tool=event.tool, result=result))
                    yield AgentEvent(type="tool_result", data=result)
                elif event.type == "final_answer":
                    return
```

### 4.2 `atlas-knowledge`

The knowledge graph ingestion and retrieval engine.

```
atlas-knowledge/
├── ingestion/
│   ├── pipeline.py       # Async ingestion orchestrator
│   ├── parsers/
│   │   ├── pdf.py        # PyMuPDF + pdfplumber
│   │   ├── web.py        # Playwright + Trafilatura
│   │   ├── markdown.py
│   │   └── email.py
│   ├── chunker.py        # Semantic chunking
│   └── enricher.py       # Entity extraction, metadata
├── graph/
│   ├── store.py          # Neo4j / Kuzu interface
│   ├── builder.py        # Graph construction from chunks
│   ├── traversal.py      # Graph RAG retrieval
│   └── pagerank.py       # Personalized PageRank scorer
├── embeddings/
│   ├── service.py        # Embedding model abstraction
│   ├── providers/
│   │   ├── openai.py     # text-embedding-3-large
│   │   └── local.py      # nomic-embed-text via LM Studio
│   └── cache.py          # Redis embedding cache
├── vector/
│   └── store.py          # ChromaDB / Qdrant interface
└── retrieval/
    ├── hybrid.py         # BM25 + vector + graph fusion
    └── reranker.py       # Cross-encoder reranking
```

### 4.3 `atlas-plugins`

Self-contained plugin modules each exposing a tool schema and async handler.

```
atlas-plugins/
├── base.py               # Plugin base class
├── registry.py           # Plugin registration & discovery
├── gmail/
│   ├── plugin.py
│   ├── auth.py           # OAuth2 flow
│   └── tools.py          # search_email, draft_email, send_email
├── github/
│   ├── plugin.py
│   └── tools.py          # search_code, list_prs, get_issue, create_issue
├── gcp/
│   ├── plugin.py
│   └── tools.py          # get_metrics, tail_logs, list_alerts, get_costs
├── calendar/
│   ├── plugin.py
│   └── tools.py          # list_events, create_event, find_slot
├── notion/
│   ├── plugin.py
│   └── tools.py          # search_pages, create_page, update_block
└── web/
    ├── plugin.py
    └── tools.py          # web_search, web_fetch, screenshot
```

**Plugin Interface:**

```python
class AtlasPlugin(ABC):
    name: str
    description: str

    @abstractmethod
    def get_tools(self) -> list[ToolSchema]:
        """Return tool definitions in JSON Schema format."""

    @abstractmethod
    async def invoke(self, tool_name: str, args: dict) -> ToolResult:
        """Execute a tool call and return structured result."""

    async def health_check(self) -> bool:
        return True
```

---

## 5. Knowledge Graph RAG System

### 5.1 Design Philosophy

Standard vector RAG treats documents as isolated chunks — it finds similar text but loses relational context. In a consulting practice, the most valuable insight is often the *connection* between things: "this client's concern about CAC is mentioned in three different project proposals, and the same methodology was used in this other engagement."

ATLAS uses a **graph-first retrieval approach** inspired by PageRank, where:
- Documents, chunks, entities, and projects are **nodes**.
- Semantic similarity, explicit references, co-citation, and temporal proximity are **edges**.
- Retrieval is a personalized random walk from a query-anchored seed set.

### 5.2 Knowledge Graph Schema

```
Nodes:
  Project      { id, name, description, status, created_at }
  Document     { id, title, source_url, file_path, type, ingested_at }
  Chunk        { id, text, position, token_count, embedding_id }
  Entity       { id, name, type: [person|org|concept|metric|date] }
  Note         { id, text, created_at, tags }
  WebClip      { id, url, title, captured_at, raw_html }
  Conversation { id, session_id, created_at }

Edges:
  BELONGS_TO      Chunk → Document
  PART_OF         Document → Project
  REFERENCES      Chunk → Entity
  LINKED_TO       Document → Document    (explicit links or citations)
  SEMANTICALLY_NEAR Chunk → Chunk        (cosine sim > threshold)
  CREATED_IN      Conversation → Project
  TAGGED_WITH     Note → Entity
  TEMPORAL_NEAR   Document → Document    (within same time window)
```

### 5.3 Ingestion Pipeline

```
Raw Input (PDF / URL / Note / Email)
         │
         ▼
┌────────────────────┐
│   Parser           │  Extract text, metadata, structure
└────────┬───────────┘
         ▼
┌────────────────────┐
│   Semantic Chunker │  Overlap-aware, boundary-respecting chunks
└────────┬───────────┘
         │         (async parallel)
    ┌────┴────┐
    ▼         ▼
┌───────┐ ┌──────────┐
│Embed  │ │Entity NER│  Parallel: generate embeddings & extract entities
└───┬───┘ └────┬─────┘
    │           │
    ▼           ▼
┌──────────────────────┐
│   Graph Builder      │  Create nodes, edges (semantic & entity links)
└──────────┬───────────┘
           ▼
┌──────────────────────┐
│  PageRank Updater    │  Incremental PR re-computation on affected subgraph
└──────────────────────┘
```

**Semantic Chunking Strategy:**
- Target chunk size: 512 tokens with 128-token overlap.
- Respect structural boundaries: headings, paragraphs, list items, code blocks.
- For PDFs: extract page boundaries and treat each page as a "document section" node, with chunks as children.
- Cross-chunk entity co-reference resolution using a lightweight SpaCy pipeline.

### 5.4 Graph RAG Retrieval

Retrieval is a three-stage hybrid process:

**Stage 1 — Seed Retrieval (Broad)**
- BM25 keyword search over chunk text.
- Dense vector search (ANN) against embedding store.
- Merge results with RRF (Reciprocal Rank Fusion).
- Output: top-20 seed chunks.

**Stage 2 — Graph Expansion (Contextual)**
- From each seed chunk, perform a bounded graph walk:
  - Follow `SEMANTICALLY_NEAR` edges (depth 1).
  - Follow `REFERENCES` → Entity → `REFERENCES` (co-entity chunks).
  - Follow `LINKED_TO` edges (explicit citations).
  - Ascend to parent Document, then descend to sibling Chunks within ±2 positions.
- Score expanded nodes using **Personalized PageRank** seeded from the initial retrieval set.
- This surfaces related chunks that wouldn't appear in raw vector search.

**Stage 3 — Reranking & Context Assembly**
- Apply a cross-encoder reranker (e.g., `ms-marco-MiniLM`) to all candidate chunks.
- Filter to top-K (default 8) by reranker score.
- Assemble context: include chunk text + parent document title/source + entity links.
- Inject structured context into system prompt.

### 5.5 PageRank-Inspired Scoring

Inspired by the original Google PageRank formulation and adapted for knowledge graphs:

```
score(v) = (1 - d) · seed_score(v)
         + d · Σ_{u ∈ in_neighbors(v)} ( score(u) / out_degree(u) · edge_weight(u→v) )
```

Where:
- `d = 0.85` is the damping factor.
- `seed_score(v)` is the initial BM25+vector retrieval score (personalizing the walk).
- `edge_weight` reflects edge type: semantic similarity edges use cosine score, citation edges use 1.0.

This runs as a sparse iterative computation on the subgraph neighborhood of retrieved seeds — not the full graph — making it tractable at query time.

### 5.6 Knowledge Ingestion UI Flows

| Ingestion Type | Trigger | Process |
|---|---|---|
| PDF upload | Drag & drop in dashboard | Upload → parse → chunk → embed → graph |
| Web clip | Browser extension or URL paste | Playwright fetch → Trafilatura extract → ingest |
| Note | Inline editor in dashboard | Markdown note → NER → link to project → ingest |
| Email | Gmail plugin scheduled sync | Fetch emails for project context → parse → ingest |
| GitHub content | GitHub plugin | READMEs, PR descriptions, issue bodies → ingest |

---

## 6. LLM Provider Abstraction

### 6.1 Unified Model Interface

All providers implement the same async streaming interface so application code never has provider-specific logic:

```python
class BaseModel(ABC):
    provider: str
    model_id: str
    context_window: int
    supports_tools: bool
    cost_per_1k_input: float
    cost_per_1k_output: float

    @abstractmethod
    async def stream(
        self,
        messages: list[Message],
        tools: list[ToolSchema] | None = None,
        temperature: float = 0.7,
    ) -> AsyncIterator[ModelEvent]:
        ...

    @abstractmethod
    async def embed(self, texts: list[str]) -> list[list[float]]:
        ...
```

### 6.2 Supported Providers

| Provider | Models | Interface | Local? |
|---|---|---|---|
| LM Studio | Gemma 4, Mistral, Llama 3.3, Qwen 2.5 | OpenAI-compatible REST | ✅ Yes |
| OpenAI | GPT-4o, GPT-4o-mini, o3 | `openai` SDK | ❌ Cloud |
| Anthropic | Claude Sonnet 4, Opus 4 | `anthropic` SDK | ❌ Cloud |
| Google | Gemini 2.5 Pro/Flash | `google-generativeai` | ❌ Cloud |

**LM Studio Integration:**
LM Studio exposes an OpenAI-compatible API on `localhost:1234`. The `lmstudio.py` provider simply points the `openai` client at this base URL. Model discovery is done by polling the LM Studio `/v1/models` endpoint and caching results.

```python
class LMStudioProvider(BaseModel):
    def __init__(self, base_url: str = "http://localhost:1234/v1"):
        self.client = AsyncOpenAI(base_url=base_url, api_key="lm-studio")

    async def stream(self, messages, tools=None, **kwargs):
        stream = await self.client.chat.completions.create(
            model=self.model_id,
            messages=messages,
            tools=tools,
            stream=True,
            **kwargs
        )
        async for chunk in stream:
            yield self._parse_chunk(chunk)
```

### 6.3 Model Router

The router selects a model for each request based on configurable policy:

```python
class ModelRouter:
    def select(self, request: ChatRequest, project: Project) -> BaseModel:
        # 1. Explicit override from request or project config
        if request.model_override:
            return self.registry[request.model_override]

        # 2. Privacy policy — never send to cloud if project is flagged
        if project.privacy_level == "local_only":
            return self.get_best_local_model(request.task_type)

        # 3. Task-based routing
        match request.task_type:
            case "code_review":
                return self.registry["claude-sonnet-4"]
            case "quick_lookup":
                return self.get_fastest_model()  # local Gemma 4
            case "long_document":
                return self.registry["gemini-2.5-pro"]  # large context
            case _:
                return self.default_model

    def get_fastest_model(self) -> BaseModel:
        # Prefer local if LM Studio is running
        if self.lmstudio.is_healthy():
            return self.lmstudio.default
        return self.registry["gpt-4o-mini"]
```

### 6.4 Cost & Usage Tracking

Every model call logs token usage to PostgreSQL:

```sql
CREATE TABLE model_usage (
    id UUID PRIMARY KEY,
    session_id UUID,
    project_id UUID,
    provider TEXT,
    model_id TEXT,
    input_tokens INTEGER,
    output_tokens INTEGER,
    cost_usd DECIMAL(10,6),
    latency_ms INTEGER,
    task_type TEXT,
    created_at TIMESTAMPTZ
);
```

The dashboard displays per-project spend, model breakdown, and latency trends.

---

## 7. React Frontend

### 7.1 Technology Stack

| Component | Choice |
|---|---|
| Framework | React 19 + Vite |
| State management | Zustand (global) + React Query (server state) |
| UI components | shadcn/ui + Tailwind CSS v4 |
| WebSocket client | Native WS API wrapped in a custom hook |
| Charts | Recharts |
| Editor | CodeMirror 6 (code blocks), TipTap (rich notes) |
| Graph visualization | Cytoscape.js (knowledge graph explorer) |
| Audio | Web Audio API + MediaRecorder |
| Routing | React Router v7 |

### 7.2 Application Layout

```
┌─────────────────────────────────────────────────────────────────────┐
│  ATLAS                              [Project: CircleK MMM] [⚙] [👤] │
├────────────┬────────────────────────────────────────────────────────┤
│            │                                                        │
│  SIDEBAR   │           MAIN CONTENT AREA                           │
│            │                                                        │
│  Projects  │   ┌─────────────────────────────────────────────────┐ │
│  ──────    │   │                                                 │ │
│  > CircleK │   │   Context-aware chat panel / active view        │ │
│  > GeoLift │   │                                                 │ │
│  > ATLAS   │   │   [Streaming token output renders here]         │ │
│            │   │                                                 │ │
│  Knowledge │   └─────────────────────────────────────────────────┘ │
│  ──────    │   ┌─────────────────────────────────────────────────┐ │
│  Graph     │   │  [Input bar]              [🎤] [Model ▾] [Send] │ │
│  Search    │   └─────────────────────────────────────────────────┘ │
│  Ingest    │                                                        │
│            │                                                        │
│  Plugins   │                                                        │
│  ──────    │                                                        │
│  Gmail     │                                                        │
│  GitHub    │                                                        │
│  GCP       │                                                        │
│  Discord   │                                                        │
│            │                                                        │
└────────────┴────────────────────────────────────────────────────────┘
```

### 7.3 Key Views & Components

**Chat Panel** — The primary interaction surface.
- Streaming token rendering with smooth append animation.
- Tool-use cards: collapsible inline cards showing tool invocation + result (e.g., "🔍 Searched GitHub: `geo-lift experiment design`").
- RAG context panel: slide-out drawer showing source documents used for grounding.
- Message history with copy, regenerate, and branch controls.
- Model selector per-message (inline override).

**Knowledge Explorer** — Interactive graph visualization.
- Cytoscape.js force-directed graph of all nodes and edges in the current project scope.
- Click a node to open its details panel and highlight neighboring nodes.
- Filter by node type (Document / Chunk / Entity / Note).
- Search bar performs hybrid retrieval and highlights matching subgraph.
- "Ingest" drop zone for files directly in the graph view.

**Project Dashboard** — Per-project home page.
- Status cards: open tasks, recent conversations, ingestion stats, spend.
- Timeline of recent knowledge ingestion events.
- Quick-access to related plugins (GitHub repo, GCP project).

**Plugin Views** — Embedded views per plugin:
- **Gmail**: Inbox summary, draft panel, thread viewer grounded in project RAG.
- **GitHub**: PR list, issue triage, code search.
- **GCP**: Cloud Monitoring charts pulled via API, log stream viewer, alert feed.

**Audio Mode** — Full-screen voice interface triggered by mic button or wake word.
- Waveform visualization during listening.
- Live transcript display as Whisper processes.
- TTS playback with pause/interrupt.

### 7.4 WebSocket Hook

```typescript
function useAtlasChat(sessionId: string) {
  const [messages, setMessages] = useState<Message[]>([]);
  const [isStreaming, setIsStreaming] = useState(false);
  const ws = useRef<WebSocket | null>(null);

  useEffect(() => {
    ws.current = new WebSocket(`wss://localhost:8000/api/v1/ws/${sessionId}`);
    ws.current.onmessage = (event) => {
      const { type, payload } = JSON.parse(event.data);
      switch (type) {
        case "chat.token":
          appendToken(payload.token);
          break;
        case "chat.tool_use":
          appendToolCard(payload);
          break;
        case "chat.done":
          setIsStreaming(false);
          break;
      }
    };
    return () => ws.current?.close();
  }, [sessionId]);

  const send = (text: string, config?: ChatConfig) => {
    setIsStreaming(true);
    ws.current?.send(JSON.stringify({ type: "chat.message", payload: { text, ...config } }));
  };

  return { messages, isStreaming, send };
}
```

### 7.5 State Architecture

```
Zustand Global Store:
  auth: { user, token }
  projects: { list, active_project_id }
  models: { available, selected }
  plugins: { enabled, status }
  audio: { mode, isRecording, transcript }
  ui: { sidebar_collapsed, active_view }

React Query (server-synced):
  useProject(id)
  useKnowledgeNodes(project_id, filters)
  useConversations(project_id)
  usePluginStatus(plugin_name)
  useModelUsage(project_id, time_range)
```

---

## 8. Audio Assistant

### 8.1 Architecture

```
Browser Mic (MediaRecorder)
       │ PCM chunks over WebSocket (audio.chunk events)
       ▼
FastAPI /api/v1/ws/{session_id}
       │
       ▼
WhisperService (streaming transcription)
  ├── Local: faster-whisper (CUDA / CPU)
  └── Cloud: OpenAI Whisper API (fallback)
       │
       ▼ transcript
AtlasAgent.run(transcript, session)
       │
       ▼ token stream
TTSService
  ├── Local: Kokoro / Piper TTS
  └── Cloud: OpenAI TTS / ElevenLabs
       │
       ▼ audio stream
Browser Audio playback
```

### 8.2 Wake Word Detection

- **Browser-side**: Use `@picovoice/porcupine-web` for on-device wake word detection ("Hey Atlas").
- Wake word detection runs in a Web Worker — zero latency overhead.
- Only activates mic streaming when triggered.

### 8.3 Voice Activity Detection

- `silero-vad` model running in-browser via ONNX Runtime Web.
- Automatically detects end-of-speech and sends the `audio.done` event to the backend.
- Prevents empty or partial utterances from being sent to Whisper.

### 8.4 Interruption Handling

- Mid-stream interruption: client sends `chat.interrupt` event.
- Server cancels the current model stream via `asyncio.CancelledError` propagation.
- TTS playback is stopped client-side and the agent returns to listening mode.

---

## 9. Plugin System

### 9.1 Plugin Registry

Plugins are auto-discovered by scanning the `atlas-plugins` package and registered at startup. Each plugin is loaded conditionally based on configured credentials.

```python
class PluginRegistry:
    plugins: dict[str, AtlasPlugin] = {}

    def register(self, plugin: AtlasPlugin):
        self.plugins[plugin.name] = plugin

    def get_tool_schemas(self, enabled: list[str]) -> list[ToolSchema]:
        return [
            tool
            for name in enabled
            for tool in self.plugins[name].get_tools()
        ]

    async def invoke(self, tool_name: str, args: dict) -> ToolResult:
        plugin_name, _ = tool_name.split(".", 1)
        return await self.plugins[plugin_name].invoke(tool_name, args)
```

### 9.2 Gmail Plugin

**Auth:** OAuth 2.0 with refresh token stored in encrypted config.

**Tools exposed to the model:**
```
gmail.search_threads(query: str, max_results: int) → list[ThreadSummary]
gmail.get_thread(thread_id: str) → Thread
gmail.draft_email(to: str, subject: str, body: str, thread_id?: str) → Draft
gmail.send_draft(draft_id: str) → SendResult
gmail.label_thread(thread_id: str, label: str) → void
```

**RAG integration:** When drafting emails, the agent retrieves relevant project context (past emails on the topic, relevant document chunks) and injects it as grounding.

### 9.3 GitHub Plugin

**Auth:** Personal Access Token or GitHub App.

**Tools:**
```
github.search_code(query: str, repo?: str) → list[CodeResult]
github.list_prs(repo: str, state: str) → list[PR]
github.get_pr(repo: str, pr_number: int) → PRDetail
github.get_issue(repo: str, issue_number: int) → Issue
github.create_issue(repo: str, title: str, body: str, labels: list) → Issue
github.list_commits(repo: str, branch: str, since: str) → list[Commit]
github.review_pr(repo: str, pr_number: int, feedback: str) → ReviewDraft
```

**Knowledge sync:** Optionally ingest README files, PR descriptions, and merged PR diffs into the project knowledge graph on a schedule.

### 9.4 GCP Plugin

**Auth:** Application Default Credentials or Service Account JSON.

**Tools:**
```
gcp.get_metrics(project: str, metric: str, window: str) → MetricSeries
gcp.tail_logs(project: str, filter: str, limit: int) → list[LogEntry]
gcp.list_alerts(project: str) → list[AlertPolicy]
gcp.get_active_incidents(project: str) → list[Incident]
gcp.get_billing_summary(project: str, period: str) → BillingSummary
gcp.list_services(project: str) → list[CloudService]
gcp.describe_instance(project: str, zone: str, instance: str) → InstanceDetail
```

**Dashboard integration:** The GCP plugin feeds a live panel in the React frontend showing key metrics (CPU, latency, error rate, cost) refreshed on a 60-second poll.

### 9.5 Adding Custom Plugins

Any Python class implementing `AtlasPlugin` with valid `ToolSchema` definitions can be registered. This makes it straightforward to add plugins for Notion, Linear, Slack, AWS, or any internal API.

---

## 10. Discord Integration

### 10.1 Architecture

The Discord bot runs as a separate Python process (using `discord.py`) that communicates with the main FastAPI service via an internal REST API. This keeps the bot stateless and simplifies auth separation.

```
Discord Gateway (discord.py)
       │
       │  /api/internal/discord/chat
       ▼
FastAPI Internal Endpoint (not public-facing)
       │
       ▼
AtlasAgent (same core as web chat)
       │
       ▼
Discord bot sends response (chunked if > 2000 chars)
```

### 10.2 Slash Commands

```
/atlas ask [question]               - Ask ATLAS, uses default project context
/atlas ask --project [name] [question]
/atlas ingest [url]                 - Ingest a URL into the active project
/atlas summarize                    - Summarize recent activity across projects
/atlas status                       - Show plugin health and system status
/atlas gcp [query]                  - Query GCP plugin directly
/atlas pr review [url]              - Review a GitHub PR
/atlas email draft [to] [subject]   - Draft an email
```

### 10.3 Notification Channels

ATLAS can push proactive notifications to designated Discord channels:

- **GCP Alerts** — Any active incident or alert policy firing is sent to `#gcp-alerts`.
- **Ingestion Complete** — When a large PDF or batch ingest finishes, confirmation in `#knowledge-updates`.
- **Daily Digest** — Scheduled morning summary of open items across all projects.
- **GitHub PRs** — New PRs, review requests, or CI failures in `#engineering`.

### 10.4 Permissions & Security

- Discord interactions only have access to projects explicitly linked to a Discord server.
- All Discord-initiated model calls are logged with `source: "discord"` for audit.
- Sensitive tool invocations (send email, create issues) require a confirmation reaction before executing.

---

## 11. Data Models & Schema

### 11.1 Core Pydantic Models

```python
class Project(BaseModel):
    id: UUID
    name: str
    description: str
    status: Literal["active", "paused", "archived"]
    privacy_level: Literal["local_only", "cloud_ok"]
    default_model: str
    enabled_plugins: list[str]
    knowledge_scope: list[UUID]  # Document IDs in scope
    created_at: datetime
    updated_at: datetime

class ChatRequest(BaseModel):
    message: str
    project_id: UUID
    session_id: UUID
    model_override: str | None = None
    task_type: str | None = None
    rag_enabled: bool = True
    top_k_context: int = 8

class KnowledgeNode(BaseModel):
    id: UUID
    type: Literal["document", "chunk", "entity", "note", "web_clip"]
    text: str
    metadata: dict[str, Any]
    project_ids: list[UUID]
    embedding_id: str | None
    created_at: datetime

class ToolSchema(BaseModel):
    name: str                    # e.g., "gmail.search_threads"
    description: str
    parameters: dict             # JSON Schema
    plugin: str
    requires_confirmation: bool = False

class IngestRequest(BaseModel):
    source_type: Literal["pdf", "url", "note", "email"]
    content: str | None = None   # raw text or base64 for PDF
    url: str | None = None
    project_id: UUID
    tags: list[str] = []
    metadata: dict = {}
```

### 11.2 PostgreSQL Schema

```sql
-- Core tables
CREATE TABLE projects (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT NOT NULL,
    description TEXT,
    status TEXT DEFAULT 'active',
    privacy_level TEXT DEFAULT 'cloud_ok',
    default_model TEXT,
    enabled_plugins JSONB DEFAULT '[]',
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE sessions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id UUID REFERENCES projects(id),
    model TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    last_active_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE messages (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id UUID REFERENCES sessions(id),
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    tool_calls JSONB,
    rag_context JSONB,
    model TEXT,
    token_count INTEGER,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE ingestion_jobs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id UUID REFERENCES projects(id),
    source_type TEXT,
    source_url TEXT,
    status TEXT DEFAULT 'pending',
    node_ids JSONB DEFAULT '[]',
    error TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    completed_at TIMESTAMPTZ
);
```

---

## 12. Infrastructure & Deployment

### 12.1 Development Setup

```yaml
# docker-compose.yml
services:
  api:
    build: ./apps/api
    ports: ["8000:8000"]
    environment:
      - DATABASE_URL=postgresql://atlas:atlas@postgres/atlas
      - REDIS_URL=redis://redis:6379
      - NEO4J_URL=bolt://neo4j:7687
    depends_on: [postgres, redis, neo4j, chroma]
    volumes: ["./packages:/packages"]   # hot reload

  worker:
    build: ./apps/api
    command: celery -A atlas.worker worker --loglevel=info
    depends_on: [redis, postgres]

  frontend:
    build: ./frontend
    ports: ["3000:3000"]
    environment:
      - VITE_API_URL=http://localhost:8000

  discord-bot:
    build: ./apps/discord-bot
    environment:
      - DISCORD_TOKEN=${DISCORD_TOKEN}
      - ATLAS_INTERNAL_URL=http://api:8000

  postgres:
    image: postgres:16
    volumes: ["postgres_data:/var/lib/postgresql/data"]

  redis:
    image: redis:7-alpine

  neo4j:
    image: neo4j:5
    ports: ["7474:7474", "7687:7687"]
    environment:
      - NEO4J_AUTH=neo4j/atlas_password
    volumes: ["neo4j_data:/data"]

  chroma:
    image: chromadb/chroma:latest
    ports: ["8001:8000"]
    volumes: ["chroma_data:/chroma/chroma"]
```

### 12.2 Environment Configuration

```bash
# LLM Providers
ANTHROPIC_API_KEY=sk-ant-...
OPENAI_API_KEY=sk-...
GOOGLE_API_KEY=...
LMSTUDIO_BASE_URL=http://host.docker.internal:1234/v1

# Plugins
GMAIL_CLIENT_ID=...
GMAIL_CLIENT_SECRET=...
GITHUB_PAT=ghp_...
GCP_SERVICE_ACCOUNT_JSON=/secrets/gcp-sa.json
DISCORD_TOKEN=...

# Infrastructure
DATABASE_URL=postgresql://...
REDIS_URL=redis://...
NEO4J_URL=bolt://...
NEO4J_PASSWORD=...
CHROMA_URL=http://chroma:8000

# Audio
WHISPER_MODEL=large-v3
TTS_PROVIDER=kokoro
```

### 12.3 GCP Production Deployment (Optional)

For a production self-hosted deployment on GCP:

- **API**: Cloud Run (autoscaling, HTTPS managed)
- **Workers**: Cloud Run Jobs or GKE node pool
- **Database**: Cloud SQL (PostgreSQL)
- **Redis**: Memorystore
- **Neo4j**: Self-managed on GCE or Aura (managed Neo4j)
- **Vector store**: Self-managed Qdrant on GCE
- **Secrets**: Secret Manager
- **CI/CD**: Cloud Build + Artifact Registry

---

## 13. Security & Privacy

### 13.1 Authentication

- FastAPI Users with JWT tokens (HS256).
- Token expiry: 24h access token, 30d refresh token.
- All API endpoints except `/health` require a valid JWT.
- Discord bot uses a separate HMAC-signed internal API key.

### 13.2 Data Privacy

- **Project-level privacy policy**: Projects marked `local_only` will never route to cloud LLM providers.
- **Knowledge isolation**: RAG retrieval is always scoped to `project_ids` the user has access to.
- **Credential storage**: OAuth tokens and API keys are stored encrypted at rest (Fernet / KMS in production).
- **Audit log**: All model invocations, plugin calls, and sensitive operations are logged with user identity and timestamp.

### 13.3 Prompt Injection Defense

Since ATLAS ingests external content (web pages, emails) and uses it in LLM prompts, prompt injection is a real risk. Mitigations:

- All retrieved chunks are wrapped in explicit XML delimiters: `<retrieved_context>...</retrieved_context>`.
- System prompt instructs models to treat context as passive reference data, not instructions.
- Plugin tool results are similarly wrapped: `<tool_result tool="gmail.search">...</tool_result>`.
- Regular auditing of ingested content types against injection pattern heuristics.

---

## 14. Development Roadmap

### Phase 1 — Foundation (Weeks 1-4)
- [ ] FastAPI skeleton with WebSocket streaming
- [ ] LLM provider abstraction (OpenAI, Anthropic, LM Studio)
- [ ] Basic PostgreSQL + Redis setup
- [ ] React frontend shell: chat panel, project sidebar
- [ ] Simple vector RAG (ChromaDB) without graph layer

### Phase 2 — Knowledge Graph (Weeks 5-8)
- [ ] PDF + URL ingestion pipeline
- [ ] Neo4j graph schema and builder
- [ ] Hybrid retrieval (BM25 + vector + graph expansion)
- [ ] Knowledge Explorer UI (Cytoscape.js)
- [ ] Note editor with inline linking

### Phase 3 — Plugins (Weeks 9-12)
- [ ] Plugin registry and base class
- [ ] Gmail plugin (OAuth + core tools)
- [ ] GitHub plugin
- [ ] GCP plugin (metrics, logs, alerts)
- [ ] Plugin management UI

### Phase 4 — Audio & Advanced (Weeks 13-16)
- [ ] Whisper STT integration
- [ ] TTS pipeline (Kokoro local)
- [ ] Wake word detection in browser
- [ ] PageRank-based graph RAG scoring
- [ ] Model cost dashboard

### Phase 5 — Discord & Polish (Weeks 17-20)
- [ ] Discord bot with slash commands
- [ ] Notification channel routing
- [ ] Gemini provider
- [ ] Multi-user auth (if needed)
- [ ] Production GCP deployment guide

---

## 15. Open Questions & Decisions

| # | Question | Options | Status |
|---|---|---|---|
| 1 | Graph database choice | Neo4j (mature, Cypher) vs Kuzu (embedded, faster, no server) | **Decide** |
| 2 | Vector store | ChromaDB (simple) vs Qdrant (production-grade, filtering) | **Decide** |
| 3 | Embedding model for local | nomic-embed-text via LM Studio vs `sentence-transformers` directly | Open |
| 4 | TTS voice | Kokoro (high quality, Python) vs Piper (fast, lightweight) | Open |
| 5 | PageRank computation | Run on every ingest vs nightly batch vs on-query subgraph | Open |
| 6 | Frontend auth | Simple JWT stored in localStorage vs httpOnly cookie | Decide |
| 7 | Multi-user vs single-user | Is ATLAS purely personal or shared with a small team? | **Decide** |
| 8 | Email provider | Gmail only vs abstract to support Outlook/IMAP | Open |
| 9 | Note format | Markdown only vs Notion-like block editor | Open |
| 10 | Discord per-project mapping | One bot per server, map channels to projects? | Open |

---

## Appendix A — Technology Reference

| Layer | Technology | Version |
|---|---|---|
| Backend framework | FastAPI | 0.115+ |
| ASGI server | uvicorn | 0.30+ |
| Task queue | Celery | 5.4+ |
| Graph database | Neo4j / Kuzu | 5.x / 0.7+ |
| Vector store | ChromaDB / Qdrant | latest |
| Relational DB | PostgreSQL | 16 |
| Cache / broker | Redis | 7 |
| PDF parsing | PyMuPDF + pdfplumber | latest |
| Web scraping | Playwright + Trafilatura | latest |
| NLP / NER | SpaCy | 3.7+ |
| Embeddings | text-embedding-3-large / nomic | — |
| STT | faster-whisper | latest |
| TTS | Kokoro / Piper | latest |
| Wake word | Porcupine Web | latest |
| Frontend | React 19 + Vite | latest |
| UI components | shadcn/ui + Tailwind v4 | latest |
| Graph viz | Cytoscape.js | 3.x |
| Discord bot | discord.py | 2.x |
| Containerization | Docker + Compose | 24+ |

---

*ATLAS Design Document — v0.1.0 · Generated April 2026*
