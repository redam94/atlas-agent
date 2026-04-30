# ATLAS Phase 3 — Plan 1: Plugin Framework Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the `atlas-plugins` framework — `AtlasPlugin` ABC, `PluginRegistry`, encrypted `CredentialStore`, REST endpoints, Anthropic tool-use loop in the chat WS handler, `FakePlugin` for tests. No real integrations land here.

**Architecture:** New `atlas-plugins` package reuses existing `atlas_core.models.llm.ToolSchema/ToolCall/ToolResult` shapes (Phase 1 pre-laid them). Lifespan builds a `CredentialStore` (Fernet + Postgres) → constructs each plugin from `REGISTERED_PLUGINS` → warms a `PluginRegistry`. Chat WS handler runs an Anthropic-only tool-use loop bounded at 10 turns; the existing `AnthropicProvider` is extended to emit `TOOL_CALL` events from streaming `tool_use` content blocks. WS already has `chat.tool_use` / `chat.tool_result` event types from Phase 1; Plan 1 wires them through.

**Tech Stack:** FastAPI + SQLAlchemy + Alembic (existing), `cryptography.Fernet` (new dep), `anthropic` SDK (existing), React 19 + Zustand (existing).

**Spec:** `docs/superpowers/specs/2026-04-29-atlas-phase-3-plan-1-plugin-framework-design.md`
**Phase spec:** `docs/superpowers/specs/2026-04-29-atlas-phase-3-plugins-design.md`

---

## File Map

**Backend (create):**
- `packages/atlas-plugins/pyproject.toml`
- `packages/atlas-plugins/atlas_plugins/__init__.py`
- `packages/atlas-plugins/atlas_plugins/base.py` — `AtlasPlugin` ABC + `HealthStatus`, `PluginInfo`
- `packages/atlas-plugins/atlas_plugins/errors.py` — `CredentialNotFound`, `CredentialDecryptError`
- `packages/atlas-plugins/atlas_plugins/credentials.py` — `CredentialStore`
- `packages/atlas-plugins/atlas_plugins/registry.py` — `PluginRegistry` + `REGISTERED_PLUGINS`
- `packages/atlas-plugins/atlas_plugins/_fake.py` — `FakePlugin`
- `packages/atlas-plugins/atlas_plugins/tests/conftest.py`
- `packages/atlas-plugins/atlas_plugins/tests/test_base.py`
- `packages/atlas-plugins/atlas_plugins/tests/test_credentials.py`
- `packages/atlas-plugins/atlas_plugins/tests/test_registry.py`
- `packages/atlas-plugins/atlas_plugins/tests/test_fake.py`
- `infra/alembic/versions/0007_create_plugin_credentials_and_enabled_plugins.py`
- `apps/api/atlas_api/routers/plugins.py`
- `apps/api/atlas_api/tests/test_plugins_router.py`
- `apps/api/atlas_api/tests/test_ws_chat_tool_use.py`
- `apps/api/atlas_api/tests/test_ws_chat_tool_use_real.py` — opt-in real-Anthropic

**Backend (modify):**
- `packages/atlas-core/atlas_core/db/orm.py` — add `PluginCredentialORM`, add `enabled_plugins` to `ProjectORM`
- `packages/atlas-core/atlas_core/providers/anthropic.py` — emit `ModelEvent(type=TOOL_CALL)` when stream sees `tool_use` content blocks
- `packages/atlas-core/atlas_core/providers/_fake.py` — extend the fake provider to support scripted `TOOL_CALL` events for chat-handler tests
- `apps/api/atlas_api/main.py` — register plugins router; build `CredentialStore` + `PluginRegistry` on lifespan
- `apps/api/atlas_api/deps.py` — `get_plugin_registry`, `get_credential_store`
- `apps/api/atlas_api/ws/chat.py` — Anthropic tool-use loop + 10-turn cap + emit `chat.tool_use` / `chat.tool_result` events

**Frontend (create):**
- `apps/web/src/components/chat/tool-call-chip.tsx` + `.test.tsx`

**Frontend (modify):**
- `apps/web/src/stores/chat-store.ts` (or wherever the chat WS event handler lives) + matching `.test.ts`
- `apps/web/src/components/chat/message-renderer.tsx` (or equivalent) — render the chip strip above text

---

## Phase A — Backend foundation

### Task 1: atlas-plugins package scaffold

**Files:**
- Create: `packages/atlas-plugins/pyproject.toml`
- Create: `packages/atlas-plugins/atlas_plugins/__init__.py`
- Create: `packages/atlas-plugins/atlas_plugins/base.py`
- Create: `packages/atlas-plugins/atlas_plugins/errors.py`
- Create: `packages/atlas-plugins/atlas_plugins/tests/__init__.py`
- Create: `packages/atlas-plugins/atlas_plugins/tests/conftest.py`
- Create: `packages/atlas-plugins/atlas_plugins/tests/test_base.py`
- Modify: root `pyproject.toml` if it lists workspace members (mirror how `atlas-graph` is registered)

- [ ] **Step 1: Write the pyproject**

`packages/atlas-plugins/pyproject.toml`:

```toml
[project]
name = "atlas-plugins"
version = "0.1.0"
description = "ATLAS plugins: framework + integrations"
requires-python = ">=3.13"
dependencies = [
    "atlas-core",
    "cryptography>=42.0",
    "structlog>=24.4",
    "sqlalchemy>=2.0",
]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["atlas_plugins"]
```

If the root `pyproject.toml` declares workspace members (check `[tool.uv.workspace]`), add `packages/atlas-plugins` there alongside the others.

- [ ] **Step 2: Write `errors.py`**

```python
"""Plugin-framework exceptions."""


class CredentialNotFound(Exception):
    """Raised when CredentialStore.get cannot find the (plugin_name, account_id) row."""


class CredentialDecryptError(Exception):
    """Raised when Fernet decrypt fails (wrong master key, ciphertext tampering)."""
```

- [ ] **Step 3: Write `base.py` with the ABC + `HealthStatus` + `PluginInfo`**

```python
"""AtlasPlugin ABC and supporting models."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any

from atlas_core.models.base import AtlasModel
from atlas_core.models.llm import ToolSchema

if TYPE_CHECKING:
    from atlas_plugins.credentials import CredentialStore


class HealthStatus(AtlasModel):
    """Result of a plugin health check."""
    ok: bool
    detail: str | None = None


class PluginInfo(AtlasModel):
    """One row in GET /api/v1/plugins."""
    name: str
    description: str
    tool_count: int
    health: HealthStatus


class AtlasPlugin(ABC):
    """Plugins implement this. Two required overrides: get_tools and invoke."""

    name: str = ""           # set on the subclass; "fake", "github", etc.
    description: str = ""

    def __init__(self, credentials: CredentialStore) -> None:
        if not self.name:
            raise ValueError(f"{self.__class__.__name__}.name must be set")
        self._credentials = credentials

    async def _get_credentials(self, account_id: str = "default") -> dict[str, Any]:
        """Lazy fetch — called per-invoke so credential rotations take effect immediately.

        Raises ``CredentialNotFound`` if no row exists.
        """
        return await self._credentials.get(self.name, account_id)

    @abstractmethod
    def get_tools(self) -> list[ToolSchema]:
        """Return the tool schemas this plugin exposes.

        Each ToolSchema.name MUST start with f"{self.name}." and ToolSchema.plugin
        MUST equal self.name.
        """

    @abstractmethod
    async def invoke(self, tool_name: str, args: dict[str, Any]) -> Any:
        """Execute the tool. Return the result value. Raise on failure.

        The PluginRegistry catches exceptions and converts to a ToolResult with
        ``error=str(e)``; the plugin doesn't construct a ToolResult itself.
        """

    async def health(self) -> HealthStatus:
        """Default: ok if at least one credential row exists."""
        accounts = await self._credentials.list(self.name)
        if not accounts:
            return HealthStatus(ok=False, detail="no credentials registered")
        return HealthStatus(ok=True)
```

- [ ] **Step 4: Write `__init__.py` with the public API**

```python
"""ATLAS plugin framework."""

from atlas_plugins.base import AtlasPlugin, HealthStatus, PluginInfo
from atlas_plugins.errors import CredentialDecryptError, CredentialNotFound

__all__ = [
    "AtlasPlugin",
    "CredentialDecryptError",
    "CredentialNotFound",
    "HealthStatus",
    "PluginInfo",
]
```

- [ ] **Step 5: Write `tests/conftest.py` (placeholder for now — fleshed out in later tasks)**

```python
"""Shared fixtures for atlas-plugins tests."""

import pytest


@pytest.fixture
def fernet_key() -> str:
    """A deterministic Fernet key for tests. NOT for production."""
    # Fernet keys are 32 url-safe base64 bytes.
    return "VGhpcy1pcy1hLXRlc3Qta2V5LXdpdGgtMzItYnl0ZXM="
```

- [ ] **Step 6: Write the failing test (no concrete plugin yet → import smoke + ABC behavior)**

`packages/atlas-plugins/atlas_plugins/tests/test_base.py`:

```python
"""Tests for AtlasPlugin ABC."""

from unittest.mock import AsyncMock

import pytest

from atlas_plugins import AtlasPlugin, HealthStatus
from atlas_core.models.llm import ToolSchema


class _StubPlugin(AtlasPlugin):
    name = "stub"
    description = "test stub"

    def get_tools(self) -> list[ToolSchema]:
        return [ToolSchema(
            name="stub.echo", description="echo", parameters={}, plugin="stub"
        )]

    async def invoke(self, tool_name, args):
        return {"echo": args.get("text")}


def test_subclass_constructs_with_credential_store():
    cred = AsyncMock()
    plugin = _StubPlugin(credentials=cred)
    assert plugin.name == "stub"


def test_subclass_without_name_raises():
    class _NoName(AtlasPlugin):
        # name not set
        def get_tools(self):
            return []
        async def invoke(self, tool_name, args):
            return None

    cred = AsyncMock()
    with pytest.raises(ValueError, match="name must be set"):
        _NoName(credentials=cred)


@pytest.mark.asyncio
async def test_get_credentials_passes_through_to_store():
    cred = AsyncMock()
    cred.get.return_value = {"foo": "bar"}
    plugin = _StubPlugin(credentials=cred)

    result = await plugin._get_credentials(account_id="alice")

    cred.get.assert_awaited_once_with("stub", "alice")
    assert result == {"foo": "bar"}


@pytest.mark.asyncio
async def test_default_health_ok_when_credentials_exist():
    cred = AsyncMock()
    cred.list.return_value = ["default", "alice"]
    plugin = _StubPlugin(credentials=cred)

    health = await plugin.health()

    assert health.ok is True
    cred.list.assert_awaited_once_with("stub")


@pytest.mark.asyncio
async def test_default_health_degraded_when_no_credentials():
    cred = AsyncMock()
    cred.list.return_value = []
    plugin = _StubPlugin(credentials=cred)

    health = await plugin.health()

    assert health.ok is False
    assert "no credentials" in (health.detail or "")
```

- [ ] **Step 7: Run the tests**

```bash
uv pip install -e packages/atlas-plugins
uv run pytest packages/atlas-plugins -v
```

Expected: 5 PASS.

- [ ] **Step 8: Commit**

```bash
git add packages/atlas-plugins/ pyproject.toml
git commit -m "feat(plugins): atlas-plugins package scaffold + AtlasPlugin ABC"
```

---

### Task 2: CredentialStore (Fernet + Postgres)

**Files:**
- Create: `packages/atlas-plugins/atlas_plugins/credentials.py`
- Create: `packages/atlas-plugins/atlas_plugins/tests/test_credentials.py`
- Modify: `packages/atlas-plugins/atlas_plugins/__init__.py` to re-export `CredentialStore`

This task uses an **in-memory backing dict** for tests. The actual Postgres ORM/migration ships in Task 5; the CredentialStore is parameterized so tests don't need DB.

- [ ] **Step 1: Write the failing tests**

`packages/atlas-plugins/atlas_plugins/tests/test_credentials.py`:

