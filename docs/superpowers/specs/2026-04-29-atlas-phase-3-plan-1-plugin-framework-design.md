# ATLAS Phase 3 — Plan 1: Plugin Framework (design)

**Date:** 2026-04-29
**Phase:** 3
**Plan:** 1 of 5
**Depends on:** Phases 1–2 merged.
**Blocks:** Plans 2–5 (Discord, GitHub, GCP, Gmail) all build on this framework.

## 1. Purpose

Plan 1 ships the plumbing that makes ATLAS a tool-using agent. No real integrations land here — the goal is a complete, tested framework that Plans 2–5 can drop plugins into without touching the framework code. Concretely: `atlas-plugins` package, plugin ABC, registry, encrypted credential store, six REST endpoints, Anthropic tool-use loop in the chat WS handler, and a `FakePlugin` that exists solely to exercise everything end-to-end.

## 2. Scope

In scope:
- New `atlas-plugins` package with `AtlasPlugin` ABC, `PluginRegistry`, `CredentialStore`, `FakePlugin`, `REGISTERED_PLUGINS` list export.
- `cryptography` (Fernet) dependency.
- Alembic migration `0007`: `plugin_credentials` table + `projects.enabled_plugins TEXT[]` column.
- Pydantic models: `ToolSchema`, `ToolResult`, `HealthStatus`, `PluginInfo`, request/response shapes for the new router.
- `apps/api/atlas_api/routers/plugins.py` with 6 endpoints (list, schema, invoke, credentials list, set, delete).
- Lifespan wiring: build `CredentialStore` + construct registered plugins + `PluginRegistry.warm()` + put on `app.state`.
- WS chat handler change: when provider is Anthropic, build the tool list from the registry + project's `enabled_plugins`, run the tool-use loop with a 10-turn cap, emit `tool_call` and `tool_result` events to the WS client.
- Frontend: `tool-call-chip.tsx` component + chat store changes to render the new WS events as inline chips.

Out of scope (explicit non-goals):
- Any real plugin (Discord/GitHub/GCP/Gmail). FakePlugin only.
- Per-project plugin-toggle UI. The DB column exists; toggling is `psql` or direct `PATCH /projects/{id}` for v1.
- Credential management UI.
- Streaming tool outputs.
- LM Studio tool emulation. Local-model chat stays toolless.
- Reaction-based confirmations. Token-based confirmation is each plugin's concern, not the framework's.
- Knowledge-graph ingestion of tool results.
- Per-tool authorization beyond plugin-level enablement.
- Key rotation / re-encrypt-all flow.
- Tool-call telemetry / analytics.

## 3. Architecture

```
                 ┌───────────────────────────────────┐
                 │  apps/api (FastAPI)               │
                 │                                   │
                 │  /api/v1/plugins/*                │
                 │  WS /chat (tool-use loop)         │
                 └────┬───────────────────┬──────────┘
                      │                   │
                      ▼                   ▼
       ┌────────────────────────┐  ┌──────────────────┐
       │ PluginRegistry         │  │ CredentialStore  │
       │   list, get_tools,     │  │   set/get/list/  │
       │   invoke, warm         │  │   delete (Fernet)│
       └─────┬──────────────────┘  └────────┬─────────┘
             │                              │
             ▼                              ▼
       ┌──────────────────┐         ┌────────────────────┐
       │ FakePlugin       │         │ Postgres           │
       │  fake.echo       │         │  plugin_credentials│
       │  fake.fail       │         │  projects.enabled_ │
       │  fake.recurse    │         │  plugins TEXT[]    │
       └──────────────────┘         └────────────────────┘
```

### 3.1 The plugin contract

```python
class ToolSchema(AtlasModel):
    name: str            # "fake.echo" — must start with "<plugin.name>."
    description: str
    input_schema: dict[str, Any]   # JSON Schema dict (Anthropic tool-use shape)


class ToolResult(AtlasModel):
    ok: bool
    value: Any | None = None
    error: str | None = None


class HealthStatus(AtlasModel):
    ok: bool
    detail: str | None = None


class AtlasPlugin(ABC):
    name: str           # "fake"
    description: str

    def __init__(self, credentials: CredentialStore) -> None:
        self._credentials = credentials

    async def _get_credentials(self, account_id: str = "default") -> dict[str, Any]:
        """Lazy fetch — called per-invoke so credential rotations take effect immediately."""
        return await self._credentials.get(self.name, account_id)

    @abstractmethod
    def get_tools(self) -> list[ToolSchema]: ...

    @abstractmethod
    async def invoke(self, tool_name: str, args: dict) -> ToolResult: ...

    async def health(self) -> HealthStatus:
        """Default: ok if at least one credential row exists. Plugins override for liveness probes."""
        accounts = await self._credentials.list(self.name)
        if not accounts:
            return HealthStatus(ok=False, detail="no credentials registered")
        return HealthStatus(ok=True)
```

