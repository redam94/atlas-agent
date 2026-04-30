# ATLAS Phase 3 — Plugins (design)

**Date:** 2026-04-29
**Phase:** 3
**Plans:** 5
**Depends on:** Phase 1 (FastAPI + chat agent + provider abstraction) and Phase 2 (knowledge graph). All merged.
**Blocks:** Nothing in the public roadmap; Phase 4 (LangGraph + audio) doesn't depend on Phase 3.

---

## 1. Purpose

Phase 3 makes ATLAS a control surface for the user's external world. The chat agent gains the ability to invoke external services as tools (read code from GitHub, query GCP metrics, draft emails, post to Discord). The Discord bot becomes a second control surface so the user can talk to ATLAS from outside the web app. The framework is uniform — any future integration is a new file in `atlas-plugins/` rather than a special-case branch in the chat handler.

## 2. Scope

In scope (5 plans):
1. **Plugin framework** (`atlas-plugins` package, ABC, registry, encrypted credential store, REST endpoints, Anthropic tool-use chat wiring, FakePlugin for tests).
2. **Discord** plugin + bot process (slash commands, ingestion-complete notifications, agent-side `discord.send_message` tool).
3. **GitHub** plugin (PAT auth; code search, PR list/get, issue list/get/create, commit list, draft PR review).
4. **GCP** plugin + live dashboard panel (ADC service-account auth; metrics, logs, alerts, billing, GCE describe; React sparklines panel).
5. **Gmail** plugin (multi-account OAuth; per-account search/draft/send/label tools).

Out of scope for Phase 3:
- Calendar, Notion, web-search/fetch plugins. Already covered by Anthropic MCP tools available in the user's environment; rebuilding them as first-party plugins duplicates working tools.
- Knowledge-graph ingestion of plugin content (GitHub READMEs / PR descriptions / Gmail threads → chunks → graph). Interesting but scope-expanding; defer to a Phase 3b or Phase 4 plan.
- Streaming tool outputs. Plugins return whole results.
- Per-project plugin-toggle UI. Backend column lands in Plan 1; the UI is a Phase 4 polish.
- Confirmation UX via Discord reactions. Phase 3 uses token-based confirmation (return a draft + token; second call with the token executes).
- LM Studio tool-use. Local models without native tool-use stay toolless; direct `POST /plugins/{name}/invoke` is the escape hatch when the user really wants a local-only conversation to call a tool.
- Key rotation for the master encryption key. v1 ships single-key; rotating means re-encrypting all rows, deferred.

## 3. Architecture

```
                 ┌──────────────────────────────────────┐
                 │  apps/api (FastAPI)                  │
                 │                                      │
                 │  /api/v1/plugins                     │
                 │    GET  /              list          │
                 │    GET  /{n}/schema    tool schemas  │
                 │    POST /{n}/invoke    direct call   │
                 │    GET  /{n}/credentials  list ids   │
                 │    POST /{n}/credentials             │
                 │    DELETE /{n}/credentials/{aid}     │
                 │  /api/v1/oauth/gmail/{start,callback}│
                 │  /api/v1/internal/discord/chat       │
                 │  WS /chat (tool-use loop)            │
                 └──────────────┬───────────────────────┘
                                │
                                ▼
       ┌────────────────────────────────────────────┐
       │  atlas-plugins (NEW package)               │
       │                                            │
       │  AtlasPlugin (ABC)                         │
       │  PluginRegistry                            │
       │  CredentialStore (Fernet + Postgres)       │
       │                                            │
       │  discord/  github/  gcp/  gmail/  _fake/   │
       └─────┬──────────┬─────────┬─────────┬───────┘
             │          │         │         │
             ▼          ▼         ▼         ▼
       Discord      api.github cloud-     gmail
       bot          .com       monitoring api
                               +logging
                               +billing
                               +compute

       ┌──────────────────────────┐
       │  apps/discord-bot (NEW)  │ ── separate compose service
       │  discord.py event loop   │    calls /api/v1/internal/...
       │  slash commands          │
       └──────────────────────────┘
```

### 3.1 Plugin contract

```python
class AtlasPlugin(ABC):
    name: str
    description: str

    @abstractmethod
    def get_tools(self) -> list[ToolSchema]:
        """Return JSON-Schema tool definitions in Anthropic tool-use format."""

    @abstractmethod
    async def invoke(self, tool_name: str, args: dict) -> ToolResult:
        """Execute the tool. Returns ToolResult(ok, value | error)."""

    async def health(self) -> HealthStatus:
        """Quick liveness check; default is to return ok if creds present."""
```