```python
"""Tests for CredentialStore (Fernet + in-memory backing for unit tests)."""

import pytest
from cryptography.fernet import Fernet

from atlas_plugins import CredentialDecryptError, CredentialNotFound
from atlas_plugins.credentials import CredentialStore, InMemoryBackend


@pytest.fixture
def store():
    backend = InMemoryBackend()
    return CredentialStore(backend=backend, master_key=Fernet.generate_key().decode())


@pytest.fixture
def safe_mode_store():
    backend = InMemoryBackend()
    return CredentialStore(backend=backend, master_key=None)


@pytest.mark.asyncio
async def test_set_and_get_round_trip(store):
    payload = {"token": "abc123", "scope": ["read", "write"]}
    await store.set("github", "default", payload)
    got = await store.get("github", "default")
    assert got == payload


@pytest.mark.asyncio
async def test_get_missing_raises_credential_not_found(store):
    with pytest.raises(CredentialNotFound):
        await store.get("github", "default")


@pytest.mark.asyncio
async def test_set_upsert_overwrites_payload(store):
    await store.set("github", "default", {"token": "old"})
    await store.set("github", "default", {"token": "new"})
    got = await store.get("github", "default")
    assert got == {"token": "new"}


@pytest.mark.asyncio
async def test_list_returns_account_ids_only(store):
    await store.set("gmail", "alice@example.com", {"refresh": "a"})
    await store.set("gmail", "bob@example.com", {"refresh": "b"})
    await store.set("github", "default", {"token": "t"})

    accounts = await store.list("gmail")
    assert sorted(accounts) == ["alice@example.com", "bob@example.com"]
    # No plaintext crosses this method — we only assert on account_ids.


@pytest.mark.asyncio
async def test_delete_removes_row(store):
    await store.set("github", "default", {"token": "t"})
    await store.delete("github", "default")
    with pytest.raises(CredentialNotFound):
        await store.get("github", "default")


@pytest.mark.asyncio
async def test_delete_missing_is_idempotent(store):
    # Should not raise.
    await store.delete("github", "default")


@pytest.mark.asyncio
async def test_decrypt_with_wrong_key_raises(store):
    payload = {"token": "secret"}
    await store.set("github", "default", payload)

    other_store = CredentialStore(
        backend=store._backend,  # same backing data
        master_key=Fernet.generate_key().decode(),  # different key
    )
    with pytest.raises(CredentialDecryptError):
        await other_store.get("github", "default")


@pytest.mark.asyncio
async def test_safe_mode_set_noops(safe_mode_store):
    await safe_mode_store.set("github", "default", {"token": "t"})
    # No exception, but also no readable data.
    with pytest.raises(CredentialNotFound):
        await safe_mode_store.get("github", "default")


@pytest.mark.asyncio
async def test_safe_mode_list_returns_empty(safe_mode_store):
    accounts = await safe_mode_store.list("github")
    assert accounts == []


@pytest.mark.asyncio
async def test_safe_mode_get_raises_credential_not_found(safe_mode_store):
    with pytest.raises(CredentialNotFound):
        await safe_mode_store.get("github", "default")
```

- [ ] **Step 2: Run the tests (fail)**

```bash
uv run pytest packages/atlas-plugins/atlas_plugins/tests/test_credentials.py -v
```

Expected: import errors / module not found.

- [ ] **Step 3: Implement `credentials.py`**

```python
"""Encrypted credential store backing for atlas-plugins.

Storage backend is pluggable via the ``CredentialBackend`` Protocol so tests
can use an in-memory dict; the production binding (Task 7) wires the
SQLAlchemy backend that talks to ``plugin_credentials``.
"""

from __future__ import annotations

import json
from typing import Any, Protocol

import structlog
from cryptography.fernet import Fernet, InvalidToken

from atlas_plugins.errors import CredentialDecryptError, CredentialNotFound

log = structlog.get_logger("atlas.plugins.credentials")


class CredentialBackend(Protocol):
    """Async storage interface for the encrypted credential store."""

    async def upsert(self, plugin_name: str, account_id: str, ciphertext: bytes) -> None: ...
    async def fetch(self, plugin_name: str, account_id: str) -> bytes | None: ...
    async def list_accounts(self, plugin_name: str) -> list[str]: ...
    async def remove(self, plugin_name: str, account_id: str) -> None: ...


class InMemoryBackend:
    """In-memory backend for tests. Production uses the SQLAlchemy backend."""

    def __init__(self) -> None:
        self._data: dict[tuple[str, str], bytes] = {}

    async def upsert(self, plugin_name: str, account_id: str, ciphertext: bytes) -> None:
        self._data[(plugin_name, account_id)] = ciphertext

    async def fetch(self, plugin_name: str, account_id: str) -> bytes | None:
        return self._data.get((plugin_name, account_id))

    async def list_accounts(self, plugin_name: str) -> list[str]:
        return [aid for (pname, aid) in self._data.keys() if pname == plugin_name]

    async def remove(self, plugin_name: str, account_id: str) -> None:
        self._data.pop((plugin_name, account_id), None)


class CredentialStore:
    """Fernet-encrypted credential storage with safe-mode for missing keys.

    With ``master_key=None`` the store enters safe-mode: ``set`` no-ops with a
    WARN log per call, ``get`` raises ``CredentialNotFound``, ``list`` returns
    ``[]``, ``delete`` no-ops. This lets local dev boot without secrets.
    """

    def __init__(self, *, backend: CredentialBackend, master_key: str | None) -> None:
        self._backend = backend
        self._master_key = master_key
        self._fernet: Fernet | None = Fernet(master_key.encode()) if master_key else None

    @property
    def safe_mode(self) -> bool:
        return self._fernet is None

    async def set(
        self, plugin_name: str, account_id: str, payload: dict[str, Any]
    ) -> None:
        if self._fernet is None:
            log.warning(
                "plugins.credentials.set_in_safe_mode",
                plugin=plugin_name, account_id=account_id,
            )
            return
        ciphertext = self._fernet.encrypt(json.dumps(payload).encode())
        await self._backend.upsert(plugin_name, account_id, ciphertext)

    async def get(self, plugin_name: str, account_id: str) -> dict[str, Any]:
        if self._fernet is None:
            raise CredentialNotFound(f"credential store in safe mode")
        ciphertext = await self._backend.fetch(plugin_name, account_id)
        if ciphertext is None:
            raise CredentialNotFound(
                f"no credentials for plugin={plugin_name!r} account_id={account_id!r}"
            )
        try:
            plaintext = self._fernet.decrypt(ciphertext)
        except InvalidToken as e:
            raise CredentialDecryptError(
                f"failed to decrypt credentials for plugin={plugin_name!r} "
                f"account_id={account_id!r}: master key mismatch or tampering"
            ) from e
        return json.loads(plaintext)

    async def list(self, plugin_name: str) -> list[str]:
        if self._fernet is None:
            return []
        return await self._backend.list_accounts(plugin_name)

    async def delete(self, plugin_name: str, account_id: str) -> None:
        if self._fernet is None:
            log.warning(
                "plugins.credentials.delete_in_safe_mode",
                plugin=plugin_name, account_id=account_id,
            )
            return
        await self._backend.remove(plugin_name, account_id)
```

- [ ] **Step 4: Add to `__init__.py`**

```python
from atlas_plugins.credentials import CredentialStore, CredentialBackend, InMemoryBackend
# ... existing imports

__all__ = [
    "AtlasPlugin",
    "CredentialBackend",
    "CredentialDecryptError",
    "CredentialNotFound",
    "CredentialStore",
    "HealthStatus",
    "InMemoryBackend",
    "PluginInfo",
]
```

- [ ] **Step 5: Run the tests (pass)**

```bash
uv run pytest packages/atlas-plugins/atlas_plugins/tests/test_credentials.py -v
```

Expected: 10 PASS.

- [ ] **Step 6: Commit**

```bash
git add packages/atlas-plugins/atlas_plugins/credentials.py packages/atlas-plugins/atlas_plugins/tests/test_credentials.py packages/atlas-plugins/atlas_plugins/__init__.py
git commit -m "feat(plugins): CredentialStore with Fernet + safe-mode for missing key"
```

---

### Task 3: PluginRegistry

**Files:**
- Create: `packages/atlas-plugins/atlas_plugins/registry.py`
- Create: `packages/atlas-plugins/atlas_plugins/tests/test_registry.py`
- Modify: `packages/atlas-plugins/atlas_plugins/__init__.py`

`REGISTERED_PLUGINS` lives in `registry.py` so future plans (`from atlas_plugins.registry import REGISTERED_PLUGINS; REGISTERED_PLUGINS.append(...)`) have one place to point at.

- [ ] **Step 1: Write the failing tests**

```python
"""Tests for PluginRegistry."""

from typing import Any
from unittest.mock import AsyncMock

import pytest

from atlas_plugins import AtlasPlugin, HealthStatus
from atlas_plugins.registry import PluginRegistry
from atlas_core.models.llm import ToolSchema


def _make_plugin(name: str, tools: list[ToolSchema], invoke_value: Any = None,
                 invoke_raises: Exception | None = None,
                 health_value: HealthStatus | None = None,
                 health_raises: Exception | None = None) -> AtlasPlugin:
    class _P(AtlasPlugin):
        pass
    _P.name = name
    _P.description = f"test plugin {name}"

    def _get_tools(self):
        return tools

    async def _invoke(self, tool_name, args):
        if invoke_raises:
            raise invoke_raises
        return invoke_value

    async def _health(self):
        if health_raises:
            raise health_raises
        return health_value or HealthStatus(ok=True)

    _P.get_tools = _get_tools
    _P.invoke = _invoke
    _P.health = _health

    cred = AsyncMock()
    cred.list.return_value = ["default"]
    return _P(credentials=cred)


def test_construction_with_two_well_formed_plugins():
    p1 = _make_plugin("a", [ToolSchema(name="a.t1", description="", parameters={}, plugin="a")])
    p2 = _make_plugin("b", [ToolSchema(name="b.t1", description="", parameters={}, plugin="b")])
    reg = PluginRegistry([p1, p2])
    names = [p.name for p in reg.list()]
    assert sorted(names) == ["a", "b"]


def test_tool_name_must_match_plugin_namespace():
    p = _make_plugin("a", [ToolSchema(name="not_a_namespace.t1", description="", parameters={}, plugin="a")])
    with pytest.raises(ValueError, match="does not match plugin"):
        PluginRegistry([p])


def test_duplicate_tool_name_raises():
    p1 = _make_plugin("a", [ToolSchema(name="a.t1", description="", parameters={}, plugin="a")])
    p2 = _make_plugin("a", [ToolSchema(name="a.t1", description="", parameters={}, plugin="a")])
    # Same plugin name on two instances would already collide on dict[str, AtlasPlugin];
    # different plugins declaring the same tool name is the test:
    p3 = _make_plugin("b", [ToolSchema(name="a.t1", description="", parameters={}, plugin="b")])
    with pytest.raises(ValueError, match="duplicate tool name"):
        PluginRegistry([p1, p3])


@pytest.mark.asyncio
async def test_warm_runs_health_for_each_plugin():
    p1 = _make_plugin("a", [ToolSchema(name="a.t1", description="", parameters={}, plugin="a")],
                     health_value=HealthStatus(ok=True))
    p2 = _make_plugin("b", [ToolSchema(name="b.t1", description="", parameters={}, plugin="b")],
                     health_value=HealthStatus(ok=False, detail="creds missing"))
    reg = PluginRegistry([p1, p2])
    await reg.warm()
    infos = {info.name: info for info in reg.list()}
    assert infos["a"].health.ok is True
    assert infos["b"].health.ok is False
    assert "creds missing" in (infos["b"].health.detail or "")


@pytest.mark.asyncio
async def test_warm_health_failure_does_not_break_others():
    p1 = _make_plugin("a", [ToolSchema(name="a.t1", description="", parameters={}, plugin="a")],
                     health_raises=RuntimeError("boom"))
    p2 = _make_plugin("b", [ToolSchema(name="b.t1", description="", parameters={}, plugin="b")],
                     health_value=HealthStatus(ok=True))
    reg = PluginRegistry([p1, p2])
    await reg.warm()
    infos = {info.name: info for info in reg.list()}
    assert infos["a"].health.ok is False
    assert "boom" in (infos["a"].health.detail or "")
    assert infos["b"].health.ok is True


@pytest.mark.asyncio
async def test_get_tool_schemas_returns_tools_when_healthy():
    schema = ToolSchema(name="a.t1", description="", parameters={}, plugin="a")
    p = _make_plugin("a", [schema])
    reg = PluginRegistry([p])
    await reg.warm()
    out = reg.get_tool_schemas(enabled=["a"])
    assert out == [schema]


@pytest.mark.asyncio
async def test_get_tool_schemas_skips_degraded_plugins():
    schema = ToolSchema(name="a.t1", description="", parameters={}, plugin="a")
    p = _make_plugin("a", [schema], health_value=HealthStatus(ok=False, detail="x"))
    reg = PluginRegistry([p])
    await reg.warm()
    out = reg.get_tool_schemas(enabled=["a"])
    assert out == []


def test_get_tool_schemas_silently_skips_unknown_plugin():
    p = _make_plugin("a", [ToolSchema(name="a.t1", description="", parameters={}, plugin="a")])
    reg = PluginRegistry([p])
    out = reg.get_tool_schemas(enabled=["unknown"])
    assert out == []


@pytest.mark.asyncio
async def test_invoke_happy_path_returns_tool_result_with_result():
    p = _make_plugin("a", [ToolSchema(name="a.echo", description="", parameters={}, plugin="a")],
                     invoke_value={"echo": "hi"})
    reg = PluginRegistry([p])
    result = await reg.invoke("a.echo", {"text": "hi"}, call_id="call_1")
    assert result.call_id == "call_1"
    assert result.tool == "a.echo"
    assert result.result == {"echo": "hi"}
    assert result.error is None


@pytest.mark.asyncio
async def test_invoke_unknown_plugin_returns_error_tool_result():
    reg = PluginRegistry([])
    result = await reg.invoke("missing.foo", {}, call_id="call_1")
    assert result.call_id == "call_1"
    assert result.tool == "missing.foo"
    assert result.result is None
    assert result.error is not None
    assert "unknown plugin" in result.error


@pytest.mark.asyncio
async def test_invoke_plugin_raise_is_caught_into_tool_result():
    p = _make_plugin("a", [ToolSchema(name="a.fail", description="", parameters={}, plugin="a")],
                     invoke_raises=RuntimeError("forced"))
    reg = PluginRegistry([p])
    result = await reg.invoke("a.fail", {}, call_id="call_1")
    assert result.error is not None
    assert "forced" in result.error
    assert result.result is None
```