Tool errors live inside `ToolResult` — never as raised exceptions out of `invoke()`. The registry catches anything that escapes and converts to `ToolResult(ok=False, error=str(e))`.

### 3.2 Registry

```python
REGISTERED_PLUGINS: list[type[AtlasPlugin]] = [FakePlugin]   # appended by future plans


class PluginRegistry:
    def __init__(self, plugins: list[AtlasPlugin]) -> None:
        self._plugins: dict[str, AtlasPlugin] = {p.name: p for p in plugins}
        self._health: dict[str, HealthStatus] = {}
        # Defensive — the namespace prevents collisions but assert anyway.
        seen: set[str] = set()
        for p in plugins:
            for t in p.get_tools():
                if not t.name.startswith(f"{p.name}."):
                    raise ValueError(f"tool {t.name!r} does not match plugin {p.name!r}")
                if t.name in seen:
                    raise ValueError(f"duplicate tool name: {t.name!r}")
                seen.add(t.name)

    async def warm(self) -> None:
        for name, p in self._plugins.items():
            try:
                self._health[name] = await p.health()
            except Exception as e:
                self._health[name] = HealthStatus(ok=False, detail=str(e))

    def list(self) -> list[PluginInfo]:
        return [
            PluginInfo(
                name=p.name, description=p.description,
                tool_count=len(p.get_tools()),
                health=self._health.get(p.name) or HealthStatus(ok=False, detail="not warmed"),
            )
            for p in self._plugins.values()
        ]

    def get_tool_schemas(self, enabled: list[str]) -> list[ToolSchema]:
        out: list[ToolSchema] = []
        for name in enabled:
            p = self._plugins.get(name)
            if p is None:
                continue   # silently skip unknown plugin names from the project column
            health = self._health.get(name) or HealthStatus(ok=False)
            if not health.ok:
                continue   # silently skip degraded plugins
            out.extend(p.get_tools())
        return out

    async def invoke(self, tool_name: str, args: dict) -> ToolResult:
        plugin_name, _, _rest = tool_name.partition(".")
        p = self._plugins.get(plugin_name)
        if p is None:
            return ToolResult(ok=False, error=f"unknown plugin: {plugin_name}")
        try:
            return await p.invoke(tool_name, args)
        except Exception as e:
            return ToolResult(ok=False, error=str(e))
```

### 3.3 Credential store

Postgres-backed, encrypted at rest with `cryptography.Fernet`:

```sql
CREATE TABLE plugin_credentials (
  id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  plugin_name  TEXT NOT NULL,
  account_id   TEXT NOT NULL DEFAULT 'default',
  ciphertext   BYTEA NOT NULL,
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE(plugin_name, account_id)
);
```

Constructor: `CredentialStore(session_factory, master_key: str | None)`.

Method semantics:
- **`set(plugin_name, account_id, payload: dict)`** — JSON-serialize, Fernet-encrypt, upsert (`ON CONFLICT (plugin_name, account_id) DO UPDATE SET ciphertext, updated_at = now()`).
- **`get(plugin_name, account_id) → dict`** — fetch, Fernet-decrypt, JSON-deserialize. Missing row → `CredentialNotFound`. Decrypt failure → `CredentialDecryptError`.
- **`list(plugin_name) → list[str]`** — returns `account_id`s only. Plaintext never crosses this method.
- **`delete(plugin_name, account_id)`** — deletes the row. Missing row is OK (idempotent delete).

Safe-mode: if `master_key is None`:
- `set` no-ops with a single WARN log per call.
- `get` raises `CredentialNotFound` (so plugins fall through to "no creds" behavior).
- `list` returns `[]`.
- `delete` no-ops.

This mode lets local dev boot without `ATLAS_PLUGINS__MASTER_KEY`; the API works, plugins are just unhealthy.