`ToolSchema` is the Anthropic tool-use shape (`name, description, input_schema`). `ToolResult` is `{ok: bool, value: Any, error: str | None}` — never raises out to the registry; failures live inside the result so the model can see them.

### 3.2 Registry

`PluginRegistry` is constructed once in `lifespan` from a `CredentialStore` plus the list of registered plugin classes. For each plugin class:
- Construct the plugin with the credential store.
- Call `await plugin.health()`. If it raises or returns degraded, the plugin is recorded but excluded from `get_tool_schemas()` until the next reload.
- If `ATLAS_PLUGINS__MASTER_KEY` is missing, the registry skips ALL plugins (one big WARN). API still boots.

`registry.invoke(tool_name, args)` splits on the first `.`, looks up the plugin, dispatches. Unknown tool → `ToolResult(ok=False, error="unknown tool")`. Unknown plugin → 404 from the route handler.

### 3.3 Credential store

Postgres-backed, encrypted at rest with `cryptography.Fernet`.

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

`account_id` is what enables Gmail multi-account. Single-credential plugins (Discord, GitHub, GCP) use `account_id='default'`. Plaintext credentials are JSON dicts before encryption. The store exposes `set(plugin_name, account_id, payload: dict)`, `get(plugin_name, account_id) → dict`, `list(plugin_name) → list[str]` (account_ids only — no plaintext), `delete(plugin_name, account_id)`.

Master key is `ATLAS_PLUGINS__MASTER_KEY` (Fernet 32-byte URL-safe base64). Missing → registry skips all plugins; getting + setting are still callable but `set` writes garbage that nothing can read (acceptable failure mode for misconfigured local dev).

### 3.4 Per-project plugin enablement

```sql
ALTER TABLE projects ADD COLUMN enabled_plugins TEXT[] NOT NULL DEFAULT '{}';
```

Default empty. Each project opts plugins in. Chat handler reads this column when building the tool list. v1 has no UI — toggling is a direct DB write or a `PATCH /projects/{id}` body field. UI is a Phase 4 concern.

### 3.5 Anthropic tool-use loop

When the active model is served by the Anthropic provider (provider key = `anthropic` in the model registry from Phase 1):

1. Build tools = `registry.get_tool_schemas(enabled=project.enabled_plugins)`.
2. Send the user message + tools to Anthropic.
3. If response contains `tool_use` blocks:
   a. For each tool_use, `await registry.invoke(tool_use.name, tool_use.input)`.
   b. Send a follow-up message with `tool_result` content blocks for each.
   c. GOTO 3 (depth-incremented).
4. Cap at 10 tool turns. On the 11th, force a non-tool response with a system message: "Tool depth exceeded; respond without further tool calls."

Each tool call streams a small status event to the WS client (`{type: "tool_call", name, started_at}` → `{type: "tool_result", name, ok, duration_ms}`) so the FE can show what's happening. This is wire-format only — the FE rendering of these events lands in the same plan that ships the tool-use loop (Plan 1).

LM Studio path is unchanged: no tools.

### 3.6 Discord bot architecture

Separate process under `apps/discord-bot/`. discord.py runs the gateway connection. Slash commands hit `http://api:8000/api/v1/internal/discord/chat` over the docker-compose internal network with a shared secret in the `X-Internal-Secret` header. The internal route is mounted under a separate FastAPI router that asserts the header before accepting the request, and is NOT included when ATLAS is exposed publicly.

The bot also subscribes to a Postgres `LISTEN ingestion_complete` channel; `IngestionService` issues `NOTIFY ingestion_complete '<job_id>'` on completion (Plan 2 wires both sides).

The agent-side `discord.send_message(channel_id, body)` plugin tool calls back to the bot via a separate internal endpoint (`/api/v1/internal/discord/send`) which the bot exposes — symmetric to the slash-command path but in reverse. This is the only API the bot exposes; it's also internal-only.

## 4. Cross-cutting decisions

These are LOCKED across all 5 plans. Do NOT re-litigate per plan.