- [ ] **Step 2: Run the tests (fail)**

```bash
uv run pytest packages/atlas-plugins/atlas_plugins/tests/test_registry.py -v
```

Expected: import errors.

- [ ] **Step 3: Implement `registry.py`**

```python
"""PluginRegistry: load + dispatch + health-cache for AtlasPlugin instances."""

from __future__ import annotations

from typing import Any

import structlog

from atlas_core.models.llm import ToolResult, ToolSchema
from atlas_plugins.base import AtlasPlugin, HealthStatus, PluginInfo

log = structlog.get_logger("atlas.plugins.registry")


# Future plans append their plugin classes here:
#   from atlas_plugins.registry import REGISTERED_PLUGINS
#   REGISTERED_PLUGINS.append(DiscordPlugin)
REGISTERED_PLUGINS: list[type[AtlasPlugin]] = []  # FakePlugin appended in Task 4


class PluginRegistry:
    """Holds constructed plugin instances; dispatches tool invocations."""

    def __init__(self, plugins: list[AtlasPlugin]) -> None:
        self._plugins: dict[str, AtlasPlugin] = {p.name: p for p in plugins}
        self._health: dict[str, HealthStatus] = {}
        self._validate_namespace_and_uniqueness(plugins)

    @staticmethod
    def _validate_namespace_and_uniqueness(plugins: list[AtlasPlugin]) -> None:
        seen: set[str] = set()
        for p in plugins:
            for t in p.get_tools():
                if not t.name.startswith(f"{p.name}."):
                    raise ValueError(
                        f"tool {t.name!r} does not match plugin namespace {p.name!r}"
                    )
                if t.name in seen:
                    raise ValueError(f"duplicate tool name across plugins: {t.name!r}")
                seen.add(t.name)

    async def warm(self) -> None:
        """Run health checks on all plugins; results cached in self._health."""
        for name, plugin in self._plugins.items():
            try:
                self._health[name] = await plugin.health()
            except Exception as e:
                log.warning("plugins.health_failed", plugin=name, error=str(e))
                self._health[name] = HealthStatus(ok=False, detail=str(e))

    def list(self) -> list[PluginInfo]:
        return [
            PluginInfo(
                name=p.name,
                description=p.description,
                tool_count=len(p.get_tools()),
                health=self._health.get(p.name) or HealthStatus(ok=False, detail="not warmed"),
            )
            for p in self._plugins.values()
        ]

    def get(self, plugin_name: str) -> AtlasPlugin | None:
        return self._plugins.get(plugin_name)

    def get_tool_schemas(self, *, enabled: list[str]) -> list[ToolSchema]:
        out: list[ToolSchema] = []
        for name in enabled:
            plugin = self._plugins.get(name)
            if plugin is None:
                continue
            health = self._health.get(name) or HealthStatus(ok=False)
            if not health.ok:
                continue
            out.extend(plugin.get_tools())
        return out

    async def invoke(
        self, tool_name: str, args: dict[str, Any], *, call_id: str
    ) -> ToolResult:
        plugin_name = tool_name.partition(".")[0]
        plugin = self._plugins.get(plugin_name)
        if plugin is None:
            return ToolResult(
                call_id=call_id, tool=tool_name, result=None,
                error=f"unknown plugin: {plugin_name}",
            )
        try:
            value = await plugin.invoke(tool_name, args)
            return ToolResult(call_id=call_id, tool=tool_name, result=value, error=None)
        except Exception as e:
            log.warning("plugins.invoke_failed", tool=tool_name, error=str(e))
            return ToolResult(
                call_id=call_id, tool=tool_name, result=None, error=str(e)
            )
```

- [ ] **Step 4: Add to `__init__.py`**

```python
from atlas_plugins.registry import PluginRegistry, REGISTERED_PLUGINS
# ... append to __all__: "PluginRegistry", "REGISTERED_PLUGINS"
```

- [ ] **Step 5: Run the tests (pass)**

```bash
uv run pytest packages/atlas-plugins -v
```

Expected: all PASS (existing tests from Tasks 1–2 plus the 11 new ones in test_registry).

- [ ] **Step 6: Commit**

```bash
git add packages/atlas-plugins/atlas_plugins/registry.py packages/atlas-plugins/atlas_plugins/tests/test_registry.py packages/atlas-plugins/atlas_plugins/__init__.py
git commit -m "feat(plugins): PluginRegistry with namespace + uniqueness asserts and health-cache"
```

---

### Task 4: FakePlugin (echo, fail, recurse)

**Files:**
- Create: `packages/atlas-plugins/atlas_plugins/_fake.py`
- Create: `packages/atlas-plugins/atlas_plugins/tests/test_fake.py`
- Modify: `packages/atlas-plugins/atlas_plugins/registry.py` — append `FakePlugin` to `REGISTERED_PLUGINS`
- Modify: `packages/atlas-plugins/atlas_plugins/__init__.py` — re-export `FakePlugin`

- [ ] **Step 1: Write the failing tests**

```python
"""Tests for FakePlugin."""

from unittest.mock import AsyncMock

import pytest

from atlas_plugins import FakePlugin


@pytest.fixture
def plugin():
    cred = AsyncMock()
    cred.list.return_value = ["default"]
    return FakePlugin(credentials=cred)


def test_get_tools_returns_three_tools(plugin):
    tools = plugin.get_tools()
    names = sorted(t.name for t in tools)
    assert names == ["fake.echo", "fake.fail", "fake.recurse"]
    for t in tools:
        assert t.plugin == "fake"


def test_tool_schemas_match_plugin_namespace(plugin):
    for tool in plugin.get_tools():
        assert tool.name.startswith("fake.")


@pytest.mark.asyncio
async def test_echo_returns_echo_dict(plugin):
    result = await plugin.invoke("fake.echo", {"text": "banana"})
    assert result == {"echo": "banana"}


@pytest.mark.asyncio
async def test_fail_raises_runtime_error(plugin):
    with pytest.raises(RuntimeError, match="forced failure"):
        await plugin.invoke("fake.fail", {})


@pytest.mark.asyncio
async def test_recurse_returns_incremented_depth(plugin):
    result = await plugin.invoke("fake.recurse", {"depth": 3})
    assert result["recurse_again"] is True
    assert result["depth"] == 4


@pytest.mark.asyncio
async def test_recurse_default_depth_zero(plugin):
    result = await plugin.invoke("fake.recurse", {})
    assert result["depth"] == 1


@pytest.mark.asyncio
async def test_unknown_tool_raises(plugin):
    with pytest.raises(ValueError, match="unknown tool"):
        await plugin.invoke("fake.nope", {})
```

- [ ] **Step 2: Run tests (fail)**

```bash
uv run pytest packages/atlas-plugins/atlas_plugins/tests/test_fake.py -v
```

Expected: import error (FakePlugin not exported yet).

- [ ] **Step 3: Implement `_fake.py`**

```python
"""FakePlugin — exercises the framework end-to-end without external API calls.

Three tools:
- fake.echo  : happy-path tool, returns {"echo": text}
- fake.fail  : always raises; tests the registry's exception → ToolResult error path
- fake.recurse: returns {"recurse_again": True, "depth": N+1}; the chat-handler test
                  mocks Anthropic to keep calling this until the 10-turn cap fires.
"""

from __future__ import annotations

from typing import Any

from atlas_core.models.llm import ToolSchema
from atlas_plugins.base import AtlasPlugin


class FakePlugin(AtlasPlugin):
    name = "fake"
    description = "Test plugin used to exercise the framework end-to-end."

    def get_tools(self) -> list[ToolSchema]:
        return [
            ToolSchema(
                name="fake.echo",
                description="Echo the given text back as {echo: text}.",
                parameters={
                    "type": "object",
                    "properties": {"text": {"type": "string"}},
                    "required": ["text"],
                },
                plugin="fake",
            ),
            ToolSchema(
                name="fake.fail",
                description="Always raises 'forced failure'. Tests the error path.",
                parameters={"type": "object", "properties": {}},
                plugin="fake",
            ),
            ToolSchema(
                name="fake.recurse",
                description=(
                    "Return {recurse_again: true, depth: N+1}. Used to drive the "
                    "tool-use loop cap in tests."
                ),
                parameters={
                    "type": "object",
                    "properties": {"depth": {"type": "integer", "default": 0}},
                },
                plugin="fake",
            ),
        ]

    async def invoke(self, tool_name: str, args: dict[str, Any]) -> Any:
        if tool_name == "fake.echo":
            return {"echo": args.get("text", "")}
        if tool_name == "fake.fail":
            raise RuntimeError("forced failure")
        if tool_name == "fake.recurse":
            depth = int(args.get("depth", 0))
            return {"recurse_again": True, "depth": depth + 1}
        raise ValueError(f"unknown tool {tool_name!r}")
```

- [ ] **Step 4: Wire FakePlugin into REGISTERED_PLUGINS and __init__**

In `packages/atlas-plugins/atlas_plugins/registry.py`:

```python
# Replace the existing REGISTERED_PLUGINS line:
from atlas_plugins._fake import FakePlugin
REGISTERED_PLUGINS: list[type[AtlasPlugin]] = [FakePlugin]
```

In `packages/atlas-plugins/atlas_plugins/__init__.py`:

```python
from atlas_plugins._fake import FakePlugin
# ... append "FakePlugin" to __all__
```

- [ ] **Step 5: Run all package tests**