### 3.4 Anthropic tool-use loop

The existing chat WS handler (`apps/api/atlas_api/ws/chat.py`) already routes between providers. The change:

1. Load the project from Postgres; read `project.enabled_plugins`.
2. `tools = registry.get_tool_schemas(enabled=project.enabled_plugins)`.
3. Convert each `ToolSchema` to the Anthropic SDK shape via `_to_anthropic_tool(schema)` — a one-line mapping `{"name": s.name, "description": s.description, "input_schema": s.input_schema}`.
4. If provider is Anthropic, attach `tools=` to the SDK call. Otherwise, skip.
5. After the model returns, scan response content blocks for `tool_use`. If none, stream the text to WS and finish.
6. For each `tool_use` block:
   - Emit `{type: "tool_call", tool_name, call_id, started_at}` to the WS.
   - `result = await registry.invoke(tool_use.name, tool_use.input)`.
   - Emit `{type: "tool_result", tool_name, call_id, ok, duration_ms}`.
7. Append a follow-up message to the conversation history: `role="user"` with `content=[{type:"tool_result", tool_use_id:call_id, content:JSON.stringify(result)}, ...]` for each tool_use.
8. Re-send to Anthropic. Goto 5.
9. **Cap.** If the tool-turn counter (incremented on each turn that contained `tool_use`) hits 10, the 11th request is sent **without** `tools=` and with an extra system message: "Tool call limit reached; respond to the user without using tools." The model returns plain text.

The counter is per-WS-message (resets on each new user message).

### 3.5 WS event format for tool calls

```ts
// Already-existing message events: {type: "message_delta", text: "..."}, etc.

// New events for Plan 1:
{
  type: "tool_call",
  tool_name: "fake.echo",
  call_id: "tool_use_01abc...",   // Anthropic-provided id
  started_at: "2026-04-29T..."
}
{
  type: "tool_result",
  tool_name: "fake.echo",
  call_id: "tool_use_01abc...",
  ok: true,
  duration_ms: 234
}
```

The actual `value` of the tool result is NOT in the event — the model sees it next turn and synthesizes an answer. The user sees the synthesized answer in the regular message stream.

### 3.6 Per-project enablement

`projects.enabled_plugins TEXT[] NOT NULL DEFAULT '{}'`. Plan 1 doesn't ship any UI for editing this; opting in is via direct DB write or a future `PATCH /projects/{id}` body field. The chat handler reads the column when building the tool list.

## 4. Backend

### 4.1 Package layout

```
packages/atlas-plugins/
├── pyproject.toml
└── atlas_plugins/
    ├── __init__.py            # exports AtlasPlugin, ToolSchema, ToolResult, HealthStatus,
    │                          # PluginInfo, PluginRegistry, CredentialStore, FakePlugin,
    │                          # REGISTERED_PLUGINS
    ├── base.py                # ABC + dataclasses
    ├── registry.py            # PluginRegistry
    ├── credentials.py         # CredentialStore + Fernet wrapper
    ├── _fake.py               # FakePlugin (echo, fail, recurse tools)
    ├── errors.py              # CredentialNotFound, CredentialDecryptError
    └── tests/
        ├── conftest.py
        ├── test_base.py
        ├── test_registry.py
        ├── test_credentials.py
        └── test_fake.py
```

### 4.2 ORM

`packages/atlas-core/atlas_core/db/orm.py` gains:

```python
class PluginCredentialORM(Base):
    __tablename__ = "plugin_credentials"
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True,
                                     server_default=func.gen_random_uuid())
    plugin_name: Mapped[str] = mapped_column(Text, nullable=False)
    account_id: Mapped[str] = mapped_column(Text, nullable=False, server_default="default")
    ciphertext: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False,
                                                 server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False,
                                                 server_default=func.now(), onupdate=func.now())
    __table_args__ = (UniqueConstraint("plugin_name", "account_id"),)
```

`ProjectORM` gets:
```python
enabled_plugins: Mapped[list[str]] = mapped_column(
    ARRAY(Text), nullable=False, server_default="{}"
)
```

### 4.3 Migration `0007`