- **Plugin loading is conditional on credentials at startup.** Missing creds → plugin omitted from registry, WARN logged, API boots normally.
- **Encryption: Fernet symmetric, single master key in env (`ATLAS_PLUGINS__MASTER_KEY`).** Key rotation deferred to Phase 4.
- **Credential storage: encrypted `plugin_credentials` Postgres table** with `(plugin_name, account_id)` unique. `account_id` defaults to `'default'`; multi-account plugins use real identifiers.
- **Tool naming: `<plugin>.<tool>`.** Registry uses `split(".", 1)`. No nested namespaces.
- **Anthropic tool-use only; LM Studio toolless.** Direct `POST /plugins/{n}/invoke` is the escape hatch for tool calls outside chat.
- **Per-project enablement via `projects.enabled_plugins TEXT[]`.** Default empty (no plugins until opted in).
- **Plugin tools are best-effort, not transactional.** Failures return `ToolResult(ok=False, error=...)`, never raise as HTTP 500.
- **Confirmation gates for write ops are token-based** (Gmail send, GitHub create_issue/submit_review, Discord agent-initiated send): first call returns draft + token, second call with the token executes. No reaction-based UX in v1.
- **No knowledge-graph ingestion of plugin content in Phase 3.** Defer.
- **No streaming tool outputs.** Plugins return whole results.
- **Tool-use loop cap: 10 turns.** Forced non-tool response on the 11th.

## 5. Plan-by-Plan Sketches

### 5.1 Plan 1 — Plugin Framework

**Goal:** `atlas-plugins` package + ABC + registry + credential store + REST endpoints + Anthropic tool-use chat wiring. Ships with `FakePlugin` only (no real integrations).

**Backend:**
- New `atlas-plugins` package + `cryptography` dependency.
- Alembic 0007: `plugin_credentials` table + `projects.enabled_plugins TEXT[]`.
- `AtlasPlugin` ABC, `ToolSchema`, `ToolResult`, `HealthStatus` dataclasses.
- `PluginRegistry`, `CredentialStore`, `FakePlugin`.
- `apps/api/atlas_api/routers/plugins.py` (6 endpoints).
- WS chat handler: detect Anthropic provider → tool-use loop with 10-turn cap.

**Out of scope:** any real plugin, the per-project plugin-toggle UI.

**Acceptance:** `GET /plugins` returns `FakePlugin`; `POST /plugins/fake/invoke {tool_name:"fake.echo", args:{text:"hi"}}` returns `{ok:true, value:{echo:"hi"}}`; with FakePlugin enabled on a project and Anthropic active, "echo banana" in chat causes the model to call `fake.echo(text="banana")` and respond with the echo result.

### 5.2 Plan 2 — Discord Plugin + Bot Process

**Goal:** discord.py bot as a separate compose service. Slash commands `/atlas ask`, `/atlas ingest`, `/atlas summarize`, `/atlas status`. Ingestion-complete notifications.

**Backend:**
- `apps/discord-bot/` — new top-level app, separate Dockerfile + compose service.
- `/api/v1/internal/discord/chat` (internal router; shared-secret guard).
- `IngestionService` issues `NOTIFY ingestion_complete '<job_id>'` on completion. Bot LISTENs.
- `atlas_plugins/discord/plugin.py` exposing `discord.send_message(channel_id, body)` and `discord.list_recent_messages(channel_id, limit)` for the agent. Bot exposes the symmetric `/api/v1/internal/discord/send` endpoint.
- Credential schema: `{bot_token, internal_api_secret, default_channel_id?}`.

**Acceptance:** `/atlas ask "what's in project X"` runs the agent and posts a chunked reply; ingestion-complete notification arrives in the configured channel; chat agent can call `discord.send_message` to push proactively.

### 5.3 Plan 3 — GitHub Plugin

**Goal:** Read-mostly GitHub via PAT.

**Backend:**
- `atlas_plugins/github/plugin.py` using `httpx.AsyncClient`.
- 8 tools: `search_code`, `list_prs`, `get_pr`, `list_issues`, `get_issue`, `create_issue`, `list_commits`, `draft_pr_review`.
- Credential schema: `{token, default_org?, default_repo?}`.
- `create_issue` and submitting a drafted review require token-based confirmation.

**Out of scope:** GitHub App auth (PAT only); ingestion of GitHub content into the graph.

**Acceptance:** `github.list_prs(repo="redam94/atlas-agent", state="open")` returns the open PRs; chat agent answers "what PRs are open?" by invoking the tool.

### 5.4 Plan 4 — GCP Plugin + Live Dashboard Panel

**Goal:** Read-only GCP visibility + live React panel polling at 60s.