```bash
uv run pytest packages/atlas-plugins -v
```

Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add packages/atlas-plugins/atlas_plugins/_fake.py packages/atlas-plugins/atlas_plugins/tests/test_fake.py packages/atlas-plugins/atlas_plugins/registry.py packages/atlas-plugins/atlas_plugins/__init__.py
git commit -m "feat(plugins): FakePlugin with echo/fail/recurse and REGISTERED_PLUGINS"
```

---

### Task 5: Migration 0007 + ORM additions

**Files:**
- Create: `infra/alembic/versions/0007_create_plugin_credentials_and_enabled_plugins.py`
- Modify: `packages/atlas-core/atlas_core/db/orm.py`

- [ ] **Step 1: Write the migration**

```python
"""create plugin_credentials and add projects.enabled_plugins

Revision ID: 0007
Revises: 0006
Create Date: 2026-04-29
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "plugin_credentials",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("plugin_name", sa.Text(), nullable=False),
        sa.Column("account_id", sa.Text(), nullable=False, server_default="default"),
        sa.Column("ciphertext", sa.LargeBinary(), nullable=False),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint(
            "plugin_name", "account_id", name="plugin_credentials_plugin_account_unique"
        ),
    )
    op.add_column(
        "projects",
        sa.Column(
            "enabled_plugins",
            postgresql.ARRAY(sa.Text()),
            nullable=False,
            server_default="{}",
        ),
    )


def downgrade() -> None:
    op.drop_column("projects", "enabled_plugins")
    op.drop_table("plugin_credentials")
```

- [ ] **Step 2: Apply the migration locally**

```bash
uv run alembic upgrade head
```

Expected: migration 0007 applied; `\d plugin_credentials` and `\d projects` show the new schema.

- [ ] **Step 3: Add ORM mapping**

Append to `packages/atlas-core/atlas_core/db/orm.py`:

```python
class PluginCredentialORM(Base):
    """Maps to the `plugin_credentials` table — encrypted plugin secrets (Plan 1, Phase 3)."""

    __tablename__ = "plugin_credentials"

    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
        server_default=func.gen_random_uuid(),
    )
    plugin_name: Mapped[str] = mapped_column(Text, nullable=False)
    account_id: Mapped[str] = mapped_column(Text, nullable=False, server_default="default")
    ciphertext: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
    __table_args__ = (UniqueConstraint("plugin_name", "account_id"),)
```

Add `LargeBinary` and `UniqueConstraint` to the existing `from sqlalchemy import ...` line at the top of the file if not already present.

Then in the `ProjectORM` class, add the new column:

```python
enabled_plugins: Mapped[list[str]] = mapped_column(
    ARRAY(Text), nullable=False, server_default="{}"
)
```

(`ARRAY(Text)` is already imported via the `notes` table from Plan 6 of Phase 2 — verify by grep.)

- [ ] **Step 4: Smoke test the import**

```bash
uv run python -c "from atlas_core.db.orm import PluginCredentialORM, ProjectORM; assert hasattr(ProjectORM, 'enabled_plugins'); print('ok')"
```

Expected: `ok`.

- [ ] **Step 5: Run the full test suite to confirm no regressions**

```bash
uv run pytest apps/api packages -q 2>&1 | tail -3
```

Expected: all previously-passing tests still pass.

- [ ] **Step 6: Commit**

```bash
git add infra/alembic/versions/0007_create_plugin_credentials_and_enabled_plugins.py packages/atlas-core/atlas_core/db/orm.py
git commit -m "feat(db): plugin_credentials table + projects.enabled_plugins (Plan 1, Phase 3)"
```

---

### Task 6: SQLAlchemy backend for CredentialStore

**Files:**
- Modify: `packages/atlas-plugins/atlas_plugins/credentials.py` — add `SqlAlchemyBackend`
- Modify: `packages/atlas-plugins/atlas_plugins/tests/test_credentials.py` — add a test against the SQLAlchemy backend
- Modify: `packages/atlas-plugins/atlas_plugins/__init__.py` — export `SqlAlchemyBackend`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_credentials.py`:

```python
@pytest.mark.asyncio
async def test_sqlalchemy_backend_round_trip(db_session):
    """Round-trip set/get/list/delete against a real Postgres test DB."""
    from atlas_plugins.credentials import SqlAlchemyBackend, CredentialStore
    from cryptography.fernet import Fernet

    # The db_session fixture is provided by apps/api conftest; this test runs
    # under apps/api's session factory so we get a real (test) Postgres.
    backend = SqlAlchemyBackend(session_factory=lambda: db_session)
    store = CredentialStore(backend=backend, master_key=Fernet.generate_key().decode())

    await store.set("github", "default", {"token": "abc"})
    got = await store.get("github", "default")
    assert got == {"token": "abc"}

    accounts = await store.list("github")
    assert accounts == ["default"]

    await store.delete("github", "default")
    with pytest.raises(CredentialNotFound):
        await store.get("github", "default")
```

The conftest needs a `db_session` fixture from apps/api's pattern. Since this test runs under the apps/api test environment (it talks to real Postgres), move it to `apps/api/atlas_api/tests/test_credential_store_postgres.py` instead. Replace the appended section above with a new test file:

`apps/api/atlas_api/tests/test_credential_store_postgres.py`:

```python
"""Postgres-backed CredentialStore tests (use apps/api's db_session fixture)."""

import pytest
from cryptography.fernet import Fernet

from atlas_plugins import CredentialNotFound
from atlas_plugins.credentials import CredentialStore, SqlAlchemyBackend


@pytest.mark.asyncio
async def test_sqlalchemy_backend_round_trip(db_session):
    backend = SqlAlchemyBackend(session_factory=lambda: db_session)
    store = CredentialStore(backend=backend, master_key=Fernet.generate_key().decode())

    await store.set("github", "default", {"token": "abc"})
    got = await store.get("github", "default")
    assert got == {"token": "abc"}


@pytest.mark.asyncio
async def test_sqlalchemy_backend_upsert_overwrites(db_session):
    backend = SqlAlchemyBackend(session_factory=lambda: db_session)
    store = CredentialStore(backend=backend, master_key=Fernet.generate_key().decode())

    await store.set("github", "default", {"token": "old"})
    await store.set("github", "default", {"token": "new"})
    got = await store.get("github", "default")
    assert got == {"token": "new"}


@pytest.mark.asyncio
async def test_sqlalchemy_backend_list_returns_account_ids(db_session):
    backend = SqlAlchemyBackend(session_factory=lambda: db_session)
    store = CredentialStore(backend=backend, master_key=Fernet.generate_key().decode())

    await store.set("gmail", "alice@example.com", {"refresh": "a"})
    await store.set("gmail", "bob@example.com", {"refresh": "b"})

    accounts = await store.list("gmail")
    assert sorted(accounts) == ["alice@example.com", "bob@example.com"]


@pytest.mark.asyncio
async def test_sqlalchemy_backend_delete(db_session):
    backend = SqlAlchemyBackend(session_factory=lambda: db_session)
    store = CredentialStore(backend=backend, master_key=Fernet.generate_key().decode())

    await store.set("github", "default", {"token": "t"})
    await store.delete("github", "default")
    with pytest.raises(CredentialNotFound):
        await store.get("github", "default")
```

- [ ] **Step 2: Run the tests (fail)**

```bash
uv run pytest apps/api/atlas_api/tests/test_credential_store_postgres.py -v
```

Expected: ImportError on `SqlAlchemyBackend`.

- [ ] **Step 3: Implement `SqlAlchemyBackend`**

Append to `packages/atlas-plugins/atlas_plugins/credentials.py`:

```python
from collections.abc import Callable
from contextlib import asynccontextmanager

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession


class SqlAlchemyBackend:
    """Postgres-backed credential store via the plugin_credentials table.

    Constructed with a callable that yields an AsyncSession. In production,
    the lifespan binds this to ``session_scope`` from atlas_core.db.session;
    tests pass a lambda that returns the test fixture's session.
    """

    def __init__(self, *, session_factory: Callable[[], AsyncSession]) -> None:
        self._session_factory = session_factory

    async def upsert(
        self, plugin_name: str, account_id: str, ciphertext: bytes
    ) -> None:
        from atlas_core.db.orm import PluginCredentialORM
        async with self._session_scope() as s:
            stmt = pg_insert(PluginCredentialORM).values(
                plugin_name=plugin_name,
                account_id=account_id,
                ciphertext=ciphertext,
            )
            stmt = stmt.on_conflict_do_update(
                index_elements=["plugin_name", "account_id"],
                set_={"ciphertext": stmt.excluded.ciphertext, "updated_at": stmt.excluded.updated_at},
            )
            await s.execute(stmt)
            await s.flush()

    async def fetch(self, plugin_name: str, account_id: str) -> bytes | None:
        from atlas_core.db.orm import PluginCredentialORM
        async with self._session_scope() as s:
            row = (await s.execute(
                select(PluginCredentialORM).where(
                    PluginCredentialORM.plugin_name == plugin_name,
                    PluginCredentialORM.account_id == account_id,
                )
            )).scalar_one_or_none()
            return bytes(row.ciphertext) if row is not None else None

    async def list_accounts(self, plugin_name: str) -> list[str]:
        from atlas_core.db.orm import PluginCredentialORM
        async with self._session_scope() as s:
            rows = (await s.execute(
                select(PluginCredentialORM.account_id).where(
                    PluginCredentialORM.plugin_name == plugin_name
                )
            )).scalars().all()
            return list(rows)

    async def remove(self, plugin_name: str, account_id: str) -> None:
        from atlas_core.db.orm import PluginCredentialORM
        async with self._session_scope() as s:
            await s.execute(
                delete(PluginCredentialORM).where(
                    PluginCredentialORM.plugin_name == plugin_name,
                    PluginCredentialORM.account_id == account_id,
                )
            )
            await s.flush()

    @asynccontextmanager
    async def _session_scope(self):
        s = self._session_factory()
        if hasattr(s, "__aenter__"):
            async with s as session:
                yield session
        else:
            yield s
```

The `_session_scope` is a small adapter so the backend works with either `session_scope`-style async context managers (production) or a plain `AsyncSession` returned by the test fixture.

- [ ] **Step 4: Export from `__init__.py`**

```python
from atlas_plugins.credentials import (
    CredentialBackend, CredentialStore, InMemoryBackend, SqlAlchemyBackend,
)
# ... append "SqlAlchemyBackend" to __all__
```

- [ ] **Step 5: Run the tests (pass)**

```bash
uv run pytest apps/api/atlas_api/tests/test_credential_store_postgres.py -v
uv run pytest packages/atlas-plugins -v   # confirm in-memory tests still pass
```

Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add packages/atlas-plugins/atlas_plugins/credentials.py packages/atlas-plugins/atlas_plugins/__init__.py apps/api/atlas_api/tests/test_credential_store_postgres.py
git commit -m "feat(plugins): SqlAlchemyBackend for CredentialStore (Postgres)"
```

---

## Phase B — REST router + lifespan wiring

### Task 7: /api/v1/plugins/* router

**Files:**
- Create: `apps/api/atlas_api/routers/plugins.py`
- Create: `apps/api/atlas_api/tests/test_plugins_router.py`
- Modify: `apps/api/atlas_api/main.py` — register the router (full lifespan wiring is Task 8)
- Modify: `apps/api/atlas_api/deps.py` — add `get_plugin_registry` and `get_credential_store`

- [ ] **Step 1: Write the failing tests**

```python
"""Integration tests for /api/v1/plugins/* (Plan 1, Phase 3)."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from atlas_plugins import (
    CredentialNotFound, FakePlugin, HealthStatus, InMemoryBackend, PluginInfo,
    PluginRegistry, CredentialStore,
)
from atlas_api.deps import get_credential_store, get_plugin_registry
from atlas_api.main import app


@pytest.fixture
def fake_credential_store():
    from cryptography.fernet import Fernet
    return CredentialStore(backend=InMemoryBackend(), master_key=Fernet.generate_key().decode())


@pytest.fixture
def fake_registry(fake_credential_store):
    plugin = FakePlugin(credentials=fake_credential_store)
    reg = PluginRegistry([plugin])
    # warm synchronously: pretend health is ok
    reg._health = {"fake": HealthStatus(ok=True)}
    return reg


@pytest.fixture
def app_with_plugin_overrides(app_client, fake_registry, fake_credential_store):
    app.dependency_overrides[get_plugin_registry] = lambda: fake_registry
    app.dependency_overrides[get_credential_store] = lambda: fake_credential_store
    yield app_client
    app.dependency_overrides.pop(get_plugin_registry, None)
    app.dependency_overrides.pop(get_credential_store, None)


@pytest.mark.asyncio
async def test_list_plugins_returns_fake(app_with_plugin_overrides):
    resp = await app_with_plugin_overrides.get("/api/v1/plugins")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["name"] == "fake"
    assert body[0]["tool_count"] == 3
    assert body[0]["health"]["ok"] is True


@pytest.mark.asyncio
async def test_get_schema_returns_three_tools(app_with_plugin_overrides):
    resp = await app_with_plugin_overrides.get("/api/v1/plugins/fake/schema")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 3
    assert {t["name"] for t in body} == {"fake.echo", "fake.fail", "fake.recurse"}


@pytest.mark.asyncio
async def test_get_schema_unknown_plugin_404(app_with_plugin_overrides):
    resp = await app_with_plugin_overrides.get("/api/v1/plugins/unknown/schema")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_invoke_echo_happy_path(app_with_plugin_overrides):
    resp = await app_with_plugin_overrides.post(
        "/api/v1/plugins/fake/invoke",
        json={"tool_name": "fake.echo", "args": {"text": "banana"}},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["tool"] == "fake.echo"
    assert body["result"] == {"echo": "banana"}
    assert body["error"] is None


@pytest.mark.asyncio
async def test_invoke_fail_returns_200_with_error(app_with_plugin_overrides):
    """Tool errors return 200 with ToolResult.error set, not 5xx."""
    resp = await app_with_plugin_overrides.post(
        "/api/v1/plugins/fake/invoke",
        json={"tool_name": "fake.fail", "args": {}},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["error"] is not None
    assert "forced failure" in body["error"]


@pytest.mark.asyncio
async def test_invoke_unknown_tool_returns_200_with_error(app_with_plugin_overrides):
    resp = await app_with_plugin_overrides.post(
        "/api/v1/plugins/fake/invoke",
        json={"tool_name": "fake.nope", "args": {}},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["error"] is not None


@pytest.mark.asyncio
async def test_invoke_unknown_plugin_returns_200_with_error(app_with_plugin_overrides):
    resp = await app_with_plugin_overrides.post(
        "/api/v1/plugins/fake/invoke",
        json={"tool_name": "missing.foo", "args": {}},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["error"] is not None
    assert "unknown plugin" in body["error"]


@pytest.mark.asyncio
async def test_credentials_list_set_delete(app_with_plugin_overrides):
    # Initially empty.
    resp = await app_with_plugin_overrides.get("/api/v1/plugins/fake/credentials")
    assert resp.status_code == 200
    assert resp.json() == []

    # Set.
    resp = await app_with_plugin_overrides.post(
        "/api/v1/plugins/fake/credentials",
        json={"account_id": "alice", "payload": {"foo": "bar"}},
    )
    assert resp.status_code == 201
    assert resp.json() == {"account_id": "alice"}

    # List sees the new account.
    resp = await app_with_plugin_overrides.get("/api/v1/plugins/fake/credentials")
    assert resp.status_code == 200
    assert resp.json() == ["alice"]

    # Delete.
    resp = await app_with_plugin_overrides.delete("/api/v1/plugins/fake/credentials/alice")
    assert resp.status_code == 204

    resp = await app_with_plugin_overrides.get("/api/v1/plugins/fake/credentials")
    assert resp.json() == []


@pytest.mark.asyncio
async def test_credentials_default_account_id(app_with_plugin_overrides):
    resp = await app_with_plugin_overrides.post(
        "/api/v1/plugins/fake/credentials",
        json={"payload": {"foo": "bar"}},   # no account_id
    )
    assert resp.status_code == 201
    assert resp.json() == {"account_id": "default"}


@pytest.mark.asyncio
async def test_credentials_set_in_safe_mode_returns_503(app_client, fake_registry):
    """When CredentialStore is in safe-mode, POST credentials returns 503."""
    safe_store = CredentialStore(backend=InMemoryBackend(), master_key=None)
    app.dependency_overrides[get_plugin_registry] = lambda: fake_registry
    app.dependency_overrides[get_credential_store] = lambda: safe_store
    try:
        resp = await app_client.post(
            "/api/v1/plugins/fake/credentials",
            json={"payload": {"foo": "bar"}},
        )
    finally:
        app.dependency_overrides.pop(get_plugin_registry, None)
        app.dependency_overrides.pop(get_credential_store, None)
    assert resp.status_code == 503
    assert resp.json()["detail"] == "credential_store_unavailable"
```

- [ ] **Step 2: Run the tests (fail)**

```bash
uv run pytest apps/api/atlas_api/tests/test_plugins_router.py -v
```

Expected: 404s (router doesn't exist yet).

- [ ] **Step 3: Add deps**

In `apps/api/atlas_api/deps.py`:

```python
from atlas_plugins import CredentialStore, PluginRegistry


def get_plugin_registry(connection: HTTPConnection) -> PluginRegistry:
    return connection.app.state.plugin_registry


def get_credential_store(connection: HTTPConnection) -> CredentialStore:
    return connection.app.state.credential_store
```

- [ ] **Step 4: Implement the router**

`apps/api/atlas_api/routers/plugins.py`:

```python
"""/api/v1/plugins/* — plugin framework REST surface (Plan 1, Phase 3)."""

from __future__ import annotations

from typing import Any
from uuid import uuid4

from atlas_core.models.base import AtlasModel
from atlas_core.models.llm import ToolResult, ToolSchema
from atlas_plugins import CredentialNotFound, CredentialStore, PluginInfo, PluginRegistry
from fastapi import APIRouter, Depends, HTTPException
from pydantic import Field

from atlas_api.deps import get_credential_store, get_plugin_registry

router = APIRouter(tags=["plugins"])


class InvokeRequest(AtlasModel):
    model_config = {"strict": False}
    tool_name: str
    args: dict[str, Any] = Field(default_factory=dict)


class CredentialSetRequest(AtlasModel):
    model_config = {"strict": False}
    account_id: str = "default"
    payload: dict[str, Any]


class CredentialSetResponse(AtlasModel):
    account_id: str


@router.get("/plugins", response_model=list[PluginInfo])
async def list_plugins(
    registry: PluginRegistry = Depends(get_plugin_registry),
) -> list[PluginInfo]:
    return registry.list()


@router.get("/plugins/{name}/schema", response_model=list[ToolSchema])
async def get_plugin_schema(
    name: str,
    registry: PluginRegistry = Depends(get_plugin_registry),
) -> list[ToolSchema]:
    plugin = registry.get(name)
    if plugin is None:
        raise HTTPException(status_code=404, detail="plugin not found")
    return plugin.get_tools()


@router.post("/plugins/{name}/invoke", response_model=ToolResult)
async def invoke_plugin(
    name: str,
    payload: InvokeRequest,
    registry: PluginRegistry = Depends(get_plugin_registry),
) -> ToolResult:
    """Direct tool invocation. Tool errors live in the response, not as 5xx."""
    return await registry.invoke(
        payload.tool_name, payload.args, call_id=f"manual_{uuid4().hex[:8]}"
    )


@router.get("/plugins/{name}/credentials", response_model=list[str])
async def list_plugin_credentials(
    name: str,
    store: CredentialStore = Depends(get_credential_store),
    registry: PluginRegistry = Depends(get_plugin_registry),
) -> list[str]:
    if registry.get(name) is None:
        raise HTTPException(status_code=404, detail="plugin not found")
    return await store.list(name)


@router.post(
    "/plugins/{name}/credentials",
    response_model=CredentialSetResponse,
    status_code=201,
)
async def set_plugin_credential(
    name: str,
    payload: CredentialSetRequest,
    store: CredentialStore = Depends(get_credential_store),
    registry: PluginRegistry = Depends(get_plugin_registry),
) -> CredentialSetResponse:
    if registry.get(name) is None:
        raise HTTPException(status_code=404, detail="plugin not found")
    if store.safe_mode:
        raise HTTPException(status_code=503, detail="credential_store_unavailable")
    await store.set(name, payload.account_id, payload.payload)
    return CredentialSetResponse(account_id=payload.account_id)


@router.delete("/plugins/{name}/credentials/{account_id}", status_code=204)
async def delete_plugin_credential(
    name: str,
    account_id: str,
    store: CredentialStore = Depends(get_credential_store),
    registry: PluginRegistry = Depends(get_plugin_registry),
) -> None:
    if registry.get(name) is None:
        raise HTTPException(status_code=404, detail="plugin not found")
    await store.delete(name, account_id)
```

- [ ] **Step 5: Register the router in `main.py`**

```python
from atlas_api.routers import plugins as plugins_router
# ... add to app.include_router calls:
app.include_router(plugins_router.router, prefix="/api/v1")
```

- [ ] **Step 6: Run the tests (pass)**

```bash
uv run pytest apps/api/atlas_api/tests/test_plugins_router.py -v
```

Expected: 10 PASS.

- [ ] **Step 7: Run ruff + full suite for regressions**

```bash
uv run ruff check apps/api/atlas_api/routers/plugins.py apps/api/atlas_api/deps.py
uv run pytest apps/api packages -q 2>&1 | tail -3
```

Expected: ruff clean; full suite green.

- [ ] **Step 8: Commit**

```bash
git add apps/api/atlas_api/routers/plugins.py apps/api/atlas_api/tests/test_plugins_router.py apps/api/atlas_api/deps.py apps/api/atlas_api/main.py
git commit -m "feat(api/plugins): /api/v1/plugins/* REST endpoints"
```

---

### Task 8: Lifespan wiring

**Files:**
- Modify: `apps/api/atlas_api/main.py` — build the registry + credential store in lifespan

- [ ] **Step 1: Inspect the current lifespan**

```bash
grep -n "async def lifespan\|app.state\." apps/api/atlas_api/main.py | head -20
```

Note where `app.state.session_factory` is built (Plan 4 of Phase 2 added `app.state.graph_store`; the registry follows the same pattern).

- [ ] **Step 2: Wire the registry in lifespan**

In `apps/api/atlas_api/main.py`, inside the `lifespan` async context manager (after the session factory is constructed and before `yield`):

```python
import os

from atlas_plugins import CredentialStore, PluginRegistry
from atlas_plugins.credentials import SqlAlchemyBackend
from atlas_plugins.registry import REGISTERED_PLUGINS

# ... existing lifespan body ...

master_key = os.getenv("ATLAS_PLUGINS__MASTER_KEY")
backend = SqlAlchemyBackend(
    session_factory=lambda: session_scope(app.state.session_factory),
)
credential_store = CredentialStore(backend=backend, master_key=master_key)
plugins = [PluginCls(credentials=credential_store) for PluginCls in REGISTERED_PLUGINS]
plugin_registry = PluginRegistry(plugins)
await plugin_registry.warm()
app.state.credential_store = credential_store
app.state.plugin_registry = plugin_registry
log.info("plugins.lifespan_ready", count=len(plugins),
         master_key_present=master_key is not None)
```

The `session_scope` import comes from `atlas_core.db.session` — confirm the existing lifespan already imports it.

- [ ] **Step 3: Smoke run the API**

```bash
ATLAS_PLUGINS__MASTER_KEY="$(uv run python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())')" uv run uvicorn atlas_api.main:app --port 8001 &
sleep 3
curl -s http://localhost:8001/api/v1/plugins | head -3
kill %1
```

Expected: response shows FakePlugin with `health.ok=true`.

Then run again WITHOUT the env var:

```bash
unset ATLAS_PLUGINS__MASTER_KEY
uv run uvicorn atlas_api.main:app --port 8001 &
sleep 3
curl -s http://localhost:8001/api/v1/plugins
kill %1
```

Expected: response shows FakePlugin with `health.ok=false` (no creds because store is in safe-mode).

- [ ] **Step 4: Run the test suite to confirm no regressions**

```bash
uv run pytest apps/api packages -q 2>&1 | tail -3
```

Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add apps/api/atlas_api/main.py
git commit -m "feat(api): wire CredentialStore + PluginRegistry into lifespan"
```

---

## Phase C — Anthropic tool-use loop

### Task 9: AnthropicProvider emits TOOL_CALL events

**Files:**
- Modify: `packages/atlas-core/atlas_core/providers/anthropic.py`
- Create: `packages/atlas-core/atlas_core/providers/tests/test_anthropic_tools.py` (or extend the existing anthropic tests file)

- [ ] **Step 1: Locate or create the anthropic test file**

```bash
find packages/atlas-core -name "test_anthropic*" -type f
```

If `test_anthropic.py` exists, append the tool-related tests there. If not, create `packages/atlas-core/atlas_core/providers/tests/test_anthropic_tools.py`.

- [ ] **Step 2: Write the failing test**

```python
"""Tests for AnthropicProvider's tool_use event emission."""

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from atlas_core.models.llm import ModelEventType
from atlas_core.providers.anthropic import AnthropicProvider


class _FakeAnthropicStream:
    """Yields scripted Anthropic streaming events.

    The real SDK emits content_block_start, content_block_delta (with
    input_json_delta), and content_block_stop for tool_use blocks. We
    simulate those with simple namedtuple-shaped objects.
    """

    def __init__(self, events: list[dict[str, Any]]) -> None:
        self._events = events

    async def __aenter__(self) -> "_FakeAnthropicStream":
        return self

    async def __aexit__(self, *_) -> None:
        pass

    def __aiter__(self):
        return self._gen()

    async def _gen(self):
        for e in self._events:
            yield _dict_to_obj(e)


def _dict_to_obj(d: dict[str, Any]) -> Any:
    """Recursively convert dicts to objects with attribute access."""
    if isinstance(d, dict):
        m = MagicMock()
        for k, v in d.items():
            setattr(m, k, _dict_to_obj(v))
        return m
    if isinstance(d, list):
        return [_dict_to_obj(x) for x in d]
    return d


@pytest.fixture
def fake_client():
    client = MagicMock()
    return client


@pytest.mark.asyncio
async def test_emits_tool_call_event_when_stream_contains_tool_use(fake_client):
    # Scripted event sequence: a tool_use content block with name "fake.echo"
    # and input streamed via input_json_delta.
    events = [
        {"type": "message_start", "message": {"usage": {"input_tokens": 10}}},
        {"type": "content_block_start", "index": 0,
         "content_block": {"type": "tool_use", "id": "tu_01", "name": "fake.echo", "input": {}}},
        {"type": "content_block_delta", "index": 0,
         "delta": {"type": "input_json_delta", "partial_json": '{"text":"hi'}},
        {"type": "content_block_delta", "index": 0,
         "delta": {"type": "input_json_delta", "partial_json": '"}'}},
        {"type": "content_block_stop", "index": 0},
        {"type": "message_delta", "usage": {"output_tokens": 5}},
        {"type": "message_stop"},
    ]
    fake_client.messages.stream = MagicMock(return_value=_FakeAnthropicStream(events))

    provider = AnthropicProvider(api_key="x", model_id="claude-sonnet-4-6", _client=fake_client)
    out = []
    async for ev in provider.stream(messages=[{"role": "user", "content": "hi"}]):
        out.append(ev)

    tool_calls = [e for e in out if e.type == ModelEventType.TOOL_CALL]
    assert len(tool_calls) == 1
    assert tool_calls[0].data["id"] == "tu_01"
    assert tool_calls[0].data["tool"] == "fake.echo"
    assert tool_calls[0].data["args"] == {"text": "hi"}


@pytest.mark.asyncio
async def test_text_stream_unchanged_with_no_tool_use(fake_client):
    events = [
        {"type": "message_start", "message": {"usage": {"input_tokens": 5}}},
        {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "hello"}},
        {"type": "message_delta", "usage": {"output_tokens": 1}},
        {"type": "message_stop"},
    ]
    fake_client.messages.stream = MagicMock(return_value=_FakeAnthropicStream(events))

    provider = AnthropicProvider(api_key="x", model_id="claude-sonnet-4-6", _client=fake_client)
    out = []
    async for ev in provider.stream(messages=[{"role": "user", "content": "hi"}]):
        out.append(ev)

    tool_calls = [e for e in out if e.type == ModelEventType.TOOL_CALL]
    assert tool_calls == []
    tokens = [e for e in out if e.type == ModelEventType.TOKEN]
    assert tokens[0].data["text"] == "hello"
```

- [ ] **Step 3: Run the tests (fail)**

```bash
uv run pytest packages/atlas-core/atlas_core/providers/tests/test_anthropic_tools.py -v
```

Expected: assertion failure on `tool_calls len == 1` (provider currently ignores tool_use blocks).

- [ ] **Step 4: Extend `AnthropicProvider.stream`**

In `packages/atlas-core/atlas_core/providers/anthropic.py`, modify the inner `async for event in stream:` loop. Add tool_use buffering:

```python
# At the top of the stream() method, alongside started/input_tokens/output_tokens:
tool_use_blocks: dict[int, dict[str, Any]] = {}   # index → {id, name, input_json}

# Replace the existing loop body with:
async for event in stream:
    et = getattr(event, "type", None)
    if et == "content_block_start":
        idx = getattr(event, "index", None)
        block = getattr(event, "content_block", None)
        if block is not None and getattr(block, "type", None) == "tool_use" and idx is not None:
            tool_use_blocks[idx] = {
                "id": getattr(block, "id", ""),
                "name": getattr(block, "name", ""),
                "input_json": "",
            }
    elif et == "content_block_delta":
        idx = getattr(event, "index", None)
        delta = getattr(event, "delta", None)
        delta_type = getattr(delta, "type", None) if delta is not None else None
        if delta_type == "text_delta" and delta is not None:
            yield ModelEvent(
                type=ModelEventType.TOKEN,
                data={"text": delta.text},
            )
        elif delta_type == "input_json_delta" and idx in tool_use_blocks and delta is not None:
            tool_use_blocks[idx]["input_json"] += getattr(delta, "partial_json", "")
    elif et == "content_block_stop":
        idx = getattr(event, "index", None)
        if idx in tool_use_blocks:
            buf = tool_use_blocks.pop(idx)
            try:
                args = json.loads(buf["input_json"]) if buf["input_json"] else {}
            except json.JSONDecodeError:
                args = {}
            yield ModelEvent(
                type=ModelEventType.TOOL_CALL,
                data={"id": buf["id"], "tool": buf["name"], "args": args},
            )
    elif et == "message_start":
        # ... existing input_tokens handling, unchanged ...
    elif et in ("message_delta", "message_stop"):
        # ... existing output_tokens handling, unchanged ...
```

Also add `import json` at the top of the file if not present.

- [ ] **Step 5: Run the tests (pass)**

```bash
uv run pytest packages/atlas-core/atlas_core/providers/tests/test_anthropic_tools.py -v
uv run pytest packages/atlas-core -q 2>&1 | tail -3
```

Expected: 2 new PASS; existing anthropic tests still pass.

- [ ] **Step 6: Commit**

```bash
git add packages/atlas-core/atlas_core/providers/anthropic.py packages/atlas-core/atlas_core/providers/tests/test_anthropic_tools.py
git commit -m "feat(providers/anthropic): emit TOOL_CALL events from streaming tool_use blocks"
```

---

### Task 10: Fake provider supports scripted TOOL_CALL events

**Files:**
- Modify: `packages/atlas-core/atlas_core/providers/_fake.py`
- Modify: `packages/atlas-core/atlas_core/providers/tests/test_fake.py` (or wherever the fake provider's tests live)

The chat-handler test in Task 11 needs a way to script tool calls in/out of a fake provider. The current `_fake.py` returns scripted text; we extend it to also support scripted `TOOL_CALL` events keyed by the message turn.

- [ ] **Step 1: Inspect the existing fake provider**

```bash
cat packages/atlas-core/atlas_core/providers/_fake.py
```

Note its existing API (probably a class with a list of scripted responses).

- [ ] **Step 2: Write the failing test**

```python
"""Test fake provider's tool-call scripting."""

import pytest

from atlas_core.models.llm import ModelEventType
from atlas_core.providers._fake import FakeProvider


@pytest.mark.asyncio
async def test_scripted_tool_call_emitted():
    provider = FakeProvider(scripted_turns=[
        {"tool_calls": [{"id": "tu_1", "tool": "fake.echo", "args": {"text": "hi"}}]},
        {"text": "Got the echo: hi"},
    ])
    # First turn: should emit a TOOL_CALL event.
    events_t1 = [e async for e in provider.stream(messages=[{"role": "user", "content": "x"}])]
    types_t1 = [e.type for e in events_t1]
    assert ModelEventType.TOOL_CALL in types_t1

    # Second turn: text only.
    events_t2 = [e async for e in provider.stream(messages=[{"role": "user", "content": "x"}])]
    types_t2 = [e.type for e in events_t2]
    assert ModelEventType.TOKEN in types_t2
    assert ModelEventType.TOOL_CALL not in types_t2
```

- [ ] **Step 3: Run the test (fail)**

```bash
uv run pytest packages/atlas-core -k fake -v
```

Expected: failure (FakeProvider doesn't accept `scripted_turns` yet).

- [ ] **Step 4: Extend FakeProvider**

Add `scripted_turns` to the constructor (a list of dicts; each dict either has `text:str` or `tool_calls:list[dict]` or both). Track an internal turn index; advance on each `stream()` call. If a turn dict has `tool_calls`, yield a TOOL_CALL event per entry. Then yield text tokens (if `text` is set), then DONE.

The exact code depends on the existing FakeProvider's structure — modify minimally.

- [ ] **Step 5: Run the tests (pass)**

```bash
uv run pytest packages/atlas-core -k fake -v
uv run pytest packages/atlas-core -q 2>&1 | tail -3
```

Expected: new test PASS, existing fake-provider tests still PASS.

- [ ] **Step 6: Commit**

```bash
git add packages/atlas-core/atlas_core/providers/_fake.py packages/atlas-core/atlas_core/providers/tests/test_fake.py
git commit -m "feat(providers/_fake): scripted_turns parameter for tool-use tests"
```

---

### Task 11: WS chat tool-use loop + 10-turn cap

**Files:**
- Modify: `apps/api/atlas_api/ws/chat.py`
- Create: `apps/api/atlas_api/tests/test_ws_chat_tool_use.py`

This is the largest single change. Read `apps/api/atlas_api/ws/chat.py` end-to-end first (≈300 lines).

- [ ] **Step 1: Write the failing tests**

`apps/api/atlas_api/tests/test_ws_chat_tool_use.py`:

```python
"""Tests for the chat WS handler's Anthropic tool-use loop (Plan 1, Phase 3)."""

from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from atlas_core.db.orm import ProjectORM, SessionORM
from atlas_core.models.llm import ModelEvent, ModelEventType
from atlas_plugins import FakePlugin, PluginRegistry, HealthStatus, CredentialStore, InMemoryBackend
from cryptography.fernet import Fernet

from atlas_api.deps import get_credential_store, get_model_router, get_plugin_registry
from atlas_api.main import app


@pytest.fixture
def fake_registry():
    store = CredentialStore(backend=InMemoryBackend(), master_key=Fernet.generate_key().decode())
    plugin = FakePlugin(credentials=store)
    reg = PluginRegistry([plugin])
    reg._health = {"fake": HealthStatus(ok=True)}
    return reg, store


@pytest.fixture
def fake_provider_factory():
    """Returns a callable that builds a FakeProvider with scripted turns."""
    from atlas_core.providers._fake import FakeProvider
    def _make(turns):
        return FakeProvider(scripted_turns=turns)
    return _make


@pytest.mark.asyncio
async def test_single_tool_call_round_trip(
    app_client, db_session, fake_registry, fake_provider_factory
):
    """Model returns one tool_use → handler dispatches → model's next turn returns text."""
    project = ProjectORM(user_id="matt", name="P", default_model="claude-sonnet-4-6",
                         enabled_plugins=["fake"])
    db_session.add(project)
    await db_session.flush()
    session_id = uuid4()

    registry, store = fake_registry
    fake_provider = fake_provider_factory([
        {"tool_calls": [{"id": "tu_1", "tool": "fake.echo", "args": {"text": "hi"}}]},
        {"text": "The echo was hi."},
    ])
    fake_router = AsyncMock()
    fake_router.get_provider.return_value = fake_provider
    fake_router.spec_for.return_value = fake_provider.spec

    app.dependency_overrides[get_plugin_registry] = lambda: registry
    app.dependency_overrides[get_credential_store] = lambda: store
    app.dependency_overrides[get_model_router] = lambda: fake_router

    try:
        async with app_client.websocket_connect(f"/api/v1/ws/{session_id}") as ws:
            await ws.send_json({"type": "chat.message",
                                "payload": {"text": "echo hi", "project_id": str(project.id)}})
            events = []
            while True:
                e = await ws.receive_json()
                events.append(e)
                if e["type"] == "chat.done":
                    break
    finally:
        app.dependency_overrides.pop(get_plugin_registry, None)
        app.dependency_overrides.pop(get_credential_store, None)
        app.dependency_overrides.pop(get_model_router, None)

    types = [e["type"] for e in events]
    assert "chat.tool_use" in types
    assert "chat.tool_result" in types
    assert any(e["type"] == "chat.token" and "echo was hi" in e["payload"].get("text", "")
               for e in events)


@pytest.mark.asyncio
async def test_ten_turn_cap_forces_final_summary(
    app_client, db_session, fake_registry, fake_provider_factory
):
    """Model that always tool_calls hits the 10-turn cap and then must respond without tools."""
    project = ProjectORM(user_id="matt", name="P", default_model="claude-sonnet-4-6",
                         enabled_plugins=["fake"])
    db_session.add(project)
    await db_session.flush()
    session_id = uuid4()

    # 10 tool-calling turns + 1 final text turn (after the handler force-disables tools).
    turns = [
        {"tool_calls": [{"id": f"tu_{i}", "tool": "fake.recurse", "args": {"depth": i}}]}
        for i in range(10)
    ] + [{"text": "Stopped recursing."}]
    fake_provider = fake_provider_factory(turns)
    fake_router = AsyncMock()
    fake_router.get_provider.return_value = fake_provider
    fake_router.spec_for.return_value = fake_provider.spec

    registry, store = fake_registry
    app.dependency_overrides[get_plugin_registry] = lambda: registry
    app.dependency_overrides[get_credential_store] = lambda: store
    app.dependency_overrides[get_model_router] = lambda: fake_router

    try:
        async with app_client.websocket_connect(f"/api/v1/ws/{session_id}") as ws:
            await ws.send_json({"type": "chat.message",
                                "payload": {"text": "go", "project_id": str(project.id)}})
            events = []
            while True:
                e = await ws.receive_json()
                events.append(e)
                if e["type"] == "chat.done":
                    break
    finally:
        app.dependency_overrides.pop(get_plugin_registry, None)
        app.dependency_overrides.pop(get_credential_store, None)
        app.dependency_overrides.pop(get_model_router, None)

    tool_use_events = [e for e in events if e["type"] == "chat.tool_use"]
    tool_result_events = [e for e in events if e["type"] == "chat.tool_result"]
    assert len(tool_use_events) == 10
    assert len(tool_result_events) == 10
    assert any(e["type"] == "chat.token" and "Stopped" in e["payload"].get("text", "")
               for e in events)


@pytest.mark.asyncio
async def test_tool_failure_returns_error_in_tool_result_event(
    app_client, db_session, fake_registry, fake_provider_factory
):
    """fake.fail raises; handler emits a tool_result event with ok=false."""
    project = ProjectORM(user_id="matt", name="P", default_model="claude-sonnet-4-6",
                         enabled_plugins=["fake"])
    db_session.add(project)
    await db_session.flush()
    session_id = uuid4()

    fake_provider = fake_provider_factory([
        {"tool_calls": [{"id": "tu_1", "tool": "fake.fail", "args": {}}]},
        {"text": "Tool failed but I'm telling you about it."},
    ])
    fake_router = AsyncMock()
    fake_router.get_provider.return_value = fake_provider
    fake_router.spec_for.return_value = fake_provider.spec

    registry, store = fake_registry
    app.dependency_overrides[get_plugin_registry] = lambda: registry
    app.dependency_overrides[get_credential_store] = lambda: store
    app.dependency_overrides[get_model_router] = lambda: fake_router

    try:
        async with app_client.websocket_connect(f"/api/v1/ws/{session_id}") as ws:
            await ws.send_json({"type": "chat.message",
                                "payload": {"text": "fail please", "project_id": str(project.id)}})
            events = []
            while True:
                e = await ws.receive_json()
                events.append(e)
                if e["type"] == "chat.done":
                    break
    finally:
        app.dependency_overrides.pop(get_plugin_registry, None)
        app.dependency_overrides.pop(get_credential_store, None)
        app.dependency_overrides.pop(get_model_router, None)

    tool_results = [e for e in events if e["type"] == "chat.tool_result"]
    assert len(tool_results) == 1
    assert tool_results[0]["payload"]["ok"] is False


@pytest.mark.asyncio
async def test_lmstudio_provider_does_not_get_tools(
    app_client, db_session, fake_registry, fake_provider_factory
):
    """When provider is LM Studio, tools are not attached and no tool-use loop runs."""
    project = ProjectORM(user_id="matt", name="P", default_model="local-model",
                         enabled_plugins=["fake"])
    db_session.add(project)
    await db_session.flush()
    session_id = uuid4()

    # FakeProvider with provider="lmstudio" in its spec acts as the LM Studio stand-in.
    fake_provider = fake_provider_factory([{"text": "no tools here"}])
    fake_provider.spec.provider = "lmstudio"   # spec is a Pydantic model with provider field
    fake_router = AsyncMock()
    fake_router.get_provider.return_value = fake_provider
    fake_router.spec_for.return_value = fake_provider.spec

    registry, store = fake_registry
    app.dependency_overrides[get_plugin_registry] = lambda: registry
    app.dependency_overrides[get_credential_store] = lambda: store
    app.dependency_overrides[get_model_router] = lambda: fake_router

    try:
        async with app_client.websocket_connect(f"/api/v1/ws/{session_id}") as ws:
            await ws.send_json({"type": "chat.message",
                                "payload": {"text": "hi", "project_id": str(project.id)}})
            events = []
            while True:
                e = await ws.receive_json()
                events.append(e)
                if e["type"] == "chat.done":
                    break
    finally:
        app.dependency_overrides.pop(get_plugin_registry, None)
        app.dependency_overrides.pop(get_credential_store, None)
        app.dependency_overrides.pop(get_model_router, None)

    assert not any(e["type"] in ("chat.tool_use", "chat.tool_result") for e in events)
```

- [ ] **Step 2: Run the tests (fail)**

```bash
uv run pytest apps/api/atlas_api/tests/test_ws_chat_tool_use.py -v
```

Expected: 4 FAIL (handler doesn't run a tool-use loop yet).

- [ ] **Step 3: Implement the loop in `apps/api/atlas_api/ws/chat.py`**

Read the current handler. Locate the section where `provider.stream(...)` is called. The change has these phases:

1. **Build the tool list** before the first `provider.stream` call:
   ```python
   tools_payload = None
   if registry := getattr(websocket.app.state, "plugin_registry", None):
       enabled = list(project.enabled_plugins or [])
       schemas = registry.get_tool_schemas(enabled=enabled)
       if schemas and provider.spec.provider == "anthropic":
           tools_payload = [_to_anthropic_tool(s) for s in schemas]
   ```

   Helper at module scope:
   ```python
   def _to_anthropic_tool(s: ToolSchema) -> dict[str, Any]:
       return {"name": s.name, "description": s.description, "input_schema": s.parameters}
   ```

2. **Wrap the existing single-turn streaming in a loop** with a turn counter and the messages list growing per turn:
   ```python
   messages_for_provider = [...existing built messages...]
   tool_turn = 0
   MAX_TOOL_TURNS = 10
   while True:
       pending_tool_calls = []
       async for event in provider.stream(
           messages=messages_for_provider,
           tools=tools_payload,
       ):
           if event.type == ModelEventType.TOKEN:
               # existing token-streaming path: emit chat.token to the WS
               ...
           elif event.type == ModelEventType.TOOL_CALL:
               call = event.data    # {id, tool, args}
               # Emit chat.tool_use start event
               await _send_event(websocket, StreamEventType.TOOL_CALL, {
                   "tool_name": call["tool"], "call_id": call["id"],
                   "started_at": _now_iso(),
               }, sequence=...)
               pending_tool_calls.append(call)
           elif event.type == ModelEventType.DONE:
               # existing usage-recording path
               ...
           elif event.type == ModelEventType.ERROR:
               # existing error path; break out of both loops
               ...
       if not pending_tool_calls:
           break
       tool_turn += 1
       # Dispatch each pending tool call.
       tool_results = []
       for call in pending_tool_calls:
           started = time.monotonic()
           result = await registry.invoke(call["tool"], call["args"], call_id=call["id"])
           duration_ms = int((time.monotonic() - started) * 1000)
           ok = result.error is None
           await _send_event(websocket, StreamEventType.TOOL_RESULT, {
               "tool_name": call["tool"], "call_id": call["id"],
               "ok": ok, "duration_ms": duration_ms,
           }, sequence=...)
           tool_results.append(result)
       # Append the assistant's tool_use turn + the user-side tool_result turn to messages.
       messages_for_provider.append({
           "role": "assistant",
           "content": [
               {"type": "tool_use", "id": c["id"], "name": c["tool"], "input": c["args"]}
               for c in pending_tool_calls
           ],
       })
       messages_for_provider.append({
           "role": "user",
           "content": [
               {"type": "tool_result",
                "tool_use_id": r.call_id,
                "content": json.dumps(r.result) if r.error is None else f"Error: {r.error}",
                "is_error": r.error is not None}
               for r in tool_results
           ],
       })
       if tool_turn >= MAX_TOOL_TURNS:
           # Force a final non-tool turn: drop tools, add a system instruction.
           tools_payload = None
           messages_for_provider.append({
               "role": "user",
               "content": "Tool call limit reached; respond to the user without using tools.",
           })
           # Loop one more time; the next iteration will yield text only.
           continue
   # After the loop, run the existing post-stream persistence (Message rows, etc.)
   ```

3. **Persist tool calls to the Message row.** The existing `Message` model has `tool_calls: list[dict] | None`. Persist the list of (tool, args, result/error) dicts so future replays can reconstruct the conversation. Specifically, on the assistant message persist:
   ```python
   message.tool_calls = [
       {"call_id": c["id"], "tool": c["tool"], "args": c["args"], "result": ...}
       for c in all_calls_from_all_turns
   ]
   ```
   Track a `all_calls_from_all_turns` list across the while loop.

The full code must be written by reading `chat.py` end-to-end and adapting; the above is the structural pattern.

- [ ] **Step 4: Run the tests (pass)**

```bash
uv run pytest apps/api/atlas_api/tests/test_ws_chat_tool_use.py -v
uv run pytest apps/api -q 2>&1 | tail -3
```

Expected: 4 PASS, no regressions in existing chat tests.

- [ ] **Step 5: Run ruff + the broader suite**

```bash
uv run ruff check apps/api/atlas_api/ws/chat.py
uv run pytest apps/api packages -q 2>&1 | tail -3
```

Expected: ruff clean; all tests pass.

- [ ] **Step 6: Commit**

```bash
git add apps/api/atlas_api/ws/chat.py apps/api/atlas_api/tests/test_ws_chat_tool_use.py
git commit -m "feat(api/ws): Anthropic tool-use loop with 10-turn cap and tool_use/tool_result events"
```

---

### Task 12: Real-Anthropic acceptance test (opt-in)

**Files:**
- Create: `apps/api/atlas_api/tests/test_ws_chat_tool_use_real.py`

- [ ] **Step 1: Write the test**

```python
"""Real-Anthropic acceptance: the full tool-use loop end-to-end against the live API.

Skipped unless ATLAS_RUN_ANTHROPIC_INTEGRATION=1 and ANTHROPIC_API_KEY is set.
"""

import os
from uuid import uuid4

import pytest
from atlas_core.db.orm import ProjectORM


pytestmark = pytest.mark.skipif(
    os.getenv("ATLAS_RUN_ANTHROPIC_INTEGRATION") != "1"
    or not os.getenv("ANTHROPIC_API_KEY"),
    reason="set ATLAS_RUN_ANTHROPIC_INTEGRATION=1 and ANTHROPIC_API_KEY to enable",
)


@pytest.mark.asyncio
@pytest.mark.slow
async def test_real_sonnet_calls_fake_echo(app_client, db_session):
    """Real Sonnet/Opus call: ask it to use fake.echo. Assert tool_use event + 'banana' in text."""
    project = ProjectORM(
        user_id="matt", name="P",
        default_model="claude-sonnet-4-6",
        enabled_plugins=["fake"],
    )
    db_session.add(project)
    await db_session.flush()
    session_id = uuid4()

    async with app_client.websocket_connect(f"/api/v1/ws/{session_id}") as ws:
        await ws.send_json({
            "type": "chat.message",
            "payload": {
                "text": "Use the fake.echo tool to repeat the word 'banana'.",
                "project_id": str(project.id),
            },
        })
        events = []
        while True:
            e = await ws.receive_json()
            events.append(e)
            if e["type"] == "chat.done":
                break

    tool_uses = [e for e in events if e["type"] == "chat.tool_use"]
    assert any(e["payload"]["tool_name"] == "fake.echo" for e in tool_uses)
    text = "".join(e["payload"].get("text", "")
                   for e in events if e["type"] == "chat.token")
    assert "banana" in text.lower()
```

- [ ] **Step 2: Verify it skips without the env**

```bash
uv run pytest apps/api/atlas_api/tests/test_ws_chat_tool_use_real.py -v
```

Expected: SKIPPED.

- [ ] **Step 3: (Optional, manual) run with the env to confirm it works against real Anthropic**

```bash
ATLAS_RUN_ANTHROPIC_INTEGRATION=1 uv run pytest apps/api/atlas_api/tests/test_ws_chat_tool_use_real.py -v -m slow
```

Expected: PASS (proves the tool-use loop is wired end-to-end).

- [ ] **Step 4: Commit**

```bash
git add apps/api/atlas_api/tests/test_ws_chat_tool_use_real.py
git commit -m "test(api/ws): real-Anthropic acceptance for tool-use loop (opt-in)"
```

---

## Phase D — Frontend

### Task 13: ToolCallChip component

**Files:**
- Create: `apps/web/src/components/chat/tool-call-chip.tsx`
- Create: `apps/web/src/components/chat/tool-call-chip.test.tsx`

- [ ] **Step 1: Write the failing tests**

```tsx
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { ToolCallChip } from "./tool-call-chip";

describe("ToolCallChip", () => {
  it("renders pending state with spinner and tool name", () => {
    render(<ToolCallChip toolName="fake.echo" status="pending" />);
    expect(screen.getByText("fake.echo")).toBeInTheDocument();
    expect(screen.getByLabelText(/calling tool/i)).toBeInTheDocument();
  });

  it("renders ok state with check and duration", () => {
    render(<ToolCallChip toolName="fake.echo" status="ok" durationMs={234} />);
    expect(screen.getByText("fake.echo")).toBeInTheDocument();
    expect(screen.getByText(/234.?ms/i)).toBeInTheDocument();
  });

  it("renders error state with X and duration", () => {
    render(<ToolCallChip toolName="fake.fail" status="error" durationMs={50} />);
    expect(screen.getByText("fake.fail")).toBeInTheDocument();
    expect(screen.getByLabelText(/tool failed/i)).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run tests (fail)**

```bash
cd apps/web && pnpm test src/components/chat/tool-call-chip.test.tsx
```

Expected: file not found.

- [ ] **Step 3: Implement the component**

```tsx
import { Check, Loader2, X } from "lucide-react";
import { cn } from "@/lib/cn";

interface Props {
  toolName: string;
  status: "pending" | "ok" | "error";
  durationMs?: number;
}

export function ToolCallChip({ toolName, status, durationMs }: Props) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-xs font-mono",
        status === "pending" && "border-blue-300 bg-blue-50 text-blue-900",
        status === "ok" && "border-emerald-300 bg-emerald-50 text-emerald-900",
        status === "error" && "border-red-300 bg-red-50 text-red-900",
      )}
    >
      {status === "pending" && (
        <Loader2 aria-label="calling tool" className="h-3 w-3 animate-spin" />
      )}
      {status === "ok" && <Check className="h-3 w-3" />}
      {status === "error" && (
        <X aria-label="tool failed" className="h-3 w-3" />
      )}
      <span>{toolName}</span>
      {status !== "pending" && durationMs !== undefined && (
        <span className="text-muted-foreground">({durationMs}ms)</span>
      )}
    </span>
  );
}
```

- [ ] **Step 4: Run tests (pass)**

```bash
cd apps/web && pnpm test src/components/chat/tool-call-chip.test.tsx
```

Expected: 3 PASS.

- [ ] **Step 5: typecheck + lint**

```bash
cd apps/web && pnpm typecheck && pnpm lint
```

Expected: clean.

- [ ] **Step 6: Commit**

```bash
git add apps/web/src/components/chat/tool-call-chip.tsx apps/web/src/components/chat/tool-call-chip.test.tsx
git commit -m "feat(web/chat): ToolCallChip component for tool-use indicators"
```

---

### Task 14: Chat store extensions for tool events

**Files:**
- Modify: `apps/web/src/stores/<chat-store>.ts` (file name varies — locate via grep below)
- Modify: that store's test file
- Modify: the assistant-message renderer to render the chip strip

- [ ] **Step 1: Locate the chat store**

```bash
grep -rln "chat\.token\|StreamEvent\|tool_use" apps/web/src 2>&1 | head -10
```

Locate the file that processes WS events from `/ws/{session_id}`. Likely candidates: `apps/web/src/stores/chat-store.ts` or `apps/web/src/components/chat/chat-panel.tsx` or `apps/web/src/hooks/use-chat-ws.ts`.

- [ ] **Step 2: Write the failing test (against the located store)**

The test should:
1. Construct a fresh store, simulate a `chat.tool_use` event arriving, assert a new ToolCall with status=pending is appended to the current assistant message.
2. Simulate a matching `chat.tool_result` event, assert the ToolCall's status flips to "ok" (or "error" if `payload.ok=false`) and `durationMs` is set.
3. Simulate a `chat.tool_result` with an unknown `call_id`, assert it is logged + ignored without crashing.

Exact code shape depends on the store's existing API.

- [ ] **Step 3: Run tests (fail)**

Expected: store doesn't handle the new events.

- [ ] **Step 4: Extend the store**

Add `toolCalls: ToolCall[]` to the assistant message shape. Handle:
- `chat.tool_use` event: append `{callId, toolName, status:"pending", startedAt}` to current assistant message's `toolCalls`.
- `chat.tool_result` event: find by `callId` and update `status` (`payload.ok ? "ok" : "error"`) and `durationMs`.

```ts
interface ToolCall {
  callId: string;
  toolName: string;
  status: "pending" | "ok" | "error";
  startedAt: string;
  durationMs?: number;
}
```

- [ ] **Step 5: Wire the renderer**

In the assistant-message component, render a chip strip above the text body:

```tsx
{msg.toolCalls && msg.toolCalls.length > 0 && (
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

- [ ] **Step 6: Run tests + typecheck + lint**

```bash
cd apps/web && pnpm test && pnpm typecheck && pnpm lint
```

Expected: all PASS, typecheck clean, no new lint errors.

- [ ] **Step 7: Commit**

```bash
git add <touched files>
git commit -m "feat(web/chat): handle tool_use/tool_result WS events and render ToolCallChip strip"
```

---

## Phase E — Smoke gate

### Task 15: Manual smoke + acceptance checklist

This task is a verification gate, not a code change. Plan acceptance criteria from the spec § 7.

- [ ] **Step 1: Bring up the stack with master key**

```bash
export ATLAS_PLUGINS__MASTER_KEY="$(uv run python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())')"
echo "Add this to .env so the api container picks it up:"
echo "ATLAS_PLUGINS__MASTER_KEY=${ATLAS_PLUGINS__MASTER_KEY}"
# Add to infra/.env then:
cd /Users/redam94/Coding/Projects/atlas-agent/infra
docker compose build api web
docker compose up -d api web
uv run alembic upgrade head
```

- [ ] **Step 2: Check the framework**

```bash
curl -s http://localhost:8000/api/v1/plugins | python3 -m json.tool
```

Expected: FakePlugin with `health.ok=true`.

- [ ] **Step 3: Direct invocation**

```bash
curl -s -X POST http://localhost:8000/api/v1/plugins/fake/invoke \
  -H 'content-type: application/json' \
  -d '{"tool_name":"fake.echo","args":{"text":"banana"}}' | python3 -m json.tool
curl -s -X POST http://localhost:8000/api/v1/plugins/fake/invoke \
  -H 'content-type: application/json' \
  -d '{"tool_name":"fake.fail","args":{}}' | python3 -m json.tool
```

Expected: echo returns `result={echo: banana}`, fail returns `error="forced failure"`.

- [ ] **Step 4: Credentials CRUD**

```bash
curl -s -X POST http://localhost:8000/api/v1/plugins/fake/credentials \
  -H 'content-type: application/json' \
  -d '{"account_id":"alice","payload":{"foo":"bar"}}'
curl -s http://localhost:8000/api/v1/plugins/fake/credentials
curl -s -X DELETE http://localhost:8000/api/v1/plugins/fake/credentials/alice
```

Expected: 201 → `["alice"]` → 204.

- [ ] **Step 5: Enable FakePlugin on a project**

```bash
docker exec atlas-postgres psql -U atlas -d atlas \
  -c "UPDATE projects SET enabled_plugins = ARRAY['fake'] WHERE id = (SELECT id FROM projects LIMIT 1);"
```

- [ ] **Step 6: Chat tool-use end-to-end**

In the web UI with Sonnet/Opus selected on that project, send: "Use the fake.echo tool to repeat the word 'banana'."

Expected:
- Tool-use chip appears (pending → ok with duration).
- Final assistant text contains "banana".

- [ ] **Step 7: Tool-toggle test**

```bash
docker exec atlas-postgres psql -U atlas -d atlas \
  -c "UPDATE projects SET enabled_plugins = ARRAY[]::text[] WHERE id = (SELECT id FROM projects LIMIT 1);"
```

Reload the UI; same prompt. Expected: model says it can't use tools, no chip.

- [ ] **Step 8: Safe-mode test**

Edit `infra/.env`, comment out `ATLAS_PLUGINS__MASTER_KEY=...`, then:

```bash
cd infra && docker compose up -d api
sleep 3
curl -s http://localhost:8000/api/v1/plugins | python3 -m json.tool
```

Expected: API boots, FakePlugin shown with `health.ok=false`. Restore the key after.

- [ ] **Step 9: Commit smoke results**

If all 9 acceptance criteria pass:

```bash
git commit --allow-empty -m "test(plan-1): manual smoke — all 9 acceptance criteria pass"
```

If any fail, file a follow-up before claiming done.

---

## Self-review

Spec coverage check (against `2026-04-29-atlas-phase-3-plan-1-plugin-framework-design.md`):

- §3.1 plugin contract → Tasks 1, 4 (ABC + FakePlugin).
- §3.2 registry → Tasks 3, 7 (registry + REST).
- §3.3 credential store → Tasks 2 (in-memory), 6 (Postgres).
- §3.4 Anthropic tool-use loop → Tasks 9 (provider events), 11 (handler loop).
- §3.5 WS event format → Task 11 (handler emits) + Task 14 (FE handles).
- §3.6 per-project enablement → Task 5 (DB column) + Task 11 (handler reads).
- §4.1 package layout → Tasks 1–4.
- §4.2 ORM → Task 5.
- §4.3 migration → Task 5.
- §4.4 REST router → Task 7.
- §4.5 lifespan wiring → Task 8.
- §4.6 chat WS handler → Task 11.
- §5 frontend → Tasks 13–14.
- §6 testing → covered across all tasks; opt-in real-Anthropic in Task 12.
- §7 acceptance → Task 15.
- §8 risks — informational, no task needed.

**Spec drift to flag in the commit log of Task 1 / Task 7:** the design spec described `ToolSchema.input_schema` and `ToolResult.{ok, value, error}`. The plan reuses the existing `atlas_core.models.llm` types (`ToolSchema.parameters`, `ToolResult.{call_id, tool, result, error}`) which Phase 1 pre-laid. Behavior is equivalent (`error is None` <=> success). Plan 1 commits should note this in their messages so the spec/plan delta is in the git history.

**Naming consistency check:**
- `CredentialStore`, `CredentialBackend`, `InMemoryBackend`, `SqlAlchemyBackend` — used consistently in Tasks 2, 6, 7, 8.
- `AtlasPlugin`, `PluginRegistry`, `PluginInfo`, `HealthStatus` — Tasks 1, 3, 7.
- `REGISTERED_PLUGINS` — Tasks 3, 4, 8.
- `FakePlugin.{name, get_tools, invoke}` — Tasks 4, 7, 11.
- `ToolSchema.parameters` (not `input_schema`) — Tasks 1, 9, 11. Adapter `_to_anthropic_tool` in Task 11 maps `parameters` → `input_schema` at the SDK boundary.
- `ToolResult.{call_id, tool, result, error}` — Tasks 3, 7, 11.

**Placeholder scan:** no TBDs, every step has full code or a precise instruction-with-code-pattern (Tasks 10, 11, 14 reference reading existing files because the code can only be written by adapting to what's there; the structural pattern is given in full).

No spec gap.
