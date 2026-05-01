# Phase 3 Plan 2 — Discord Bot + Plugin

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship a Discord bot compose service + `DiscordPlugin` so Matt can `/atlas ask`, `/atlas ingest`, and `/atlas status` from Discord, with ingestion-complete notifications and agent-initiated `discord.send_message` tool support.

**Architecture:** The Anthropic tool-use loop is extracted from `ws/chat.py` into a reusable `agent_runner.py` async generator; the new `POST /api/v1/internal/discord/chat` endpoint calls `run_turn_collected()` for the bot. A new `apps/discord-bot/` compose service runs discord.py + a mini FastAPI app for inbound API→bot posts + a 10s polling loop for ingestion notifications.

**Tech Stack:** discord.py 2.x, httpx, FastAPI (bot's internal app), SQLAlchemy (ORM polling), pydantic-settings, Alembic, atlas-plugins contextvar gate, uv workspace.

---

## File Map

**New files:**
- `infra/alembic/versions/0008_discord_columns.py`
- `packages/atlas-core/atlas_core/db/orm.py` — 2 new columns on `IngestionJobORM`
- `packages/atlas-plugins/atlas_plugins/context.py` — `is_interactive()` contextvar
- `apps/api/atlas_api/services/__init__.py`
- `apps/api/atlas_api/services/agent_runner.py` — `AgentEvent`, `run_tool_loop`, `run_turn_collected`
- `apps/api/atlas_api/routers/_internal/__init__.py`
- `apps/api/atlas_api/routers/_internal/discord.py` — 5 internal endpoints
- `apps/api/atlas_api/tests/test_agent_runner.py`
- `apps/api/atlas_api/tests/test_internal_discord.py`
- `packages/atlas-plugins/atlas_plugins/discord/__init__.py`
- `packages/atlas-plugins/atlas_plugins/discord/plugin.py`
- `packages/atlas-plugins/atlas_plugins/tests/test_discord.py`
- `apps/discord-bot/pyproject.toml`
- `apps/discord-bot/Dockerfile`
- `apps/discord-bot/atlas_discord_bot/__init__.py`
- `apps/discord-bot/atlas_discord_bot/settings.py`
- `apps/discord-bot/atlas_discord_bot/chunker.py`
- `apps/discord-bot/atlas_discord_bot/api_client.py`
- `apps/discord-bot/atlas_discord_bot/internal_app.py`
- `apps/discord-bot/atlas_discord_bot/commands/__init__.py`
- `apps/discord-bot/atlas_discord_bot/commands/ask.py`
- `apps/discord-bot/atlas_discord_bot/commands/ingest.py`
- `apps/discord-bot/atlas_discord_bot/commands/status.py`
- `apps/discord-bot/atlas_discord_bot/poller.py`
- `apps/discord-bot/atlas_discord_bot/__main__.py`
- `apps/discord-bot/tests/__init__.py`
- `apps/discord-bot/tests/test_chunker.py`
- `apps/discord-bot/tests/test_commands.py`
- `apps/discord-bot/tests/test_poller.py`

**Modified files:**
- `packages/atlas-plugins/pyproject.toml` — add `httpx`
- `packages/atlas-plugins/atlas_plugins/registry.py` — append `DiscordPlugin`
- `packages/atlas-knowledge/atlas_knowledge/models/ingestion.py` — add `discord_channel_id` to `UrlIngestRequest`
- `apps/api/atlas_api/routers/knowledge.py` — write `discord_channel_id` to job row
- `apps/api/atlas_api/ws/chat.py` — replace inline loop with `run_tool_loop`
- `apps/api/atlas_api/main.py` — include internal discord router
- `infra/docker-compose.yml` — add `discord-bot` service
- `pyproject.toml` — add `apps/discord-bot` to workspace

---

## Task 1: Alembic migration — add discord columns to ingestion_jobs

**Files:**
- Modify: `packages/atlas-core/atlas_core/db/orm.py`
- Create: `infra/alembic/versions/0008_discord_columns.py`

- [ ] **Step 1: Add columns to IngestionJobORM**

In `packages/atlas-core/atlas_core/db/orm.py`, after the `completed_at` column in `IngestionJobORM` (around line 198):

```python
    completed_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    discord_channel_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    notified_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
```

- [ ] **Step 2: Write migration**

Create `infra/alembic/versions/0008_discord_columns.py`:

```python
"""add discord_channel_id and notified_at to ingestion_jobs

Revision ID: 0008
Revises: 0007
Create Date: 2026-04-30
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("ingestion_jobs", sa.Column("discord_channel_id", sa.Text(), nullable=True))
    op.add_column(
        "ingestion_jobs",
        sa.Column("notified_at", sa.TIMESTAMP(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_ingestion_jobs_status_notified_at",
        "ingestion_jobs",
        ["status", "notified_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_ingestion_jobs_status_notified_at", table_name="ingestion_jobs")
    op.drop_column("ingestion_jobs", "notified_at")
    op.drop_column("ingestion_jobs", "discord_channel_id")
```

- [ ] **Step 3: Run migration**

```bash
cd /path/to/atlas-agent
uv run alembic upgrade head
```

Expected: `Running upgrade 0007 -> 0008, add discord_channel_id and notified_at to ingestion_jobs`

- [ ] **Step 4: Commit**

```bash
git add packages/atlas-core/atlas_core/db/orm.py infra/alembic/versions/0008_discord_columns.py
git commit -m "feat(db): add discord_channel_id + notified_at columns to ingestion_jobs"
```

---

## Task 2: Interactive contextvar

**Files:**
- Create: `packages/atlas-plugins/atlas_plugins/context.py`

- [ ] **Step 1: Write the contextvar module**

Create `packages/atlas-plugins/atlas_plugins/context.py`:

```python
"""Contextvar that signals whether the current agent call is interactive.

Interactive = originating from the WebSocket chat handler (React UI).
Non-interactive = originating from the internal Discord HTTP endpoint.

Plugins read this to decide whether to enforce confirmation gates.
"""

import contextvars

_INTERACTIVE: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "atlas_interactive", default=True
)


def is_interactive() -> bool:
    """Return True if the current call is from an interactive session."""
    return _INTERACTIVE.get()


def set_interactive(v: bool) -> contextvars.Token[bool]:
    """Set the interactive flag; returns a token for reset."""
    return _INTERACTIVE.set(v)


def reset_interactive(token: contextvars.Token[bool]) -> None:
    """Reset the interactive flag using the token from set_interactive."""
    _INTERACTIVE.reset(token)
```

- [ ] **Step 2: Export from atlas_plugins `__init__.py`**

In `packages/atlas-plugins/atlas_plugins/__init__.py`, add:

```python
from atlas_plugins.context import is_interactive, reset_interactive, set_interactive
```

- [ ] **Step 3: Run existing plugin tests to make sure nothing broke**

```bash
uv run pytest packages/atlas-plugins/ -q
```

Expected: all pass.

- [ ] **Step 4: Commit**

```bash
git add packages/atlas-plugins/atlas_plugins/context.py packages/atlas-plugins/atlas_plugins/__init__.py
git commit -m "feat(plugins): add interactive contextvar for confirmation gate control"
```

---

## Task 3: Agent runner — extract tool-use loop from ws/chat.py

**Files:**
- Create: `apps/api/atlas_api/services/__init__.py`
- Create: `apps/api/atlas_api/services/agent_runner.py`
- Modify: `apps/api/atlas_api/ws/chat.py`
- Create: `apps/api/atlas_api/tests/test_agent_runner.py`

- [ ] **Step 1: Write failing tests**

Create `apps/api/atlas_api/tests/test_agent_runner.py`:

```python
"""Tests for the extracted agent runner."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest
from atlas_core.models.llm import ToolResult
from atlas_core.providers._fake import FakeProvider
from atlas_plugins import FakePlugin, HealthStatus, PluginRegistry
from atlas_plugins.context import is_interactive

from atlas_api.services.agent_runner import (
    AgentEvent,
    AgentEventType,
    run_tool_loop,
    run_turn_collected,
)


@pytest.fixture
def fake_registry():
    plugin = FakePlugin(credentials=AsyncMock())
    reg = PluginRegistry([plugin])
    reg._health = {"fake": HealthStatus(ok=True)}
    return reg


@pytest.mark.asyncio
async def test_run_tool_loop_text_only_emits_text_delta_and_done():
    provider = FakeProvider(token_chunks=["hello", " world"])
    messages = [{"role": "user", "content": "hi"}]
    events = []
    async for event in run_tool_loop(
        provider=provider,
        messages=messages,
        tools_payload=None,
        plugin_registry=None,
        interactive=True,
    ):
        events.append(event)

    types = [e.type for e in events]
    assert AgentEventType.TEXT_DELTA in types
    assert types[-1] == AgentEventType.DONE
    done = events[-1]
    assert done.data["text"] == "hello world"
    assert done.data["tool_calls"] == []


@pytest.mark.asyncio
async def test_run_tool_loop_single_tool_call(fake_registry):
    provider = FakeProvider(
        scripted_turns=[
            {"tool_calls": [{"id": "c1", "tool": "fake__echo", "args": {"text": "ping"}}]},
            {"text": "the echo was ping"},
        ]
    )
    messages = [{"role": "user", "content": "echo ping"}]
    events = []
    async for event in run_tool_loop(
        provider=provider,
        messages=messages,
        tools_payload=[],
        plugin_registry=fake_registry,
        interactive=True,
    ):
        events.append(event)

    types = [e.type for e in events]
    assert AgentEventType.TOOL_CALL in types
    assert AgentEventType.TOOL_RESULT in types
    assert types[-1] == AgentEventType.DONE
    done = events[-1]
    assert len(done.data["tool_calls"]) == 1
    assert done.data["tool_calls"][0]["tool"] == "fake.echo"


@pytest.mark.asyncio
async def test_run_tool_loop_10_turn_cap_drops_tools(fake_registry):
    """On the 11th stream call, tools must be None (cap enforcement)."""
    # 10 recurse turns + 1 final text turn
    scripted = [
        {"tool_calls": [{"id": f"c{i}", "tool": "fake__recurse", "args": {"depth": i}}]}
        for i in range(10)
    ] + [{"text": "done"}]
    provider = FakeProvider(scripted_turns=scripted)
    messages = [{"role": "user", "content": "recurse"}]

    async for _ in run_tool_loop(
        provider=provider,
        messages=messages,
        tools_payload=[{"name": "fake__recurse", "description": "", "input_schema": {}}],
        plugin_registry=fake_registry,
        interactive=True,
    ):
        pass

    assert len(provider.stream_calls) == 11
    assert provider.stream_calls[10]["tools"] is None


@pytest.mark.asyncio
async def test_run_tool_loop_sets_interactive_contextvar():
    provider = FakeProvider(token_chunks=["hi"])
    captured: list[bool] = []

    async def _spy_invoke(tool_name: str, args: dict[str, Any]) -> Any:
        captured.append(is_interactive())
        return {"ok": True}

    registry = AsyncMock()
    registry.invoke.side_effect = lambda tool, args, call_id: _make_tool_result(call_id, tool)

    provider2 = FakeProvider(
        scripted_turns=[
            {"tool_calls": [{"id": "c1", "tool": "fake__echo", "args": {"text": "x"}}]},
            {"text": "done"},
        ]
    )
    from atlas_plugins import FakePlugin, HealthStatus, PluginRegistry
    plugin = FakePlugin(credentials=AsyncMock())
    reg = PluginRegistry([plugin])
    reg._health = {"fake": HealthStatus(ok=True)}

    interactive_flag: list[bool] = []
    original_invoke = reg.invoke

    async def _capturing_invoke(tool, args, *, call_id):
        interactive_flag.append(is_interactive())
        return await original_invoke(tool, args, call_id=call_id)

    reg.invoke = _capturing_invoke

    messages = [{"role": "user", "content": "test"}]
    async for _ in run_tool_loop(
        provider=provider2,
        messages=messages,
        tools_payload=[],
        plugin_registry=reg,
        interactive=False,
    ):
        pass

    assert interactive_flag == [False]


def _make_tool_result(call_id: str, tool: str) -> ToolResult:
    return ToolResult(call_id=call_id, tool=tool, result={"ok": True}, error=None)


@pytest.mark.asyncio
async def test_run_turn_collected_returns_final_text():
    provider = FakeProvider(token_chunks=["foo", " bar"])
    text = await run_turn_collected(
        provider=provider,
        messages=[{"role": "user", "content": "hi"}],
        tools_payload=None,
        plugin_registry=None,
        interactive=False,
    )
    assert text == "foo bar"


@pytest.mark.asyncio
async def test_run_tool_loop_contextvar_reset_after_done():
    """interactive contextvar must be restored to its previous value after the loop."""
    from atlas_plugins.context import set_interactive

    token = set_interactive(True)  # set outer context to True
    try:
        provider = FakeProvider(token_chunks=["hi"])
        async for _ in run_tool_loop(
            provider=provider,
            messages=[{"role": "user", "content": "hi"}],
            tools_payload=None,
            plugin_registry=None,
            interactive=False,
        ):
            pass
        assert is_interactive() is True  # restored after generator exhausted
    finally:
        reset_interactive(token)


from atlas_plugins.context import reset_interactive
```

- [ ] **Step 2: Run — confirm ImportError (module doesn't exist yet)**

```bash
uv run pytest apps/api/atlas_api/tests/test_agent_runner.py -q 2>&1 | head -20
```

Expected: `ModuleNotFoundError: No module named 'atlas_api.services.agent_runner'`

- [ ] **Step 3: Create `services/__init__.py`**

```bash
touch apps/api/atlas_api/services/__init__.py
```

- [ ] **Step 4: Create `agent_runner.py`**

Create `apps/api/atlas_api/services/agent_runner.py`:

```python
"""Extracted Anthropic tool-use loop.

run_tool_loop:      async generator → AgentEvent stream
run_turn_collected: drains the generator → final text string

The ``interactive`` flag is written to a ContextVar in atlas_plugins.context
so plugins can read it without it passing through the call stack.
"""

from __future__ import annotations

import json
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

import structlog
from atlas_core.models.llm import ModelEventType, ToolSchema
from atlas_plugins import PluginRegistry
from atlas_plugins.context import reset_interactive, set_interactive

log = structlog.get_logger("atlas.api.agent_runner")

MAX_TOOL_TURNS = 10


class AgentEventType(str, Enum):
    TEXT_DELTA = "text_delta"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    DONE = "done"
    ERROR = "error"


@dataclass
class AgentEvent:
    type: AgentEventType
    data: dict[str, Any] = field(default_factory=dict)


def _encode_tool_name(name: str) -> str:
    return name.replace(".", "__")


def _decode_tool_name(name: str) -> str:
    return name.replace("__", ".")


def _to_anthropic_tool(s: ToolSchema) -> dict[str, Any]:
    return {
        "name": _encode_tool_name(s.name),
        "description": s.description,
        "input_schema": s.parameters,
    }


def _now_iso() -> str:
    from datetime import UTC, datetime
    return datetime.now(UTC).isoformat()


async def run_tool_loop(
    *,
    provider: Any,
    messages: list[dict[str, Any]],
    tools_payload: list[dict[str, Any]] | None,
    plugin_registry: PluginRegistry | None,
    interactive: bool = True,
    temperature: float = 1.0,
) -> AsyncIterator[AgentEvent]:
    """Async generator yielding AgentEvents for the full tool-use loop."""
    token = set_interactive(interactive)
    try:
        assistant_text_parts: list[str] = []
        all_tool_calls: list[dict[str, Any]] = []
        tool_turn = 0
        started = time.monotonic()
        usage: dict[str, Any] = {}
        error_occurred = False

        while True:
            pending_tool_calls: list[dict[str, Any]] = []

            async for event in provider.stream(
                messages=messages,
                tools=tools_payload,
                temperature=temperature,
            ):
                if event.type == ModelEventType.TOKEN:
                    text = event.data.get("text", "")
                    assistant_text_parts.append(text)
                    yield AgentEvent(AgentEventType.TEXT_DELTA, {"text": text})
                elif event.type == ModelEventType.TOOL_CALL:
                    call = dict(event.data)
                    call["tool"] = _decode_tool_name(call["tool"])
                    yield AgentEvent(
                        AgentEventType.TOOL_CALL,
                        {"id": call["id"], "tool": call["tool"], "started_at": _now_iso()},
                    )
                    pending_tool_calls.append(call)
                elif event.type == ModelEventType.ERROR:
                    yield AgentEvent(AgentEventType.ERROR, event.data)
                    error_occurred = True
                    break
                elif event.type == ModelEventType.DONE:
                    usage = event.data.get("usage", {})

            if error_occurred:
                break

            if not pending_tool_calls:
                break

            tool_turn += 1
            tool_results = []
            for call in pending_tool_calls:
                call_started = time.monotonic()
                if plugin_registry is None:
                    from atlas_core.models.llm import ToolResult
                    result = ToolResult(
                        call_id=call["id"],
                        tool=call["tool"],
                        result=None,
                        error="no plugin registry available",
                    )
                else:
                    result = await plugin_registry.invoke(
                        call["tool"], call["args"], call_id=call["id"]
                    )
                duration_ms = int((time.monotonic() - call_started) * 1000)
                ok = result.error is None
                yield AgentEvent(
                    AgentEventType.TOOL_RESULT,
                    {
                        "tool": call["tool"],
                        "call_id": call["id"],
                        "ok": ok,
                        "duration_ms": duration_ms,
                    },
                )
                tool_results.append(result)
                all_tool_calls.append(
                    {
                        "call_id": call["id"],
                        "tool": call["tool"],
                        "args": call["args"],
                        "result": result.result if ok else None,
                        "error": result.error,
                    }
                )

            messages.append(
                {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "tool_use",
                            "id": c["id"],
                            "name": _encode_tool_name(c["tool"]),
                            "input": c["args"],
                        }
                        for c in pending_tool_calls
                    ],
                }
            )
            messages.append(
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": r.call_id,
                            "content": (
                                json.dumps(r.result) if r.error is None else f"Error: {r.error}"
                            ),
                            "is_error": r.error is not None,
                        }
                        for r in tool_results
                    ],
                }
            )

            if tool_turn >= MAX_TOOL_TURNS:
                tools_payload = None
                messages.append(
                    {
                        "role": "user",
                        "content": "Tool call limit reached; respond to the user without using tools.",
                    }
                )

        if not error_occurred:
            latency_ms = int((time.monotonic() - started) * 1000)
            yield AgentEvent(
                AgentEventType.DONE,
                {
                    "text": "".join(assistant_text_parts),
                    "tool_calls": all_tool_calls,
                    "usage": usage,
                    "latency_ms": latency_ms,
                },
            )
    finally:
        reset_interactive(token)


async def run_turn_collected(
    *,
    provider: Any,
    messages: list[dict[str, Any]],
    tools_payload: list[dict[str, Any]] | None,
    plugin_registry: PluginRegistry | None,
    interactive: bool = False,
    temperature: float = 1.0,
) -> str:
    """Drain run_tool_loop; return the final assembled text."""
    async for event in run_tool_loop(
        provider=provider,
        messages=messages,
        tools_payload=tools_payload,
        plugin_registry=plugin_registry,
        interactive=interactive,
        temperature=temperature,
    ):
        if event.type == AgentEventType.DONE:
            return event.data.get("text", "")
        if event.type == AgentEventType.ERROR:
            raise RuntimeError(event.data.get("message", "agent error"))
    return ""
```

- [ ] **Step 5: Run tests — all should pass**

```bash
uv run pytest apps/api/atlas_api/tests/test_agent_runner.py -v
```

Expected: 6 tests pass.

- [ ] **Step 6: Refactor `ws/chat.py` to use the runner**

Replace the tool-use loop section in `_handle_chat_message` (lines 246–383). The full updated function body (replace everything from `# 7. Stream events` through `await db.flush()` + the final `_send` call):

```python
    # 7. Stream events via agent runner
    assistant_text_parts: list[str] = []
    all_tool_calls_across_turns: list[dict] = []
    usage: dict = {}
    latency_ms = 0
    error_occurred = False

    from atlas_api.services.agent_runner import (
        AgentEventType,
        run_tool_loop,
        _to_anthropic_tool,
    )

    async for event in run_tool_loop(
        provider=provider,
        messages=messages_for_provider,
        tools_payload=tools_payload,
        plugin_registry=plugin_registry,
        interactive=True,
        temperature=req.temperature,
    ):
        if event.type == AgentEventType.TEXT_DELTA:
            text = event.data["text"]
            assistant_text_parts.append(text)
            sequence = await _send(websocket, StreamEventType.TOKEN, {"token": text}, sequence)
        elif event.type == AgentEventType.TOOL_CALL:
            sequence = await _send(
                websocket,
                StreamEventType.TOOL_CALL,
                {
                    "tool_name": event.data["tool"],
                    "call_id": event.data["id"],
                    "started_at": event.data["started_at"],
                },
                sequence,
            )
        elif event.type == AgentEventType.TOOL_RESULT:
            sequence = await _send(
                websocket,
                StreamEventType.TOOL_RESULT,
                {
                    "tool_name": event.data["tool"],
                    "call_id": event.data["call_id"],
                    "ok": event.data["ok"],
                    "duration_ms": event.data["duration_ms"],
                },
                sequence,
            )
        elif event.type == AgentEventType.DONE:
            assistant_text_parts = [event.data["text"]]  # runner assembled it
            all_tool_calls_across_turns = event.data["tool_calls"]
            usage = event.data.get("usage", {})
            latency_ms = event.data.get("latency_ms", 0)
        elif event.type == AgentEventType.ERROR:
            sequence = await _send(websocket, StreamEventType.ERROR, event.data, sequence)
            error_occurred = True

    if error_occurred:
        return sequence

    full_assistant_text = assistant_text_parts[0] if assistant_text_parts else ""

    # 8. Persist the assistant turn + usage
    assistant_row = MessageORM(
        user_id=settings.user_id,
        session_id=session_id,
        role=MessageRole.ASSISTANT.value,
        content=full_assistant_text,
        rag_context=rag_citations,
        model=provider.spec.model_id,
        token_count=(usage or {}).get("output_tokens"),
        tool_calls=all_tool_calls_across_turns if all_tool_calls_across_turns else None,
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
        {"usage": usage or {}, "model": provider.spec.model_id, "latency_ms": latency_ms},
        sequence,
    )
    return sequence
```

Also remove the now-unused imports from `ws/chat.py`: `import json`, `import time` (they move to agent_runner). Keep `from atlas_core.models.llm import ModelEventType, ToolResult, ToolSchema` → replace with just `ToolSchema` (ModelEventType and ToolResult are used inside agent_runner now).

Also remove the `_encode_tool_name`, `_decode_tool_name`, `_to_anthropic_tool` functions from `ws/chat.py` — they now live in `agent_runner.py`. Update the `_to_anthropic_tool` call in `_handle_chat_message` to import from agent_runner (already done in the block above via `from atlas_api.services.agent_runner import _to_anthropic_tool`). Better: move the import to the top of the file.

Update the top-level imports in `ws/chat.py`:

```python
from atlas_api.services.agent_runner import AgentEventType, _to_anthropic_tool, run_tool_loop
```

And remove `import json`, `import time` (no longer needed in this file), remove `ModelEventType, ToolResult` from the atlas_core import.

- [ ] **Step 7: Run the full WS chat test suite to confirm no regression**

```bash
uv run pytest apps/api/atlas_api/tests/test_ws_chat.py apps/api/atlas_api/tests/test_ws_chat_rag.py apps/api/atlas_api/tests/test_ws_chat_tool_use.py -v
```

Expected: all pass.

- [ ] **Step 8: Commit**

```bash
git add apps/api/atlas_api/services/ apps/api/atlas_api/ws/chat.py apps/api/atlas_api/tests/test_agent_runner.py
git commit -m "refactor(api/ws): extract tool-use loop into agent_runner service"
```

---

## Task 4: Internal Discord router

**Files:**
- Create: `apps/api/atlas_api/routers/_internal/__init__.py`
- Create: `apps/api/atlas_api/routers/_internal/discord.py`
- Modify: `apps/api/atlas_api/main.py`
- Create: `apps/api/atlas_api/tests/test_internal_discord.py`

- [ ] **Step 1: Write failing tests**

Create `apps/api/atlas_api/tests/test_internal_discord.py`:

```python
"""Tests for /api/v1/internal/discord/* endpoints."""

from __future__ import annotations

import os
from unittest.mock import AsyncMock, patch

import pytest
from atlas_core.db.orm import IngestionJobORM, ProjectORM

from atlas_api.main import app
from atlas_api.deps import get_session

SECRET = "test-secret-abc"


@pytest.fixture(autouse=True)
def patch_secret(monkeypatch):
    monkeypatch.setenv("ATLAS_DISCORD__INTERNAL_SECRET", SECRET)


@pytest.mark.asyncio
async def test_chat_missing_secret_returns_401(app_client):
    resp = await app_client.post(
        "/api/v1/internal/discord/chat",
        json={"project_id": "00000000-0000-0000-0000-000000000001", "prompt": "hi"},
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_chat_wrong_secret_returns_401(app_client):
    resp = await app_client.post(
        "/api/v1/internal/discord/chat",
        headers={"X-Internal-Secret": "wrong"},
        json={"project_id": "00000000-0000-0000-0000-000000000001", "prompt": "hi"},
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_chat_project_not_found_returns_404(app_client):
    resp = await app_client.post(
        "/api/v1/internal/discord/chat",
        headers={"X-Internal-Secret": SECRET},
        json={"project_id": "00000000-0000-0000-0000-000000000099", "prompt": "hi"},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_chat_happy_path_returns_text(app_client, db_session):
    # Create a project row
    import uuid
    proj = ProjectORM(
        id=uuid.uuid4(),
        user_id="test-user",
        name="test",
        default_model="claude-haiku-4-5-20251001",
        enabled_plugins=[],
    )
    db_session.add(proj)
    await db_session.flush()

    with patch(
        "atlas_api.routers._internal.discord.run_turn_collected",
        new=AsyncMock(return_value="hello from agent"),
    ):
        resp = await app_client.post(
            "/api/v1/internal/discord/chat",
            headers={"X-Internal-Secret": SECRET},
            json={"project_id": str(proj.id), "prompt": "hello"},
        )

    assert resp.status_code == 200
    assert resp.json()["text"] == "hello from agent"


@pytest.mark.asyncio
async def test_jobs_pending_missing_secret(app_client):
    resp = await app_client.get("/api/v1/internal/discord/jobs/pending")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_jobs_pending_returns_empty(app_client):
    resp = await app_client.get(
        "/api/v1/internal/discord/jobs/pending",
        headers={"X-Internal-Secret": SECRET},
    )
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.asyncio
async def test_mark_notified_updates_row(app_client, db_session):
    import uuid
    from datetime import UTC, datetime

    job = IngestionJobORM(
        id=uuid.uuid4(),
        user_id="test-user",
        project_id=uuid.uuid4(),
        source_type="url",
        status="completed",
        completed_at=datetime.now(UTC),
    )
    db_session.add(job)
    await db_session.flush()

    resp = await app_client.post(
        f"/api/v1/internal/discord/jobs/{job.id}/mark_notified",
        headers={"X-Internal-Secret": SECRET},
    )
    assert resp.status_code == 200
    await db_session.refresh(job)
    assert job.notified_at is not None


@pytest.mark.asyncio
async def test_discord_status_returns_postgres_ok(app_client):
    resp = await app_client.get(
        "/api/v1/internal/discord/status",
        headers={"X-Internal-Secret": SECRET},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["postgres"] == "ok"
    assert "running_jobs" in body
```

- [ ] **Step 2: Run — confirm 404 (routes not registered)**

```bash
uv run pytest apps/api/atlas_api/tests/test_internal_discord.py::test_chat_missing_secret_returns_401 -q 2>&1 | head -10
```

Expected: 404 from FastAPI (route doesn't exist yet).

- [ ] **Step 3: Create `_internal/__init__.py`**

```bash
mkdir -p apps/api/atlas_api/routers/_internal
touch apps/api/atlas_api/routers/_internal/__init__.py
```

- [ ] **Step 4: Create the router**

Create `apps/api/atlas_api/routers/_internal/discord.py`:

```python
"""Internal Discord endpoints — callable only by the discord-bot service.

Auth: X-Internal-Secret header, validated against ATLAS_DISCORD__INTERNAL_SECRET env var.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import structlog
from atlas_core.config import AtlasConfig
from atlas_core.db.orm import IngestionJobORM, ProjectORM
from atlas_core.prompts.builder import SystemPromptBuilder
from atlas_core.prompts.registry import prompt_registry
from atlas_plugins import PluginRegistry
from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from atlas_api.deps import get_model_router, get_plugin_registry, get_session, get_settings
from atlas_api.services.agent_runner import _to_anthropic_tool, run_turn_collected

log = structlog.get_logger("atlas.api.internal.discord")
router = APIRouter(tags=["internal-discord"])
_prompt_builder = SystemPromptBuilder(prompt_registry)


async def _require_secret(x_internal_secret: str = Header(alias="X-Internal-Secret")) -> None:
    expected = os.getenv("ATLAS_DISCORD__INTERNAL_SECRET")
    if not expected or x_internal_secret != expected:
        raise HTTPException(status_code=401, detail="unauthorized")


class ChatRequest(BaseModel):
    project_id: UUID
    prompt: str
    temperature: float = 1.0


class ChatResponse(BaseModel):
    text: str


class PendingJob(BaseModel):
    id: UUID
    status: str
    source_filename: str | None
    discord_channel_id: str | None
    error: str | None


@router.post("/internal/discord/chat", response_model=ChatResponse)
async def discord_chat(
    req: ChatRequest,
    _: None = Depends(_require_secret),
    db: AsyncSession = Depends(get_session),
    model_router: Any = Depends(get_model_router),
    plugin_registry: PluginRegistry | None = Depends(get_plugin_registry),
    settings: AtlasConfig = Depends(get_settings),
) -> ChatResponse:
    project = await db.get(ProjectORM, req.project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="project not found")

    from atlas_core.db.converters import project_from_orm
    proj = project_from_orm(project)

    try:
        provider = model_router.select(proj)
    except ValueError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e

    tools_payload = None
    if plugin_registry is not None and provider.spec.provider == "anthropic":
        enabled = list(project.enabled_plugins or [])
        schemas = plugin_registry.get_tool_schemas(enabled=enabled)
        if schemas:
            tools_payload = [_to_anthropic_tool(s) for s in schemas]

    system_prompt = _prompt_builder.build(proj)
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": req.prompt},
    ]

    text = await run_turn_collected(
        provider=provider,
        messages=messages,
        tools_payload=tools_payload,
        plugin_registry=plugin_registry,
        interactive=False,
        temperature=req.temperature,
    )
    return ChatResponse(text=text)


@router.get("/internal/discord/jobs/pending", response_model=list[PendingJob])
async def get_pending_jobs(
    _: None = Depends(_require_secret),
    db: AsyncSession = Depends(get_session),
) -> list[PendingJob]:
    cutoff = datetime.now(UTC) - timedelta(minutes=10)
    result = await db.execute(
        select(IngestionJobORM).where(
            IngestionJobORM.status.in_(["completed", "failed"]),
            IngestionJobORM.notified_at.is_(None),
            IngestionJobORM.completed_at >= cutoff,
        )
    )
    rows = result.scalars().all()
    return [
        PendingJob(
            id=r.id,
            status=r.status,
            source_filename=r.source_filename,
            discord_channel_id=r.discord_channel_id,
            error=r.error,
        )
        for r in rows
    ]


@router.post("/internal/discord/jobs/{job_id}/mark_notified")
async def mark_notified(
    job_id: UUID,
    _: None = Depends(_require_secret),
    db: AsyncSession = Depends(get_session),
) -> dict:
    job = await db.get(IngestionJobORM, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    job.notified_at = datetime.now(UTC)
    await db.flush()
    return {"ok": True}


@router.post("/internal/discord/jobs/mark_stale_notified")
async def mark_stale_notified(
    _: None = Depends(_require_secret),
    db: AsyncSession = Depends(get_session),
) -> dict:
    cutoff = datetime.now(UTC) - timedelta(minutes=10)
    now = datetime.now(UTC)
    await db.execute(
        update(IngestionJobORM)
        .where(
            IngestionJobORM.status.in_(["completed", "failed"]),
            IngestionJobORM.notified_at.is_(None),
            IngestionJobORM.completed_at < cutoff,
        )
        .values(notified_at=now)
    )
    await db.flush()
    return {"ok": True}


@router.get("/internal/discord/status")
async def discord_status(
    _: None = Depends(_require_secret),
    db: AsyncSession = Depends(get_session),
) -> dict:
    running = await db.scalar(
        select(func.count(IngestionJobORM.id)).where(IngestionJobORM.status == "running")
    )
    return {"postgres": "ok", "running_jobs": running or 0}
```

- [ ] **Step 5: Wire into `main.py`**

Add to `apps/api/atlas_api/main.py` (after the existing imports, before `app = FastAPI(...)`):

```python
from atlas_api.routers._internal import discord as internal_discord_router
```

And after the existing `app.include_router(...)` calls:

```python
app.include_router(internal_discord_router.router, prefix="/api/v1")
```

- [ ] **Step 6: Run tests**

```bash
uv run pytest apps/api/atlas_api/tests/test_internal_discord.py -v
```

Expected: all 8 tests pass.

- [ ] **Step 7: Commit**

```bash
git add apps/api/atlas_api/routers/_internal/ apps/api/atlas_api/main.py apps/api/atlas_api/tests/test_internal_discord.py
git commit -m "feat(api): internal discord router — chat, jobs, status endpoints"
```

---

## Task 5: DiscordPlugin

**Files:**
- Create: `packages/atlas-plugins/atlas_plugins/discord/__init__.py`
- Create: `packages/atlas-plugins/atlas_plugins/discord/plugin.py`
- Modify: `packages/atlas-plugins/pyproject.toml`
- Modify: `packages/atlas-plugins/atlas_plugins/registry.py`
- Create: `packages/atlas-plugins/atlas_plugins/tests/test_discord.py`

- [ ] **Step 1: Add `httpx` to atlas-plugins deps**

In `packages/atlas-plugins/pyproject.toml`, update `dependencies`:

```toml
dependencies = [
    "atlas-core",
    "cryptography>=42.0",
    "httpx>=0.27",
    "structlog>=24.4",
    "sqlalchemy>=2.0",
]
```

Run `uv sync` to update the lockfile.

- [ ] **Step 2: Write failing tests**

Create `packages/atlas-plugins/atlas_plugins/tests/test_discord.py`:

```python
"""Tests for DiscordPlugin."""

from __future__ import annotations

import os
import time
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from atlas_plugins.context import is_interactive, reset_interactive, set_interactive

from atlas_plugins.discord.plugin import DiscordPlugin
from atlas_plugins import CredentialStore, HealthStatus
from atlas_plugins.credentials import InMemoryBackend


@pytest.fixture
def cred_store():
    from cryptography.fernet import Fernet
    return CredentialStore(backend=InMemoryBackend(), master_key=Fernet.generate_key().decode())


@pytest.fixture
def plugin(cred_store):
    return DiscordPlugin(credentials=cred_store)


@pytest.fixture(autouse=True)
def discord_env(monkeypatch):
    monkeypatch.setenv("ATLAS_DISCORD__INTERNAL_SECRET", "test-secret")
    monkeypatch.setenv("ATLAS_DISCORD__BOT_URL", "http://fake-bot:8001")


@pytest.mark.asyncio
async def test_health_no_creds_returns_not_ok(plugin):
    status = await plugin.health()
    assert status.ok is False


@pytest.mark.asyncio
async def test_health_with_creds_returns_ok(plugin, cred_store):
    await cred_store.set("discord", "default", {"default_channel_id": "123456"})
    status = await plugin.health()
    assert status.ok is True


@pytest.mark.asyncio
async def test_health_cred_missing_channel_returns_not_ok(plugin, cred_store):
    await cred_store.set("discord", "default", {})
    status = await plugin.health()
    assert status.ok is False


def test_get_tools_returns_send_message(plugin):
    tools = plugin.get_tools()
    assert len(tools) == 1
    assert tools[0].name == "discord.send_message"
    assert tools[0].plugin == "discord"


@pytest.mark.asyncio
async def test_send_message_noninteractive_posts_directly(plugin, cred_store):
    await cred_store.set("discord", "default", {"default_channel_id": "111"})
    token = set_interactive(False)
    try:
        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client_cls.return_value.__aenter__.return_value = mock_client
            mock_client.post.return_value = MagicMock(
                status_code=200,
                json=lambda: {"message_id": "msg-1"},
            )
            result = await plugin.invoke("discord.send_message", {"body": "hello bot"})
    finally:
        reset_interactive(token)

    assert result["posted"] is True
    assert result["message_id"] == "msg-1"
    mock_client.post.assert_called_once()
    call_kwargs = mock_client.post.call_args
    assert call_kwargs[1]["json"]["body"] == "hello bot"
    assert call_kwargs[1]["json"]["channel_id"] == "111"


@pytest.mark.asyncio
async def test_send_message_interactive_first_call_returns_draft_token(plugin, cred_store):
    await cred_store.set("discord", "default", {"default_channel_id": "222"})
    token = set_interactive(True)
    try:
        result = await plugin.invoke("discord.send_message", {"body": "hi there"})
    finally:
        reset_interactive(token)

    assert "draft_token" in result
    assert result["preview"]["body"] == "hi there"
    assert result["preview"]["channel_id"] == "222"


@pytest.mark.asyncio
async def test_send_message_interactive_second_call_executes(plugin, cred_store):
    await cred_store.set("discord", "default", {"default_channel_id": "333"})
    token = set_interactive(True)
    try:
        first = await plugin.invoke("discord.send_message", {"body": "confirm me"})
        draft_token = first["draft_token"]

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client_cls.return_value.__aenter__.return_value = mock_client
            mock_client.post.return_value = MagicMock(
                status_code=200,
                json=lambda: {"message_id": "msg-confirm"},
            )
            result = await plugin.invoke(
                "discord.send_message", {"confirm_token": draft_token}
            )
    finally:
        reset_interactive(token)

    assert result["posted"] is True


@pytest.mark.asyncio
async def test_send_message_interactive_expired_token_raises(plugin, cred_store):
    await cred_store.set("discord", "default", {"default_channel_id": "444"})
    token = set_interactive(True)
    try:
        with pytest.raises(ValueError, match="confirm_token expired or invalid"):
            await plugin.invoke(
                "discord.send_message", {"confirm_token": "no-such-token"}
            )
    finally:
        reset_interactive(token)


@pytest.mark.asyncio
async def test_send_message_interactive_reused_token_raises(plugin, cred_store):
    await cred_store.set("discord", "default", {"default_channel_id": "555"})
    token = set_interactive(True)
    try:
        first = await plugin.invoke("discord.send_message", {"body": "double"})
        draft_token = first["draft_token"]

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client_cls.return_value.__aenter__.return_value = mock_client
            mock_client.post.return_value = MagicMock(
                status_code=200, json=lambda: {"message_id": "m"}
            )
            await plugin.invoke("discord.send_message", {"confirm_token": draft_token})

        with pytest.raises(ValueError, match="confirm_token expired or invalid"):
            await plugin.invoke("discord.send_message", {"confirm_token": draft_token})
    finally:
        reset_interactive(token)
```

- [ ] **Step 3: Run — confirm ImportError**

```bash
uv run pytest packages/atlas-plugins/atlas_plugins/tests/test_discord.py -q 2>&1 | head -5
```

Expected: `ModuleNotFoundError: No module named 'atlas_plugins.discord'`

- [ ] **Step 4: Create the plugin**

```bash
mkdir -p packages/atlas-plugins/atlas_plugins/discord
touch packages/atlas-plugins/atlas_plugins/discord/__init__.py
```

Create `packages/atlas-plugins/atlas_plugins/discord/plugin.py`:

```python
"""Discord plugin — exposes discord.send_message to the agent.

Credential schema (stored in plugin_credentials):
  {"default_channel_id": "<channel-snowflake>"}

Env vars (not in credential store):
  ATLAS_DISCORD__INTERNAL_SECRET  — shared secret for bot HTTP calls
  ATLAS_DISCORD__BOT_URL          — bot internal app URL (default: http://discord-bot:8001)

Confirmation gate:
  Interactive callers (WS chat): first call returns draft+token; second call executes.
  Non-interactive callers (/atlas ask): gate bypassed, posts immediately.
"""

from __future__ import annotations

import os
import time
import uuid
from typing import Any

import httpx
import structlog
from atlas_core.models.llm import ToolSchema
from atlas_plugins.base import AtlasPlugin, HealthStatus
from atlas_plugins.context import is_interactive

log = structlog.get_logger("atlas.plugins.discord")

_TOKEN_TTL_SECONDS = 300


class DiscordPlugin(AtlasPlugin):
    name = "discord"
    description = "Send messages to Discord and manage bot interactions."

    def __init__(self, credentials) -> None:
        super().__init__(credentials)
        # In-memory confirmation gate: {token: {"body": str, "channel_id": str, "expires": float}}
        self._pending: dict[str, dict[str, Any]] = {}

    def get_tools(self) -> list[ToolSchema]:
        return [
            ToolSchema(
                name="discord.send_message",
                description=(
                    "Send a message to the configured Discord channel. "
                    "In interactive sessions, the first call returns a draft preview + token; "
                    "call again with confirm_token to actually send. "
                    "body: the message text to send. "
                    "confirm_token: token from a previous draft call to confirm and send."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "body": {"type": "string", "description": "Message text to send."},
                        "confirm_token": {
                            "type": "string",
                            "description": "Confirmation token from a prior draft call.",
                        },
                    },
                },
                plugin="discord",
            )
        ]

    async def health(self) -> HealthStatus:
        try:
            creds = await self._get_credentials()
            if not creds.get("default_channel_id"):
                return HealthStatus(ok=False, detail="default_channel_id missing from credentials")
            return HealthStatus(ok=True)
        except Exception as e:
            return HealthStatus(ok=False, detail=str(e))

    async def invoke(self, tool_name: str, args: dict[str, Any]) -> Any:
        if tool_name != "discord.send_message":
            raise ValueError(f"unknown tool {tool_name!r}")
        return await self._send_message(args)

    async def _send_message(self, args: dict[str, Any]) -> dict[str, Any]:
        self._expire_pending()
        creds = await self._get_credentials()
        channel_id = creds["default_channel_id"]

        confirm_token = args.get("confirm_token")
        body = args.get("body")

        if confirm_token is not None:
            # Second call: execute the pending send
            entry = self._pending.pop(confirm_token, None)
            if entry is None or entry["expires"] < time.monotonic():
                if entry is not None:
                    pass  # already popped above but was expired
                raise ValueError("confirm_token expired or invalid")
            return await self._post_to_bot(entry["channel_id"], entry["body"])

        if body is None:
            raise ValueError("either body or confirm_token must be provided")

        if is_interactive():
            # First call: return draft + token (confirmation gate)
            token = str(uuid.uuid4())
            self._pending[token] = {
                "body": body,
                "channel_id": channel_id,
                "expires": time.monotonic() + _TOKEN_TTL_SECONDS,
            }
            return {"preview": {"body": body, "channel_id": channel_id}, "draft_token": token}

        # Non-interactive: post immediately
        return await self._post_to_bot(channel_id, body)

    async def _post_to_bot(self, channel_id: str, body: str) -> dict[str, Any]:
        bot_url = os.getenv("ATLAS_DISCORD__BOT_URL", "http://discord-bot:8001")
        secret = os.getenv("ATLAS_DISCORD__INTERNAL_SECRET", "")
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f"{bot_url}/internal/discord/send",
                json={"channel_id": channel_id, "body": body},
                headers={"X-Internal-Secret": secret},
            )
            resp.raise_for_status()
            data = resp.json()
        return {"posted": True, "message_id": data.get("message_id")}

    def _expire_pending(self) -> None:
        now = time.monotonic()
        expired = [k for k, v in self._pending.items() if v["expires"] < now]
        for k in expired:
            del self._pending[k]
```

- [ ] **Step 5: Run tests**

```bash
uv run pytest packages/atlas-plugins/atlas_plugins/tests/test_discord.py -v
```

Expected: all 8 tests pass.

- [ ] **Step 6: Register DiscordPlugin**

In `packages/atlas-plugins/atlas_plugins/registry.py`, update the imports and `REGISTERED_PLUGINS`:

```python
from atlas_plugins._fake import FakePlugin
from atlas_plugins.discord.plugin import DiscordPlugin

REGISTERED_PLUGINS: list[type[AtlasPlugin]] = [FakePlugin, DiscordPlugin]
```

- [ ] **Step 7: Run full plugin test suite**

```bash
uv run pytest packages/atlas-plugins/ -q
```

Expected: all pass.

- [ ] **Step 8: Commit**

```bash
git add packages/atlas-plugins/
git commit -m "feat(plugins/discord): DiscordPlugin with send_message + interactive confirmation gate"
```

---

## Task 6: URL ingest — accept `discord_channel_id`

**Files:**
- Modify: `packages/atlas-knowledge/atlas_knowledge/models/ingestion.py`
- Modify: `apps/api/atlas_api/routers/knowledge.py`

- [ ] **Step 1: Add field to `UrlIngestRequest`**

In `packages/atlas-knowledge/atlas_knowledge/models/ingestion.py`, update `UrlIngestRequest`:

```python
class UrlIngestRequest(AtlasRequestModel):
    project_id: UUID
    url: HttpUrl
    discord_channel_id: str | None = None
```

- [ ] **Step 2: Write discord_channel_id to job row in the URL ingest endpoint**

In `apps/api/atlas_api/routers/knowledge.py`, in `ingest_url_endpoint`, after `job_row = await db.get(IngestionJobORM, result.job_id)` and the `if job_row is None` check, add:

```python
    if payload.discord_channel_id:
        job_row.discord_channel_id = payload.discord_channel_id
        await db.flush()
```

- [ ] **Step 3: Verify existing knowledge router tests still pass**

```bash
uv run pytest apps/api/atlas_api/tests/test_knowledge_router.py -q
```

Expected: all pass (discord_channel_id is optional, default None).

- [ ] **Step 4: Commit**

```bash
git add packages/atlas-knowledge/atlas_knowledge/models/ingestion.py apps/api/atlas_api/routers/knowledge.py
git commit -m "feat(api/knowledge): accept discord_channel_id on URL ingest, persist to job row"
```

---

## Task 7: Discord bot scaffolding

**Files:**
- Create: `apps/discord-bot/pyproject.toml`
- Create: `apps/discord-bot/Dockerfile`
- Create: `apps/discord-bot/atlas_discord_bot/__init__.py`
- Create: `apps/discord-bot/atlas_discord_bot/settings.py`
- Create: `apps/discord-bot/atlas_discord_bot/chunker.py`
- Create: `apps/discord-bot/atlas_discord_bot/api_client.py`
- Create: `apps/discord-bot/atlas_discord_bot/internal_app.py`
- Create: `apps/discord-bot/tests/__init__.py`
- Create: `apps/discord-bot/tests/test_chunker.py`
- Modify: `pyproject.toml` (workspace)

- [ ] **Step 1: Add discord-bot to workspace**

In root `pyproject.toml`, add `"apps/discord-bot"` to `[tool.uv.workspace] members`.

- [ ] **Step 2: Create `pyproject.toml` for the bot**

Create `apps/discord-bot/pyproject.toml`:

```toml
[project]
name = "atlas-discord-bot"
version = "0.1.0"
description = "ATLAS Discord bot"
requires-python = ">=3.13"
dependencies = [
    "atlas-core",
    "discord.py>=2.4",
    "fastapi>=0.115",
    "httpx>=0.27",
    "pydantic-settings>=2.0",
    "uvicorn[standard]>=0.32",
]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["atlas_discord_bot"]
```

Run `uv sync` to resolve deps.

- [ ] **Step 3: Create `settings.py`**

```bash
mkdir -p apps/discord-bot/atlas_discord_bot apps/discord-bot/tests
touch apps/discord-bot/atlas_discord_bot/__init__.py apps/discord-bot/tests/__init__.py
```

Create `apps/discord-bot/atlas_discord_bot/settings.py`:

```python
"""Bot settings — fail fast on missing required env vars."""

from uuid import UUID

from pydantic import Field
from pydantic_settings import BaseSettings


class BotSettings(BaseSettings):
    model_config = {"env_prefix": "ATLAS_DISCORD__", "case_sensitive": False}

    bot_token: str = Field(..., description="Discord bot token")
    guild_id: int = Field(..., description="Discord guild (server) ID for slash commands")
    internal_secret: str = Field(..., description="Shared secret for API↔bot HTTP calls")
    api_base_url: str = Field(default="http://api:8000", description="ATLAS API base URL")
    default_project_id: UUID = Field(..., description="Default project ID for all commands")
    notify_channel_id: str | None = Field(
        default=None, description="Fallback channel for ingestion-complete notifications"
    )
    internal_app_port: int = Field(default=8001, description="Port for the bot's internal FastAPI app")
```

- [ ] **Step 4: Create `chunker.py` and tests**

Create `apps/discord-bot/atlas_discord_bot/chunker.py`:

```python
"""Split text into Discord-safe chunks (≤1900 chars) at paragraph/sentence boundaries."""

from __future__ import annotations

MAX_CHUNK = 1900


def chunk_text(text: str, max_len: int = MAX_CHUNK) -> list[str]:
    """Split text into chunks of at most max_len chars.

    Prefers splitting at double-newline (paragraph), then single newline,
    then period+space, then falls back to hard truncation.
    """
    if not text:
        return []
    if len(text) <= max_len:
        return [text]

    chunks: list[str] = []
    remaining = text
    while remaining:
        if len(remaining) <= max_len:
            chunks.append(remaining)
            break
        # Try paragraph boundary
        idx = remaining.rfind("\n\n", 0, max_len)
        if idx > 0:
            chunks.append(remaining[: idx + 2].rstrip())
            remaining = remaining[idx + 2 :].lstrip()
            continue
        # Try line boundary
        idx = remaining.rfind("\n", 0, max_len)
        if idx > 0:
            chunks.append(remaining[:idx].rstrip())
            remaining = remaining[idx + 1 :].lstrip()
            continue
        # Try sentence boundary
        idx = remaining.rfind(". ", 0, max_len)
        if idx > 0:
            chunks.append(remaining[: idx + 1].rstrip())
            remaining = remaining[idx + 2 :].lstrip()
            continue
        # Hard split
        chunks.append(remaining[:max_len])
        remaining = remaining[max_len:]
    return [c for c in chunks if c]
```

Create `apps/discord-bot/tests/test_chunker.py`:

```python
"""Tests for the text chunker."""

import pytest
from atlas_discord_bot.chunker import chunk_text, MAX_CHUNK


def test_short_text_single_chunk():
    result = chunk_text("hello world")
    assert result == ["hello world"]


def test_empty_text_returns_empty_list():
    assert chunk_text("") == []


def test_exactly_max_len_is_single_chunk():
    text = "a" * MAX_CHUNK
    assert chunk_text(text) == [text]


def test_over_max_splits_at_paragraph():
    para = "word " * 200  # well over 1900 chars
    text = para.strip() + "\n\n" + "second paragraph"
    chunks = chunk_text(text)
    assert len(chunks) >= 2
    assert all(len(c) <= MAX_CHUNK for c in chunks)
    assert "second paragraph" in chunks[-1]


def test_over_max_splits_at_newline():
    line = "x" * 950
    text = line + "\n" + line + "\n" + line
    chunks = chunk_text(text)
    assert all(len(c) <= MAX_CHUNK for c in chunks)


def test_over_max_splits_at_sentence():
    sentence = "This is a sentence. " * 100
    chunks = chunk_text(sentence)
    assert all(len(c) <= MAX_CHUNK for c in chunks)


def test_over_max_hard_split_fallback():
    text = "x" * (MAX_CHUNK * 3)
    chunks = chunk_text(text)
    assert all(len(c) <= MAX_CHUNK for c in chunks)
    assert "".join(chunks) == text


def test_chunks_reassemble_to_original_content():
    import random, string
    random.seed(42)
    text = " ".join("".join(random.choices(string.ascii_lowercase, k=8)) for _ in range(500))
    chunks = chunk_text(text)
    assert all(len(c) <= MAX_CHUNK for c in chunks)
    assert len(chunks) > 1
```

- [ ] **Step 5: Run chunker tests**

```bash
cd apps/discord-bot && uv run pytest tests/test_chunker.py -v
```

Expected: 8 tests pass.

- [ ] **Step 6: Create `api_client.py`**

Create `apps/discord-bot/atlas_discord_bot/api_client.py`:

```python
"""Typed httpx wrapper for calls from the bot to the ATLAS API."""

from __future__ import annotations

from uuid import UUID

import httpx


class APIClient:
    def __init__(self, *, base_url: str, internal_secret: str, timeout: float = 60.0) -> None:
        self._base = base_url.rstrip("/")
        self._secret = internal_secret
        self._timeout = timeout

    def _headers(self) -> dict[str, str]:
        return {"X-Internal-Secret": self._secret}

    async def chat(self, project_id: UUID | str, prompt: str) -> str:
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.post(
                f"{self._base}/api/v1/internal/discord/chat",
                headers=self._headers(),
                json={"project_id": str(project_id), "prompt": prompt},
            )
            resp.raise_for_status()
            return resp.json()["text"]

    async def ingest_url(
        self, project_id: UUID | str, url: str, discord_channel_id: str | None = None
    ) -> dict:
        payload = {"project_id": str(project_id), "url": url}
        if discord_channel_id:
            payload["discord_channel_id"] = discord_channel_id
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f"{self._base}/api/v1/knowledge/ingest/url",
                json=payload,
            )
            resp.raise_for_status()
            return resp.json()

    async def get_pending_jobs(self) -> list[dict]:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                f"{self._base}/api/v1/internal/discord/jobs/pending",
                headers=self._headers(),
            )
            resp.raise_for_status()
            return resp.json()

    async def mark_notified(self, job_id: str) -> None:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                f"{self._base}/api/v1/internal/discord/jobs/{job_id}/mark_notified",
                headers=self._headers(),
            )
            resp.raise_for_status()

    async def mark_stale_notified(self) -> None:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                f"{self._base}/api/v1/internal/discord/jobs/mark_stale_notified",
                headers=self._headers(),
            )
            resp.raise_for_status()

    async def get_status(self) -> dict:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                f"{self._base}/api/v1/internal/discord/status",
                headers=self._headers(),
            )
            resp.raise_for_status()
            return resp.json()

    async def healthz(self) -> dict:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(f"{self._base}/health")
            resp.raise_for_status()
            return resp.json()
```

- [ ] **Step 7: Create `internal_app.py`**

Create `apps/discord-bot/atlas_discord_bot/internal_app.py`:

```python
"""Mini FastAPI app the bot runs for inbound API→bot calls.

Endpoints:
  POST /internal/discord/send  — agent-initiated message send
"""

from __future__ import annotations

import os

import discord
from atlas_discord_bot.chunker import chunk_text
from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel

app = FastAPI(title="atlas-discord-bot-internal")

# Injected by __main__ after the discord client is created
_bot: discord.Client | None = None


def set_bot(bot: discord.Client) -> None:
    global _bot
    _bot = bot


async def _require_secret(x_internal_secret: str = Header(alias="X-Internal-Secret")) -> None:
    expected = os.getenv("ATLAS_DISCORD__INTERNAL_SECRET")
    if not expected or x_internal_secret != expected:
        raise HTTPException(status_code=401, detail="unauthorized")


class SendRequest(BaseModel):
    channel_id: str
    body: str


class SendResponse(BaseModel):
    message_id: str | None = None


@app.post("/internal/discord/send", response_model=SendResponse)
async def send_message(
    req: SendRequest,
    _: None = Depends(_require_secret),
) -> SendResponse:
    if _bot is None:
        raise HTTPException(status_code=503, detail="bot not ready")
    channel = _bot.get_channel(int(req.channel_id))
    if channel is None:
        raise HTTPException(status_code=404, detail=f"channel {req.channel_id} not found")
    chunks = chunk_text(req.body)
    last_msg = None
    for chunk in chunks:
        last_msg = await channel.send(chunk)
    return SendResponse(message_id=str(last_msg.id) if last_msg else None)
```

Fix missing import at the top (FastAPI `Depends`):

```python
from fastapi import Depends, FastAPI, Header, HTTPException
```

- [ ] **Step 8: Create Dockerfile**

Create `apps/discord-bot/Dockerfile`:

```dockerfile
FROM python:3.13-slim AS base
WORKDIR /app
RUN pip install uv

FROM base AS deps
COPY pyproject.toml uv.lock ./
COPY packages/atlas-core packages/atlas-core
COPY apps/discord-bot/pyproject.toml apps/discord-bot/pyproject.toml
RUN uv sync --frozen --no-dev --package atlas-discord-bot

FROM deps AS runtime
COPY apps/discord-bot/atlas_discord_bot apps/discord-bot/atlas_discord_bot
WORKDIR /app/apps/discord-bot
CMD ["uv", "run", "python", "-m", "atlas_discord_bot"]
```

- [ ] **Step 9: Commit**

```bash
git add apps/discord-bot/ pyproject.toml
git commit -m "feat(discord-bot): scaffolding — settings, chunker, api_client, internal_app, Dockerfile"
```

---

## Task 8: Bot commands

**Files:**
- Create: `apps/discord-bot/atlas_discord_bot/commands/__init__.py`
- Create: `apps/discord-bot/atlas_discord_bot/commands/ask.py`
- Create: `apps/discord-bot/atlas_discord_bot/commands/ingest.py`
- Create: `apps/discord-bot/atlas_discord_bot/commands/status.py`
- Create: `apps/discord-bot/tests/test_commands.py`

- [ ] **Step 1: Write failing tests**

Create `apps/discord-bot/tests/test_commands.py`:

```python
"""Tests for bot slash command handlers."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID

import pytest

from atlas_discord_bot.commands.ask import ask_handler
from atlas_discord_bot.commands.ingest import ingest_handler
from atlas_discord_bot.commands.status import status_handler
from atlas_discord_bot.chunker import MAX_CHUNK


def _make_interaction():
    interaction = MagicMock()
    interaction.channel_id = 99999
    interaction.response = MagicMock()
    interaction.response.defer = AsyncMock()
    interaction.followup = MagicMock()
    interaction.followup.send = AsyncMock()
    return interaction


PROJECT_ID = UUID("00000000-0000-0000-0000-000000000001")


@pytest.mark.asyncio
async def test_ask_happy_path_sends_reply():
    interaction = _make_interaction()
    api = AsyncMock()
    api.chat.return_value = "the answer"

    await ask_handler(interaction, "what is X", api_client=api, project_id=PROJECT_ID)

    interaction.response.defer.assert_called_once()
    interaction.followup.send.assert_called_once_with("the answer")


@pytest.mark.asyncio
async def test_ask_long_reply_chunked():
    interaction = _make_interaction()
    api = AsyncMock()
    api.chat.return_value = "word " * 500  # well over 1900 chars

    await ask_handler(interaction, "essay", api_client=api, project_id=PROJECT_ID)

    calls = interaction.followup.send.call_args_list
    assert len(calls) > 1
    for call in calls:
        assert len(call[0][0]) <= MAX_CHUNK


@pytest.mark.asyncio
async def test_ask_api_error_posts_error_message():
    interaction = _make_interaction()
    api = AsyncMock()
    api.chat.side_effect = Exception("500 Internal Server Error")

    await ask_handler(interaction, "fail", api_client=api, project_id=PROJECT_ID)

    text = interaction.followup.send.call_args[0][0]
    assert text.startswith("❌")


@pytest.mark.asyncio
async def test_ingest_happy_path_posts_queued():
    interaction = _make_interaction()
    api = AsyncMock()
    api.ingest_url.return_value = {"id": "job-abc", "status": "pending"}

    await ingest_handler(
        interaction, "https://example.com", api_client=api, project_id=PROJECT_ID
    )

    interaction.response.defer.assert_called_once()
    text = interaction.followup.send.call_args[0][0]
    assert "queued" in text.lower() or "job-abc" in text


@pytest.mark.asyncio
async def test_ingest_bad_url_posts_error():
    interaction = _make_interaction()
    api = AsyncMock()
    api.ingest_url.side_effect = Exception("400 Bad Request")

    await ingest_handler(
        interaction, "not-a-url", api_client=api, project_id=PROJECT_ID
    )

    text = interaction.followup.send.call_args[0][0]
    assert text.startswith("❌")


@pytest.mark.asyncio
async def test_status_posts_embed_with_postgres():
    interaction = _make_interaction()
    api = AsyncMock()
    api.healthz.return_value = {"status": "ok"}
    api.get_status.return_value = {"postgres": "ok", "running_jobs": 2}

    await status_handler(interaction, api_client=api)

    interaction.response.defer.assert_called_once()
    sent = interaction.followup.send.call_args
    # Either an embed or a string containing status info
    assert sent is not None
```

- [ ] **Step 2: Run — confirm ImportError**

```bash
cd apps/discord-bot && uv run pytest tests/test_commands.py -q 2>&1 | head -5
```

Expected: `ModuleNotFoundError: No module named 'atlas_discord_bot.commands'`

- [ ] **Step 3: Create command modules**

```bash
mkdir -p apps/discord-bot/atlas_discord_bot/commands
touch apps/discord-bot/atlas_discord_bot/commands/__init__.py
```

Create `apps/discord-bot/atlas_discord_bot/commands/ask.py`:

```python
"""Handler for /atlas ask."""

from __future__ import annotations

from typing import Any
from uuid import UUID

import discord
from atlas_discord_bot.api_client import APIClient
from atlas_discord_bot.chunker import chunk_text


async def ask_handler(
    interaction: Any,
    prompt: str,
    *,
    api_client: APIClient,
    project_id: UUID,
) -> None:
    await interaction.response.defer()
    try:
        text = await api_client.chat(project_id, prompt)
    except TimeoutError:
        await interaction.followup.send("❌ chat timed out")
        return
    except Exception as e:
        await interaction.followup.send(f"❌ chat failed: {e}")
        return
    for chunk in chunk_text(text):
        await interaction.followup.send(chunk)


def setup(tree: discord.app_commands.CommandTree, guild: discord.Object, *, settings, api_client: APIClient) -> None:
    @tree.command(name="ask", description="Ask ATLAS a question", guild=guild)
    @discord.app_commands.describe(prompt="Your question for the ATLAS agent")
    async def _ask(interaction: discord.Interaction, prompt: str) -> None:
        await ask_handler(interaction, prompt, api_client=api_client, project_id=settings.default_project_id)
```

Create `apps/discord-bot/atlas_discord_bot/commands/ingest.py`:

```python
"""Handler for /atlas ingest."""

from __future__ import annotations

from typing import Any
from uuid import UUID

import discord
from atlas_discord_bot.api_client import APIClient


async def ingest_handler(
    interaction: Any,
    url: str,
    *,
    api_client: APIClient,
    project_id: UUID,
) -> None:
    await interaction.response.defer()
    channel_id = str(interaction.channel_id)
    try:
        job = await api_client.ingest_url(project_id, url, discord_channel_id=channel_id)
    except Exception as e:
        await interaction.followup.send(f"❌ ingest failed: {e}")
        return
    job_id = job.get("id", "unknown")
    await interaction.followup.send(f"📥 ingestion queued (job `{job_id}`)")


def setup(tree: discord.app_commands.CommandTree, guild: discord.Object, *, settings, api_client: APIClient) -> None:
    @tree.command(name="ingest", description="Ingest a URL into the ATLAS knowledge base", guild=guild)
    @discord.app_commands.describe(url="URL to ingest")
    async def _ingest(interaction: discord.Interaction, url: str) -> None:
        await ingest_handler(interaction, url, api_client=api_client, project_id=settings.default_project_id)
```

Create `apps/discord-bot/atlas_discord_bot/commands/status.py`:

```python
"""Handler for /atlas status."""

from __future__ import annotations

from typing import Any

import discord
from atlas_discord_bot.api_client import APIClient


async def status_handler(interaction: Any, *, api_client: APIClient) -> None:
    await interaction.response.defer()
    try:
        health = await api_client.healthz()
        status = await api_client.get_status()
    except Exception as e:
        await interaction.followup.send(f"❌ status check failed: {e}")
        return

    embed = discord.Embed(title="ATLAS Status", color=0x2ECC71)
    embed.add_field(name="API", value=health.get("status", "unknown"), inline=True)
    embed.add_field(name="Postgres", value=status.get("postgres", "unknown"), inline=True)
    embed.add_field(name="Running jobs", value=str(status.get("running_jobs", 0)), inline=True)
    await interaction.followup.send(embed=embed)


def setup(tree: discord.app_commands.CommandTree, guild: discord.Object, *, settings, api_client: APIClient) -> None:
    @tree.command(name="status", description="Show ATLAS system status", guild=guild)
    async def _status(interaction: discord.Interaction) -> None:
        await status_handler(interaction, api_client=api_client)
```

- [ ] **Step 4: Run tests**

```bash
cd apps/discord-bot && uv run pytest tests/test_commands.py -v
```

Expected: all 6 tests pass.

- [ ] **Step 5: Commit**

```bash
git add apps/discord-bot/atlas_discord_bot/commands/ apps/discord-bot/tests/test_commands.py
git commit -m "feat(discord-bot): ask/ingest/status command handlers"
```

---

## Task 9: Bot notification poller

**Files:**
- Create: `apps/discord-bot/atlas_discord_bot/poller.py`
- Create: `apps/discord-bot/tests/test_poller.py`

- [ ] **Step 1: Write failing tests**

Create `apps/discord-bot/tests/test_poller.py`:

```python
"""Tests for the notification poller."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from atlas_discord_bot.poller import poll_once


@pytest.fixture
def mock_bot():
    bot = MagicMock()
    channel = AsyncMock()
    channel.send = AsyncMock(return_value=MagicMock(id=12345))
    bot.get_channel = MagicMock(return_value=channel)
    return bot, channel


@pytest.mark.asyncio
async def test_poll_once_completed_job_sends_notification(mock_bot):
    bot, channel = mock_bot
    api = AsyncMock()
    api.get_pending_jobs.return_value = [
        {"id": "job-1", "status": "completed", "source_filename": "https://example.com", "discord_channel_id": "999", "error": None}
    ]
    api.mark_notified = AsyncMock()

    await poll_once(bot=bot, api_client=api, fallback_channel_id="888")

    channel.send.assert_called_once()
    sent_text = channel.send.call_args[0][0]
    assert "✅" in sent_text
    api.mark_notified.assert_called_once_with("job-1")


@pytest.mark.asyncio
async def test_poll_once_failed_job_sends_error_notification(mock_bot):
    bot, channel = mock_bot
    api = AsyncMock()
    api.get_pending_jobs.return_value = [
        {"id": "job-2", "status": "failed", "source_filename": "https://bad.com", "discord_channel_id": None, "error": "timeout"}
    ]
    api.mark_notified = AsyncMock()

    await poll_once(bot=bot, api_client=api, fallback_channel_id="888")

    bot.get_channel.assert_called_with(888)
    sent_text = channel.send.call_args[0][0]
    assert "❌" in sent_text
    api.mark_notified.assert_called_once_with("job-2")


@pytest.mark.asyncio
async def test_poll_once_no_fallback_and_no_channel_id_skips(mock_bot):
    bot, channel = mock_bot
    api = AsyncMock()
    api.get_pending_jobs.return_value = [
        {"id": "job-3", "status": "completed", "source_filename": "f", "discord_channel_id": None, "error": None}
    ]
    api.mark_notified = AsyncMock()

    await poll_once(bot=bot, api_client=api, fallback_channel_id=None)

    channel.send.assert_not_called()
    # still marks notified to prevent re-processing
    api.mark_notified.assert_called_once_with("job-3")


@pytest.mark.asyncio
async def test_poll_once_api_error_does_not_raise(mock_bot):
    bot, _ = mock_bot
    api = AsyncMock()
    api.get_pending_jobs.side_effect = Exception("network error")

    # Should not raise
    await poll_once(bot=bot, api_client=api, fallback_channel_id="888")


@pytest.mark.asyncio
async def test_poll_once_empty_returns_immediately(mock_bot):
    bot, channel = mock_bot
    api = AsyncMock()
    api.get_pending_jobs.return_value = []

    await poll_once(bot=bot, api_client=api, fallback_channel_id=None)

    channel.send.assert_not_called()
```

- [ ] **Step 2: Run — confirm ImportError**

```bash
cd apps/discord-bot && uv run pytest tests/test_poller.py -q 2>&1 | head -5
```

Expected: `ModuleNotFoundError: No module named 'atlas_discord_bot.poller'`

- [ ] **Step 3: Create `poller.py`**

Create `apps/discord-bot/atlas_discord_bot/poller.py`:

```python
"""Ingestion-complete notification poller.

Runs every 10 seconds. Queries /internal/discord/jobs/pending for completed/failed
jobs with notified_at IS NULL (freshness enforced server-side to last 10 minutes).
Posts a notification to the job's discord_channel_id or the fallback channel, then
marks the job notified.

On startup, call api_client.mark_stale_notified() to silence old unnotified jobs.
"""

from __future__ import annotations

import asyncio
import structlog
from typing import Any

import discord
from atlas_discord_bot.api_client import APIClient
from atlas_discord_bot.chunker import chunk_text

log = structlog.get_logger("atlas.discord.poller")

POLL_INTERVAL = 10  # seconds


async def poll_once(
    *,
    bot: Any,  # discord.Client
    api_client: APIClient,
    fallback_channel_id: str | None,
) -> None:
    try:
        jobs = await api_client.get_pending_jobs()
    except Exception as e:
        log.warning("discord.poller.fetch_failed", error=str(e))
        return

    for job in jobs:
        job_id = job["id"]
        status = job["status"]
        filename = job.get("source_filename") or "unknown source"
        error = job.get("error")
        channel_id_str = job.get("discord_channel_id") or fallback_channel_id

        if channel_id_str:
            channel = bot.get_channel(int(channel_id_str))
            if channel is not None:
                if status == "completed":
                    text = f"✅ ingested `{filename}`"
                else:
                    text = f"❌ ingestion failed for `{filename}`: {error or 'unknown error'}"
                try:
                    await channel.send(text)
                except Exception as e:
                    log.warning("discord.poller.send_failed", job_id=job_id, error=str(e))

        try:
            await api_client.mark_notified(job_id)
        except Exception as e:
            log.warning("discord.poller.mark_failed", job_id=job_id, error=str(e))


async def run_poller(
    *,
    bot: Any,
    api_client: APIClient,
    fallback_channel_id: str | None,
) -> None:
    """Infinite polling loop. Run as an asyncio task."""
    while True:
        await poll_once(bot=bot, api_client=api_client, fallback_channel_id=fallback_channel_id)
        await asyncio.sleep(POLL_INTERVAL)
```

- [ ] **Step 4: Run tests**

```bash
cd apps/discord-bot && uv run pytest tests/test_poller.py -v
```

Expected: all 5 tests pass.

- [ ] **Step 5: Commit**

```bash
git add apps/discord-bot/atlas_discord_bot/poller.py apps/discord-bot/tests/test_poller.py
git commit -m "feat(discord-bot): ingestion-complete notification poller"
```

---

## Task 10: Bot entry point + compose wiring

**Files:**
- Create: `apps/discord-bot/atlas_discord_bot/__main__.py`
- Modify: `infra/docker-compose.yml`

- [ ] **Step 1: Create `__main__.py`**

Create `apps/discord-bot/atlas_discord_bot/__main__.py`:

```python
"""Entry point for the ATLAS Discord bot.

Starts three concurrent asyncio tasks:
  1. discord.py gateway connection (commands, events)
  2. uvicorn serving internal_app on port 8001 (inbound API→bot)
  3. notification_poller (10s tick)
"""

from __future__ import annotations

import asyncio
import logging

import discord
import structlog
import uvicorn
from atlas_discord_bot.api_client import APIClient
from atlas_discord_bot.commands import ask as ask_cmd
from atlas_discord_bot.commands import ingest as ingest_cmd
from atlas_discord_bot.commands import status as status_cmd
from atlas_discord_bot.internal_app import app as internal_app
from atlas_discord_bot.internal_app import set_bot
from atlas_discord_bot.poller import run_poller
from atlas_discord_bot.settings import BotSettings

log = structlog.get_logger("atlas.discord")


async def main() -> None:
    settings = BotSettings()

    intents = discord.Intents.default()
    client = discord.Client(intents=intents)
    tree = discord.app_commands.CommandTree(client)
    guild = discord.Object(id=settings.guild_id)

    api_client = APIClient(
        base_url=settings.api_base_url,
        internal_secret=settings.internal_secret,
    )

    ask_cmd.setup(tree, guild, settings=settings, api_client=api_client)
    ingest_cmd.setup(tree, guild, settings=settings, api_client=api_client)
    status_cmd.setup(tree, guild, settings=settings, api_client=api_client)

    @client.event
    async def on_ready() -> None:
        log.info("discord.bot.ready", user=str(client.user))
        await tree.sync(guild=guild)
        log.info("discord.commands.synced", guild_id=settings.guild_id)
        # On startup: silence stale unnotified jobs to avoid notification flood
        try:
            await api_client.mark_stale_notified()
        except Exception as e:
            log.warning("discord.stale_notified.failed", error=str(e))

    set_bot(client)

    uvicorn_config = uvicorn.Config(
        app=internal_app,
        host="0.0.0.0",
        port=settings.internal_app_port,
        loop="none",
        log_level="warning",
    )
    server = uvicorn.Server(uvicorn_config)

    fallback = settings.notify_channel_id

    await asyncio.gather(
        client.start(settings.bot_token),
        server.serve(),
        run_poller(bot=client, api_client=api_client, fallback_channel_id=fallback),
        return_exceptions=True,
    )


if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 2: Add discord-bot service to compose**

In `infra/docker-compose.yml`, add after the `web` service block:

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
      - "8001"
```

- [ ] **Step 3: Document required env vars**

Add to your `.env.example` (or equivalent deploy docs) the following Discord-specific vars:

```
ATLAS_DISCORD__BOT_TOKEN=<your-discord-bot-token>
ATLAS_DISCORD__GUILD_ID=<your-discord-server-id>
ATLAS_DISCORD__INTERNAL_SECRET=<any-random-secret-shared-with-api>
ATLAS_DISCORD__DEFAULT_PROJECT_ID=<uuid-of-your-default-atlas-project>
ATLAS_DISCORD__NOTIFY_CHANNEL_ID=<channel-id-for-fallback-notifications>  # optional
```

For the API side, `ATLAS_DISCORD__INTERNAL_SECRET` must equal the same value.

For the `DiscordPlugin` credential row, register it once via the REST API:

```bash
curl -X PUT http://localhost:8000/api/v1/plugins/discord/credentials/default \
  -H "Content-Type: application/json" \
  -d '{"default_channel_id": "<your-discord-channel-id>"}'
```

For the plugin to activate, add `"discord"` to the project's `enabled_plugins` array:

```sql
UPDATE projects SET enabled_plugins = array_append(enabled_plugins, 'discord') WHERE id = '<project-uuid>';
```

- [ ] **Step 4: Commit**

```bash
git add apps/discord-bot/atlas_discord_bot/__main__.py infra/docker-compose.yml
git commit -m "feat(discord-bot): entry point + compose wiring"
```

---

## Task 11: Manual integration smoke

Run against a real Discord test guild with real credentials. Verify each acceptance criterion:

- [ ] **AC1:** `docker compose up discord-bot` starts cleanly; bot appears online in guild.
- [ ] **AC2:** `/atlas status` → embed shows `API: ok`, `Postgres: ok`, `Running jobs: 0`.
- [ ] **AC3:** `/atlas ask "hello"` → bot defers, posts reply within 60 seconds.
- [ ] **AC4:** `/atlas ask` with a prompt that produces > 1900-char reply → multiple followup messages, all ≤ 1900 chars.
- [ ] **AC5:** `/atlas ingest url:https://example.com` → bot replies `📥 ingestion queued (job <id>)`; within ~15 seconds a `✅ ingested` message appears in the same channel.
- [ ] **AC6:** From React UI, with Discord plugin enabled on a project and Anthropic provider selected, ask agent to send a Discord message → tool preview chip appears → user types confirm call → message appears in Discord.
- [ ] **AC7:** Same as AC6 but via `/atlas ask "send a message to discord saying hello"` → message posts directly without confirmation prompt.

---

## Self-Review

**Spec coverage check:**

| Spec requirement | Task |
|---|---|
| Alembic migration: discord_channel_id + notified_at | T1 |
| Interactive contextvar | T2 |
| Agent runner extracted; WS handler refactored | T3 |
| Internal discord router (chat, jobs, status) | T4 |
| Shared-secret guard | T4 |
| DiscordPlugin with send_message + confirmation gate | T5 |
| Non-interactive gate bypass | T5 |
| URL ingest discord_channel_id | T6 |
| Bot scaffolding (settings, chunker, api_client, internal_app) | T7 |
| /atlas ask — chunked followups | T8 |
| /atlas ingest | T8 |
| /atlas status (non-LLM) | T8 |
| Notification poller (10s, freshness filter, fallback channel) | T9 |
| Bot entry point + compose | T10 |
| Startup backlog cleanup (mark_stale_notified) | T10 |
| Manual smoke | T11 |

**Placeholder scan:** No TBDs found.

**Type consistency check:**
- `AgentEvent.data` dict keys (`text`, `tool_calls`, `usage`, `latency_ms`) match WS handler read sites in T3's refactored `ws/chat.py`.
- `DiscordPlugin.invoke` raises on error; registry wraps into `ToolResult(error=...)` — consistent with Plan 1 contract.
- `APIClient.chat()` returns `str`; `ask_handler` passes it to `chunk_text(text)` — types match.
- `PendingJob` model fields (`id`, `status`, `source_filename`, `discord_channel_id`, `error`) match what `poller.poll_once` reads — consistent.
- `chunk_text` returns `list[str]`; callers iterate and send each — consistent.