```python
def upgrade() -> None:
    op.create_table(
        "plugin_credentials",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("plugin_name", sa.Text(), nullable=False),
        sa.Column("account_id", sa.Text(), nullable=False, server_default="default"),
        sa.Column("ciphertext", sa.LargeBinary(), nullable=False),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.UniqueConstraint("plugin_name", "account_id"),
    )
    op.add_column(
        "projects",
        sa.Column("enabled_plugins", postgresql.ARRAY(sa.Text()),
                  nullable=False, server_default="{}"),
    )

def downgrade() -> None:
    op.drop_column("projects", "enabled_plugins")
    op.drop_table("plugin_credentials")
```

### 4.4 REST router

`apps/api/atlas_api/routers/plugins.py`:

| Method | Path | Body | Returns |
|--------|------|------|---------|
| GET    | `/plugins` | — | `list[PluginInfo]` |
| GET    | `/plugins/{name}/schema` | — | `list[ToolSchema]` (404 unknown plugin) |
| POST   | `/plugins/{name}/invoke` | `{tool_name, args}` | `ToolResult` (200; tool errors don't 5xx) |
| GET    | `/plugins/{name}/credentials` | — | `list[str]` (account_ids only) |
| POST   | `/plugins/{name}/credentials` | `{account_id?, payload}` | 201 `{account_id}` (503 if store in safe-mode) |
| DELETE | `/plugins/{name}/credentials/{account_id}` | — | 204 |

`account_id` defaults to `"default"` on POST. The `payload` field is `dict[str, Any]` — the plugin contract documents what each plugin's payload should contain (Plan 1 only documents FakePlugin's, which is empty `{}`).

### 4.5 Lifespan wiring

`apps/api/atlas_api/main.py`:

```python
master_key = os.getenv("ATLAS_PLUGINS__MASTER_KEY")
cred_store = CredentialStore(session_factory, master_key)
plugins = [PluginCls(cred_store) for PluginCls in REGISTERED_PLUGINS]
registry = PluginRegistry(plugins)
await registry.warm()
app.state.plugin_registry = registry
app.state.credential_store = cred_store
```

New deps in `apps/api/atlas_api/deps.py`:
```python
def get_plugin_registry(connection: HTTPConnection) -> PluginRegistry:
    return connection.app.state.plugin_registry

def get_credential_store(connection: HTTPConnection) -> CredentialStore:
    return connection.app.state.credential_store
```

### 4.6 Chat WS tool-use loop

Existing `apps/api/atlas_api/ws/chat.py` handler. The current code routes through the model registry and streams text deltas. The change:

1. After resolving the project, fetch `enabled_plugins`. Read the project once at message start.
2. If provider is Anthropic: build `tools = [_to_anthropic_tool(s) for s in registry.get_tool_schemas(project.enabled_plugins)]`. Otherwise tools = `None`.
3. Maintain a `messages: list[Message]` for the multi-turn loop within this single user message.
4. Loop: send messages + tools to Anthropic. Scan response.
   - If response has only text, stream it as today. Done.
   - If response has `tool_use` blocks: emit start/end events, dispatch to registry, append `tool_result` content blocks to messages, increment turn counter.
   - If turn counter == 10: send next request with `tools=None` and an extra system instruction. Stream final response. Done.

5. The cap is enforced as a function-local counter; persisted state isn't needed because each user message starts fresh.

The existing RAG-context injection (Plan 1 of Phase 1) stays wherever it is in the prompt assembly — tools are orthogonal to RAG context.

## 5. Frontend

### 5.1 Chat tool-call chip component

`apps/web/src/components/chat/tool-call-chip.tsx`:

```tsx
interface Props {
  toolName: string;
  status: "pending" | "ok" | "error";
  durationMs?: number;
}
```

Renders:
- pending: small pill with spinner + tool name in monospace
- ok: green checkmark + tool name + "(234ms)" in muted
- error: red X + tool name + duration

Color/icon scheme consistent with the existing chat-message component family.

### 5.2 Chat store changes

The existing chat store (Zustand or context — check `apps/web/src/stores/`) accumulates message events from the WS. Extend the assistant-message shape to optionally include an array of tool calls:

```ts
interface ToolCall {
  callId: string;
  toolName: string;
  status: "pending" | "ok" | "error";
  startedAt: string;
  durationMs?: number;
}

interface AssistantMessage {
  id: string;
  text: string;
  toolCalls: ToolCall[];   // pre-filled in the order they arrive; rendered above the text
  // ... existing fields
}
```

