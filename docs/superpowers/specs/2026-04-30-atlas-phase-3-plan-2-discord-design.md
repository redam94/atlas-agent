# Phase 3 Plan 2 — Discord Bot + Plugin (Design)

**Date:** 2026-04-30
**Status:** Design approved; awaiting plan-doc authoring
**Phase:** 3 (Plugins)
**Predecessor:** Plan 1 — Plugin Framework (PR #14, merged 2026-04-30)
**Phase spec:** `docs/superpowers/specs/2026-04-29-atlas-phase-3-plugins-design.md`

## 1. Goal

Ship a Discord integration consisting of a separate `apps/discord-bot/` compose service plus a `discord` plugin in `atlas-plugins`. After this plan ships, Matt can:

1. Type `/atlas ask "..."` in his personal Discord guild and get the full ATLAS chat agent's reply (chunked into followups).
2. Type `/atlas ingest url:<url>` and have the bot reply when ingestion completes.
3. Type `/atlas status` and see a non-LLM health snapshot.
4. Use the React chat UI; when the agent calls `discord.send_message`, get a token-gated preview that posts to Discord on confirm.

## 2. Constraints (locked at phase level)

These come from `2026-04-29-atlas-phase-3-plugins-design.md` and are not re-litigated here:

- Plugins load conditionally on credentials at startup; missing creds = WARN log, omitted from registry, API still boots.
- Encryption: Fernet symmetric, single master key in env (`ATLAS_PLUGINS__MASTER_KEY`).
- Credential storage: encrypted `plugin_credentials` Postgres table.
- Tool naming: `discord.send_message` internal; encoded to `discord__send_message` on the Anthropic wire.
- Anthropic tool-use only; LM Studio path is toolless.
- Per-project enablement via `projects.enabled_plugins TEXT[]`.
- Tool-use loop cap: 10 turns; the 11th call drops `tools=` and forces a final summary.
- Tool errors return `ToolResult(error=str(e))`, never 5xx.
- Confirmation gates for write ops are token-based (Redis, 5-min TTL).

## 3. Plan-level locked decisions

Each numbered item is a brainstorm decision that does NOT get re-litigated in plan authoring or per-task scoping.

### 3.1 Single default project, single guild

- `ATLAS_DISCORD__DEFAULT_PROJECT_ID` (env, required) — every slash command operates against this project.
- `ATLAS_DISCORD__GUILD_ID` (env, required) — slash commands are guild-scoped to this single guild only. Instant updates during dev; bot rejects commands from other guilds.
- No per-channel project bindings, no `discord_channel_bindings` table. Defer to Phase 3b if needed.
- No bot-side user allowlist; rely on guild membership as the access control boundary.

### 3.2 Slash command surface

Three commands only:

- `/atlas ask prompt:<string>` — runs the agent, posts chunked reply.
- `/atlas ingest url:<string>` — queues a URL for ingestion. URL only; no file attachments, no message-link ingestion.
- `/atlas status` — non-LLM health snapshot.

`/atlas summarize` is dropped; it's a thin wrapper over `/atlas ask` and adds no value.

### 3.3 Ingestion-complete notification — hybrid channel + polling

- New columns on `ingestion_jobs`: `discord_channel_id TEXT NULL`, `notified_at TIMESTAMPTZ NULL`.
- `/atlas ingest` writes `discord_channel_id = <interaction.channel_id>` on the job row.
- Bot has a background `notification_poller` task that runs every 10s, queries `GET /internal/discord/jobs/pending`, and posts a notification per row to either `discord_channel_id` (if set) or `ATLAS_DISCORD__NOTIFY_CHANNEL_ID` (fallback). Marks each row `notified_at = now()` via `POST /internal/discord/jobs/{id}/mark_notified` after posting.
- **No LISTEN/NOTIFY.** Polling is the entire mechanism. 10s latency is acceptable.
- **Restart freshness filter:** the pending-jobs query filters `completed_at >= now() - interval '10 minutes'`. Older completed-unnotified jobs are marked `notified_at = now()` silently in a separate request the bot makes on startup, preventing flood after extended downtime.

### 3.4 `/atlas ask` — chunked followups

- `interaction.response.defer()` to claim the 3-second response window.
- POST `/api/v1/internal/discord/chat` with `{project_id, prompt}` + shared-secret header.
- API returns the final assembled text (string) — no streaming, no event semantics over the wire.
- Bot chunks the text into ≤1900-char segments at sentence/paragraph boundaries (newline → period → fallback to hard split). Each chunk posts as `interaction.followup.send(...)` sequentially.
- Stateless: each `/atlas ask` is a fresh single-turn agent call. No session row written, no per-channel conversational memory. Defer per-channel sessions to Phase 3b.
- API timeout: 60 seconds. On timeout or 5xx, bot posts a single `❌ <reason>` message.

### 3.5 Internal API auth — single shared secret

- One env var `ATLAS_DISCORD__INTERNAL_SECRET`, present in both `api` and `discord-bot` compose services.
- Both directions (bot→API, API→bot) use the header `X-Internal-Secret: <value>`.
- The API mounts the internal router under `/api/v1/internal/discord/*` and applies a router-level dependency that 401s on mismatch.
- The bot mounts a small FastAPI app on a configurable port for the symmetric `/internal/discord/send` endpoint (and applies the same guard).
- No rotation, no asymmetric secrets. Phase 4 problem.

### 3.6 Agent runner extraction

The Anthropic tool-use loop currently lives inside `apps/api/atlas_api/ws/chat.py`. Plan 2 extracts it:

- New module `apps/api/atlas_api/services/agent_runner.py`.
- `run_turn(project_id, prompt, *, interactive: bool) -> AsyncIterator[AgentEvent]` — async generator yielding typed events (`text_delta`, `tool_call`, `tool_result`, `final_text`, `error`).
- `run_turn_collected(project_id, prompt, *, interactive: bool) -> str` — thin collector that drains the generator and returns the concatenated final text.
- WS handler subscribes to `run_turn` events to emit its existing `chat.delta` / `chat.tool_use` / `chat.tool_result` WS frames. WS-side tests from Plan 1 remain green.
- Internal HTTP handler awaits `run_turn_collected` for the final string.
- The `interactive` flag flows via a `contextvars.ContextVar` set at the entry of each runner call, so plugins can read it without it being a tool argument.

### 3.7 `discord.send_message` agent tool

- Single tool: `discord.send_message(body: str)`. No `channel_id` parameter — the plugin always sends to `default_channel_id` from the credential payload. This avoids the model needing channel-discovery (which would require the dropped `list_recent_messages` tool or system-prompt pollution).
- Tool result on success: `{posted: true, message_id: <str>}`. On error: `ToolResult(error=str(e))`.

### 3.8 Confirmation gate — interactive-only

- `discord.send_message` honors the token-based gate **only when `interactive=True`** (set by the WS chat path).
- Interactive flow: first call → `{preview: {channel_id, body}, draft_token}`, stores `(token, body)` in Redis with 300s TTL. Second call with `{confirm_token}` → pops Redis entry, POSTs to bot, returns `{posted: true, message_id}`.
- Non-interactive flow (`/atlas ask`): `interactive=False`, gate is bypassed, plugin POSTs directly. Rationale: the gate's purpose is human review through a UI; in `/atlas ask` there's no human-in-the-loop, and the model would just re-invoke with the token, defeating the gate.

### 3.9 Discord intents and rate limiting

- Default intents only. No `MESSAGE_CONTENT` privileged intent. (Consequence: `discord.list_recent_messages` is **dropped from v1**; only `send_message` ships.)
- Outbound rate limiting: rely on discord.py's built-in queue. If 429s persist, bot returns 503 to API, plugin returns `ToolResult(error=...)`.
- Long bodies: bot chunks `body` > 1900 chars in `/internal/discord/send` before posting, reusing the same chunker as `/atlas ask` reply.

### 3.10 Credentials

- `('discord', 'default')` row in `plugin_credentials`.
- Schema: `{bot_token, default_channel_id}`. Both required for `DiscordPlugin.health()` to return ok.
- The shared `internal_api_secret` is **not** in the credential row — it lives in env (`ATLAS_DISCORD__INTERNAL_SECRET`) on both the API and bot processes. The API router reads it for inbound bot→API validation, the bot reads it for inbound API→bot validation, and the plugin reads it for the outbound API→bot POST. Single source on each side, no duplication between env and DB.

## 4. Architecture

```
                 ┌──────────────────────────────────────┐
                 │          Discord guild               │
                 │  /atlas ask  /atlas ingest  status   │
                 └──────────────┬───────────────────────┘
                                │  (slash commands, gateway)
                                ▼
   ┌────────────────────────────────────────────────────────────┐
   │ apps/discord-bot (new compose service)                     │
   │ - discord.py client (default intents, guild-scoped sync)   │
   │ - command handlers: ask / ingest / status                  │
   │ - notification_poller (10s tick)                           │
   │ - mini FastAPI app for /internal/discord/send              │
   └─────┬───────────────────────────────────────┬──────────────┘
         │                                       ▲
   POST  │ /api/v1/internal/discord/chat         │ POST /internal/discord/send
         │ /api/v1/knowledge/ingest              │
         │ /api/v1/internal/discord/jobs/*       │
         ▼                                       │
   ┌────────────────────────────────────────────────────────────┐
   │ apps/api (existing)                                        │
   │ + routers/_internal/discord.py (chat, jobs, status)        │
   │ + services/agent_runner.py (extracted from ws/chat.py)     │
   │ + atlas_plugins/discord/plugin.py (outbound POST to bot)   │
   └─────┬─────────────────────────────────┬────────────────────┘
         │                                 │
         ▼                                 ▼
   ┌──────────────┐                 ┌─────────────┐
   │  Postgres    │                 │   Redis     │
   │ ingestion_jobs│                 │ confirm_tokens│
   │ + discord_   │                 │ (TTL 300s)  │
   │   channel_id │                 └─────────────┘
   │ + notified_at│
   └──────────────┘
```

### 4.1 Components

**`apps/discord-bot/`** (new):
- `pyproject.toml` — `uv`-managed; deps: `discord.py`, `httpx`, `fastapi`, `uvicorn`, `pydantic-settings`, `atlas-core` (workspace).
- `Dockerfile` — multi-stage like the api Dockerfile, copies `atlas-core` package.
- `atlas_discord_bot/__main__.py` — entry; wires intents, registers commands, starts poller + uvicorn for inbound.
- `atlas_discord_bot/commands/{ask,ingest,status}.py` — one file per slash command.
- `atlas_discord_bot/poller.py` — `notification_poller` task.
- `atlas_discord_bot/internal_app.py` — FastAPI app with `/internal/discord/send`.
- `atlas_discord_bot/chunker.py` — sentence/paragraph-boundary text chunker (≤1900 chars), shared by `/atlas ask` reply and `/internal/discord/send`.
- `atlas_discord_bot/api_client.py` — typed httpx wrapper for API calls.
- `atlas_discord_bot/settings.py` — pydantic-settings; fail-fast on missing required env vars.

**`apps/api/atlas_api/services/agent_runner.py`** (new):
- `run_turn(...) -> AsyncIterator[AgentEvent]`
- `run_turn_collected(...) -> str`
- Reads `interactive` from contextvar set inside these functions.
- Existing 10-turn cap, tool_calls JSONB persistence, and Anthropic-name encoding all preserved.

**`apps/api/atlas_api/routers/_internal/discord.py`** (new):
- `POST /api/v1/internal/discord/chat` → calls `run_turn_collected(interactive=False)`, returns `{text}`.
- `GET /api/v1/internal/discord/jobs/pending` → returns rows where `status='completed' AND notified_at IS NULL AND completed_at >= now() - interval '10 minutes'`.
- `POST /api/v1/internal/discord/jobs/{id}/mark_notified` → sets `notified_at = now()`.
- `POST /api/v1/internal/discord/jobs/mark_stale_notified` (startup helper) → marks all `status='completed' AND notified_at IS NULL AND completed_at < now() - interval '10 minutes'` as notified silently.
- `GET /api/v1/internal/discord/status` → non-LLM health snapshot for `/atlas status`.
- Router-level dependency enforces `X-Internal-Secret`.

**`packages/atlas-plugins/atlas_plugins/discord/plugin.py`** (new):
- `class DiscordPlugin(AtlasPlugin)`.
- `name = "discord"`.
- `tools()` → list with single `ToolSchema` for `send_message`.
- `health()` → ok iff all three credential fields present.
- `invoke("send_message", args)` — reads `interactive` contextvar; if interactive, runs gate; if non-interactive, POSTs `/internal/discord/send` immediately.
- Appended to `REGISTERED_PLUGINS` in `atlas_plugins.registry`.

**`apps/api/atlas_api/ws/chat.py`** (modified):
- Replace inline tool-use loop with `agent_runner.run_turn(..., interactive=True)`. Subscribe to events, emit existing WS frames.
- Set `interactive=True` contextvar at entry.

### 4.2 Database changes

Alembic migration `0008_discord_columns.py`:
- `ingestion_jobs.discord_channel_id TEXT NULL`
- `ingestion_jobs.notified_at TIMESTAMPTZ NULL`
- Index on `(status, notified_at)` for the poll query (`status='completed' AND notified_at IS NULL`).

No new tables. No changes to `plugin_credentials`.

### 4.3 Compose changes

`infra/docker-compose.yml` adds:

```yaml
  discord-bot:
    build:
      context: ..
      dockerfile: apps/discord-bot/Dockerfile
    container_name: atlas-discord-bot
    restart: unless-stopped
    env_file: ../.env
    environment:
      ATLAS_DISCORD__API_BASE_URL: http://api:8000
    depends_on:
      api:
        condition: service_started
    expose:
      - "8001"   # internal_app, reachable only by API container on the compose network
```

Bot's internal FastAPI port is `expose`d (compose-network only), not published to the host — the API→bot POST happens within the docker network at `http://discord-bot:8001`. No host port needed. Bot service depends on `api` starting; API healthcheck is out of scope for this plan and `service_started` is sufficient for v1 (bot will retry transient API failures).

## 5. Data flow (summarized — full flows in section 7)

### `/atlas ask`
Slash → defer → POST `/internal/discord/chat` → `run_turn_collected(interactive=False)` → return text → bot chunks → followup.send.

### `/atlas ingest`
Slash → defer → POST `/api/v1/knowledge/ingest` (with `discord_channel_id`) → bot replies "queued" → poll picks up completion → bot posts to channel.

### Agent calls `discord.send_message` (WS chat)
Anthropic tool_use → plugin.invoke (interactive=True) → first call returns draft+token → React UI shows preview → user confirms → second call POSTs `/internal/discord/send` → bot chunks + posts.

### Agent calls `discord.send_message` (`/atlas ask`)
Anthropic tool_use → plugin.invoke (interactive=False) → POSTs `/internal/discord/send` immediately → bot posts → returns `{posted, message_id}` to model.

## 6. Error handling

| Scenario | Behavior |
|---|---|
| Bot env var missing | Fail-fast on startup; container exits with explicit log |
| API unreachable on `/atlas ask` | Bot posts `❌ chat failed: <status>` or `❌ chat timed out` |
| API 10-turn cap hit | Existing Plan 1 behavior; user sees forced summary |
| Slash interaction expired (>15min) | Log error; no further user-visible action |
| Ingestion job failed | Polling sees `status='failed'`; bot posts `❌ ingestion failed: <error>` |
| `discord.send_message` body > 1900 chars | Bot chunks before sending |
| `discord.send_message` Discord 429 persistent | discord.py auto-retries; if fails, bot returns 503; plugin returns `ToolResult(error=...)` |
| Confirmation token expired/reused | Plugin returns `ToolResult(error="confirm_token expired or invalid")` |
| Bot DB poll query failure | Log WARN; skip tick; retry in 10s; no crash |
| Bot crash mid-post | On restart, 10-min freshness filter catches recent jobs (notified_at not yet set) |
| Bot down >10min | Older completed jobs are marked stale-notified silently on next bot startup; user does not get a flood |
| Missing discord credentials at API startup | `DiscordPlugin.health()` returns not-ok; plugin omitted from registry; WARN logged; API boots normally |

## 7. Test strategy

### 7.1 Unit tests

- `packages/atlas-plugins/atlas_plugins/tests/test_discord.py`
  - Tool schema shape.
  - `health()` with full creds → ok; with any field missing → not ok.
  - `send_message` with `interactive=False` → POSTs directly, returns `{posted, message_id}`.
  - `send_message` interactive first-call → returns `{preview, draft_token}`, stores in Redis.
  - `send_message` interactive second-call valid token → pops Redis, POSTs, returns success.
  - Expired token → `ToolResult(error="confirm_token expired or invalid")`.
  - Reused token → same error.
  - Mock bot HTTP via `httpx.MockTransport`. Redis via `fakeredis`.

- `apps/api/atlas_api/tests/test_agent_runner.py`
  - Reuse Plan 1's FakeProvider scripted-turns fixture.
  - `run_turn` event sequence matches the WS handler's pre-refactor output (regression test).
  - `run_turn_collected` returns concatenated final text only.
  - 10-turn cap drops `tools=` on the 11th call (carryover from Plan 1's T11 lesson).
  - `interactive` contextvar is set inside the runner and visible to plugins.

- `apps/api/atlas_api/tests/test_internal_discord.py`
  - 401 without `X-Internal-Secret`.
  - 200 with correct secret.
  - `/internal/discord/chat` happy path mocks `run_turn_collected`.
  - `/internal/discord/jobs/pending` freshness filter.
  - `mark_notified` updates row; `mark_stale_notified` updates and does not return rows.

- `apps/discord-bot/tests/test_chunker.py`
  - Chunker splits at paragraph > sentence > hard split, all ≤1900 chars.
  - Empty / whitespace-only / single-char inputs.

- `apps/discord-bot/tests/test_commands.py`
  - dpytest (or hand-rolled fakes) for each command handler.
  - `/atlas ask` happy path: API mocked, asserts `interaction.followup.send` called per chunk.
  - `/atlas ingest` posts queued message with job ID.
  - `/atlas status` calls `/healthz` + status endpoint, formats embed.

- `apps/discord-bot/tests/test_poller.py`
  - Mock httpx, fast-forward poll loop.
  - Posts only fire for jobs returned by `/jobs/pending` (freshness already enforced server-side).
  - `mark_notified` called per posted job.

### 7.2 Integration smoke (manual)

Documented as a checklist in the plan doc. Real bot token, real Anthropic key, real test guild:

1. `/atlas status` returns embed with all green.
2. `/atlas ask "hello"` → followup with response.
3. `/atlas ask` with a long prompt that produces > 1900-char reply → multiple followups.
4. `/atlas ingest url:<some short URL>` → "queued" reply, then "✅ ingested" within ~10s.
5. From React chat with DiscordPlugin enabled → ask agent to send a Discord message → preview appears in UI → confirm → message arrives in Discord.
6. From `/atlas ask` → ask agent to send a Discord message → message arrives in Discord without confirmation prompt.

### 7.3 Out of scope

- No real Discord API in CI.
- No load testing.
- No cross-guild testing (bot rejects other guilds by design).

## 8. Risks

- **Agent runner refactor is the highest-risk change.** It touches Plan 1's WS chat handler. Mitigation: regression test in `test_agent_runner.py` validates event sequence against Plan 1's existing fixture; WS-handler tests from Plan 1 remain unmodified and must stay green.
- **Bot token leakage.** `bot_token` lives in `plugin_credentials` (encrypted) for the API and in env for the bot. Bot env file should be tightly scoped. Documented in deploy notes.
- **Polling interval blocked behind a long-running query.** A slow `jobs/pending` query could stretch the 10s tick. Mitigation: index on `(status, notified_at)`; query has a freshness filter so the working set is tiny.
- **discord.py library version churn.** Pin to a tested version in `pyproject.toml`. Match the version Matt has tested with locally.
- **Confirmation gate UI render.** Plan 1 already lands `ToolCallChip` rendering for tool_use events; the preview comes through as a regular tool_result with `result.preview` populated. No Plan 2 frontend work, but the Phase 4 UI polish should expose a confirm button.

## 9. Definition of Done

- [ ] `apps/discord-bot/` ships as a new compose service with Dockerfile, pyproject.toml, settings module, and the four code areas (commands, poller, internal_app, chunker).
- [ ] `agent_runner.py` extracted; WS chat handler refactored to use it; existing Plan 1 WS tests pass unchanged.
- [ ] `/api/v1/internal/discord/{chat,jobs/pending,jobs/{id}/mark_notified,jobs/mark_stale_notified,status}` all live with shared-secret guard.
- [ ] Alembic migration adds `discord_channel_id` and `notified_at` columns + index.
- [ ] `DiscordPlugin` shipped in `atlas_plugins`, appended to `REGISTERED_PLUGINS`, single tool `send_message` with interactive-aware confirmation gate.
- [ ] Unit tests cover all areas listed in section 7.1.
- [ ] Manual integration smoke (section 7.2) passes against real Discord guild.
- [ ] Deploy docs updated: required env vars on bot, required `('discord', 'default')` credential payload, bot Discord developer-portal setup notes (no privileged intents needed, guild-scoped commands).

## 10. Out of Phase 3 Plan 2

- File / message-link ingestion (`/atlas ingest` URL only).
- `discord.list_recent_messages` plugin tool.
- `/atlas summarize` slash command.
- Per-channel project bindings.
- Bot user allowlist.
- Knowledge-graph ingestion of Discord content.
- LISTEN/NOTIFY (replaced by polling).
- Per-channel session memory for `/atlas ask`.
- Cross-guild support.
- Internal-secret rotation (Phase 4).

## 11. Implementation order (rough)

The plan doc will translate this to T-tasks. Sketch:

1. Alembic migration + `IngestionJobORM` columns.
2. Agent runner extraction + regression tests (highest-risk; first so reviewers can sign off before the rest is built on it).
3. Internal API router (chat / jobs / status endpoints with shared-secret guard).
4. `DiscordPlugin` (plugin module + interactive-aware gate + register).
5. Bot scaffolding (`apps/discord-bot/`, Dockerfile, settings, chunker, api_client, internal_app).
6. Bot commands (`ask`, `ingest`, `status`).
7. Bot poller.
8. Compose wiring + deploy doc updates.
9. Manual integration smoke.

This ordering keeps the agent-runner refactor as the first reviewer checkpoint, lets the API surface settle before bot work, and ends with a single end-to-end smoke pass.