**Backend:**
- `atlas_plugins/gcp/plugin.py` using `google-cloud-monitoring`, `google-cloud-logging`, `google-cloud-billing`, `google-cloud-compute`.
- Auth: service-account JSON stored in the credential store (encrypted).
- 7 tools: `get_metrics`, `tail_logs`, `list_alerts`, `get_active_incidents`, `get_billing_summary`, `list_services`, `describe_instance`.
- Cost-control: hard limits on rows + request timeout; no cursor pagination.

**Frontend:**
- New panel/page under `/projects/:id/dashboard` (or extend project shell). Polls `/api/v1/plugins/gcp/invoke` every 60s. Sparklines via `recharts` (new dep).

**Acceptance:** `gcp.get_active_incidents` returns real data; dashboard sparklines render real metrics; chat answers "what's spend this month" by calling `get_billing_summary`.

### 5.5 Plan 5 — Gmail Plugin (multi-account)

**Goal:** Multi-account Gmail with per-account OAuth refresh tokens; account chosen explicitly per tool call.

**Backend:**
- `atlas_plugins/gmail/plugin.py` using `google-api-python-client` Gmail v1.
- OAuth flow at `apps/api/atlas_api/routers/oauth.py`: `/api/v1/oauth/gmail/start` (returns Google consent URL) and `/api/v1/oauth/gmail/callback` (exchanges code → refresh token, stores with `account_id = email`).
- 6 tools: `list_accounts`, `search_threads(account, ...)`, `get_thread(account, ...)`, `draft_email(account, ...)`, `send_draft(account, ...)`, `label_thread(account, ...)`.
- `send_draft` uses token-based confirmation.

**Frontend:** small "Gmail accounts" settings page with a "Connect another account" button.

**Acceptance:** Two accounts registered; per-account search returns only that account's threads; chat answers "draft a reply to last week's invoice" using the right account.

## 6. Risks and Open Items

- **Master key loss.** Lose `ATLAS_PLUGINS__MASTER_KEY` → all credentials unrecoverable. Document at deploy time. v1 has no recovery.
- **Tool-use loop runaway.** Mitigated by the 10-turn cap (Plan 1).
- **Discord rate limits.** discord.py handles slash-command limits; agent-initiated proactive sends respect 5/sec/channel. Wrapper logs and silently drops if exceeded.
- **GCP API costs.** Cloud Logging in particular bills per query. Tool description documents the cost so the model is aware.
- **Gmail OAuth refresh token expiration.** Google may revoke after 6 months inactivity or password change. Plugin emits a clear error; UI prompts re-auth. No proactive refresh in v1.
- **Cross-plugin tool name collisions.** Avoided by `<plugin>.<tool>` namespace; registry asserts uniqueness defensively.
- **`enabled_plugins` array column on `projects`.** Already using `notes.mention_entity_ids` array — no new infra concern.

### Open decisions deferred to per-plan brainstorms

- Plan 2: Discord internal-secret rotation strategy (v1 = static env var).
- Plan 3: GitHub App vs PAT (v1 = PAT).
- Plan 4: Project-list management (v1 = whatever the service-account JSON has access to; tool calls take `project` as an arg).
- Plan 5: Google OAuth client registration and storage of client_id/client_secret (env vs credential store).

## 7. Definition of Done (Phase 3)

- [ ] `atlas-plugins` package merged with `AtlasPlugin`, `PluginRegistry`, `CredentialStore`, `FakePlugin`, full test coverage on the framework.
- [ ] `plugin_credentials` table + `projects.enabled_plugins` column shipped via Alembic.
- [ ] `/api/v1/plugins/*` endpoints serve list / schema / invoke / credentials CRUD.
- [ ] Anthropic tool-use loop wired in chat WS handler with 10-turn cap.
- [ ] Discord bot ships as a separate compose service; slash commands work; ingestion-complete notification fires.
- [ ] GitHub plugin: 8 read-mostly tools work against a real repo.
- [ ] GCP plugin: 7 read-only tools work against a real GCP project; React dashboard panel renders sparklines.
- [ ] Gmail plugin: OAuth flow registers two accounts; per-account tools work; multi-account smoke passes.
- [ ] Each plan's PR is independently reviewable and shippable.

## 8. Roadmap context

After Phase 3 closes, roadmap moves to Phase 4 (LangGraph + audio assistant) and Phase 5 (Discord polish + Terraform infra). Phase 3b (knowledge-graph ingestion of plugin content) and a per-project plugin-toggle UI are floating items that can land between phases as needed.