WS event handler:
- `tool_call` event: append a `ToolCall` to the current assistant message with status="pending".
- `tool_result` event: find the call by `call_id` in the current message; update status (ok→"ok", false→"error") and `durationMs`. If `call_id` doesn't match, log a console warning and ignore (don't crash on protocol drift).

### 5.3 Renderer

The existing assistant-message renderer gets a new sub-section above the text body:

```tsx
{msg.toolCalls.length > 0 && (
  <div className="mb-2 flex flex-wrap gap-1">
    {msg.toolCalls.map((tc) => (
      <ToolCallChip
        key={tc.callId}
        toolName={tc.toolName}
        status={tc.status}
        durationMs={tc.durationMs}
      />
    ))}
  </div>
)}
```

## 6. Testing

### 6.1 Backend unit tests

`atlas_plugins/tests/test_credentials.py`:
- Round-trip equality of `set` then `get`.
- Upsert: write A, write B with same key, get returns B.
- `list` returns account_ids only.
- `delete` removes; `get` after delete raises `CredentialNotFound`.
- Decrypt with wrong key raises `CredentialDecryptError`.
- Safe-mode: `set`/`delete` no-op with WARN, `get` raises `CredentialNotFound`, `list` returns `[]`.

`atlas_plugins/tests/test_base.py`:
- Subclass constructs cleanly.
- `_get_credentials` calls through to the store with the right kwargs.
- Default `health()` returns ok=False with no creds, ok=True with creds.

`atlas_plugins/tests/test_registry.py`:
- Construction with two well-formed plugins succeeds.
- Construction with a tool whose name doesn't match the plugin namespace raises `ValueError`.
- Construction with two plugins declaring the same tool name raises `ValueError`.
- `warm()` runs each plugin's health check; failure in one doesn't break others.
- `list()` returns `PluginInfo` per plugin with cached health.
- `get_tool_schemas(['fake'])` returns FakePlugin tools when health is ok.
- `get_tool_schemas(['fake'])` returns `[]` when health is degraded.
- `get_tool_schemas(['unknown'])` silently skips.
- `invoke('fake.echo', ...)` happy path.
- `invoke('unknown.foo', ...)` returns `ToolResult(ok=False, error="unknown plugin: ...")`.
- `invoke('fake.fail', ...)` returns `ToolResult(ok=False, error=...)` (plugin's invoke catches its own errors but the registry's catch is the safety net).

`atlas_plugins/tests/test_fake.py`:
- `fake.echo({"text":"x"})` → `{echo: "x"}`.
- `fake.fail({})` → ok=False with the documented error.
- `fake.recurse({"depth":N})` → returns `{recurse_again: True, depth: N+1}` (no special protocol; the chat-handler test mocks Anthropic to keep calling it).

### 6.2 Backend integration tests

`apps/api/atlas_api/tests/test_plugins_router.py`:
- `GET /plugins` returns FakePlugin info.
- `GET /plugins/fake/schema` returns 3 tool schemas (echo, fail, recurse).
- `GET /plugins/unknown/schema` returns 404.
- `POST /plugins/fake/invoke` echo happy path.
- `POST /plugins/fake/invoke` with unknown plugin name returns 200 + ok=False (consistent with the contract — tool errors don't bubble).
- Credentials CRUD: write, list (account_ids only), delete.
- With the credential store in safe-mode: GET list returns `[]`, POST returns 503 with `detail="credential_store_unavailable"`.

`apps/api/atlas_api/tests/test_ws_chat_tool_use.py`:
- Mock Anthropic provider. Project has `enabled_plugins=['fake']`.
- Test 1: model returns one `tool_use` for `fake.echo`. WS emits start+end events; final assistant text is delivered.
- Test 2: 10-turn cap. Mock anthropic to ALWAYS return a `tool_use` for `fake.recurse`. After 10 turns, handler injects forced-summary system instruction and re-sends without `tools`. Mock returns text. Assertions: 10 `tool_call`+`tool_result` event pairs, 1 final non-tool message, no additional tool calls.
- Test 3: tool failure. Mock returns `tool_use` for `fake.fail`. Handler emits `tool_result` with ok=false; model's next turn returns text.
- Test 4: provider is LM Studio. Tools NOT included; no tool-use loop.

### 6.3 Frontend tests

`apps/web/src/components/chat/tool-call-chip.test.tsx`:
- Pending state renders spinner + tool name.
- Ok state renders check + tool name + duration.
- Error state renders red X + tool name + duration.

Chat-store extension test (location depends on existing chat store layout):
- Receiving a `tool_call` event appends a `ToolCall` with status=pending.
- Receiving a `tool_result` event updates the matching `ToolCall` (status, duration).
- Mismatched `call_id` is logged + ignored.

### 6.4 Real-Anthropic acceptance (opt-in)

`apps/api/atlas_api/tests/test_ws_chat_tool_use_real.py`, gated on `ATLAS_RUN_ANTHROPIC_INTEGRATION=1`:
- Real Sonnet/Opus call.
- Sends "Use the fake.echo tool to repeat the word 'banana'."
- Asserts at least one `tool_call` event for `fake.echo` and the final text contains `banana`.

This is the load-bearing smoke that proves our `ToolSchema` → Anthropic dict conversion is correct. Skipping it without the env var is fine; running it before merge confirms end-to-end correctness.

### 6.5 Manual smoke (Plan 1 acceptance)

1. `GET /api/v1/plugins` returns FakePlugin with `health.ok=true`.
2. `POST /api/v1/plugins/fake/invoke` works for echo and fail.
3. Set `enabled_plugins=['fake']` on a real project via `psql`.
4. In the chat UI with Sonnet selected, send "Use the fake.echo tool to repeat 'hi'." Observe: chip appears, then result chip, then assistant text containing "hi".
5. With `enabled_plugins=[]`, same prompt → model says it can't use tools, no chips.
6. Restart API with `ATLAS_PLUGINS__MASTER_KEY` unset → API boots, `/plugins` shows FakePlugin with `health.ok=false`.

## 7. Acceptance criteria

1. `GET /api/v1/plugins` returns FakePlugin info with health.
2. `GET /api/v1/plugins/fake/schema` returns three tool schemas.
3. `POST /api/v1/plugins/fake/invoke {tool_name:"fake.echo", args:{text:"banana"}}` returns `ToolResult(ok=True, value={"echo":"banana"})`.
4. `POST /api/v1/plugins/fake/invoke {tool_name:"fake.fail", args:{}}` returns `ToolResult(ok=False, error="forced failure")`.
5. Credentials CRUD round-trips for FakePlugin.
6. With Anthropic provider + `enabled_plugins=['fake']` + project, chat invokes `fake.echo` end-to-end and emits the right WS events.
7. Tool-use loop hits 10-turn cap and the model returns a final non-tool summary.
8. Unknown plugin invocation returns `ToolResult(ok=False, ...)` not 5xx.
9. With `ATLAS_PLUGINS__MASTER_KEY` unset: API boots, FakePlugin health is degraded, the rest of the API works.

## 8. Risks and open items

- **Anthropic SDK version drift.** Tool-use is in the messages API and stable. We version-pin the SDK; major-version bumps revisit the conversion shim.
- **Tool-use loop runaway.** Mitigated by 10-turn cap (forced final summary).
- **Master key loss.** Documented at deploy time. v1 has no recovery flow.
- **Credential store decrypt errors mid-flight.** A user changes the master key and the old rows can no longer be decrypted. The plugin's invoke surface raises `CredentialDecryptError`, which the registry catches and converts to `ToolResult(ok=False, error=...)`. Loud but not crashy.
- **The chat WS handler is the most complex change.** Plan 1's tests exercise it with a fake Anthropic client; the real-API smoke (§6.4) is the proof.
- **Lazy credential reads + per-invoke Postgres round-trip.** Cheap (microseconds per Fernet decrypt + ~1ms per Postgres read). Acceptable at solo-user scale; if it bites, we add a 30s cache later.

## 9. Definition of Done

- [ ] `atlas-plugins` package merged with full test coverage on framework + FakePlugin.
- [ ] Migration `0007` applied; `plugin_credentials` table + `projects.enabled_plugins` column visible.
- [ ] `/api/v1/plugins/*` endpoints work and are tested.
- [ ] WS chat handler's tool-use loop works against fake Anthropic; real-Anthropic acceptance passes when run with the env var.
- [ ] Frontend `ToolCallChip` component renders all three states; chat store handles the new events.
- [ ] All 9 acceptance criteria pass on a manual smoke run.
- [ ] ruff + typecheck + lint clean.
- [ ] Code review approved (per workflow: Haiku implementer + Sonnet reviewers).
