"""Tests for the chat WS handler's tool-use loop (Plan 1, Phase 3).

Uses httpx_ws + ASGIWebSocketTransport to exercise the WS handler directly,
the same pattern used by test_ws_chat.py and test_ws_chat_rag.py.
"""

from __future__ import annotations

import contextlib
from uuid import uuid4

import httpx
import pytest
import pytest_asyncio
from atlas_core.config import AtlasConfig
from atlas_core.db.orm import MessageORM, ProjectORM
from atlas_core.models.sessions import MessageRole
from atlas_core.providers._fake import FakeProvider
from atlas_core.providers.registry import ModelRegistry, ModelRouter
from atlas_knowledge.embeddings import FakeEmbedder
from atlas_knowledge.retrieval.retriever import Retriever
from atlas_knowledge.vector.chroma import ChromaVectorStore
from atlas_plugins import CredentialStore, FakePlugin, HealthStatus, InMemoryBackend, PluginRegistry
from cryptography.fernet import Fernet
from httpx_ws import aconnect_ws
from httpx_ws.transport import ASGIWebSocketTransport
from sqlalchemy import select

from atlas_api.deps import (
    get_credential_store,
    get_model_router,
    get_plugin_registry,
    get_retriever,
    get_session,
    get_settings,
)
from atlas_api.main import app

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@contextlib.asynccontextmanager
async def make_ws_client():
    """Per-call async context manager that yields an AsyncClient backed by
    ASGIWebSocketTransport.  Entering and exiting in the same task avoids
    CancelScope cross-task RuntimeError."""
    async with httpx.AsyncClient(
        transport=ASGIWebSocketTransport(app=app), base_url="http://test"
    ) as client:
        yield client


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_registry():
    """A PluginRegistry with a healthy FakePlugin installed."""
    store = CredentialStore(backend=InMemoryBackend(), master_key=Fernet.generate_key().decode())
    plugin = FakePlugin(credentials=store)
    reg = PluginRegistry([plugin])
    reg._health = {"fake": HealthStatus(ok=True)}
    return reg, store


@pytest_asyncio.fixture
async def tool_use_overrides(db_session, tmp_path, fake_registry):
    """Set common dependency overrides for tool-use tests.

    Yields (registry, store) so tests can also override get_model_router per-test.
    Clears overrides in teardown.
    """
    registry, store = fake_registry
    fake_retriever = Retriever(
        embedder=FakeEmbedder(dim=16),
        vector_store=ChromaVectorStore(persist_dir=str(tmp_path / "chroma"), user_id="matt"),
    )

    async def _override_session():
        yield db_session

    app.dependency_overrides[get_session] = _override_session
    app.dependency_overrides[get_settings] = lambda: AtlasConfig()
    app.dependency_overrides[get_retriever] = lambda: fake_retriever
    app.dependency_overrides[get_plugin_registry] = lambda: registry
    app.dependency_overrides[get_credential_store] = lambda: store

    yield registry, store

    app.dependency_overrides.clear()


def _make_router(fake_provider: FakeProvider) -> ModelRouter:
    """Build a ModelRouter that always returns fake_provider."""
    reg = ModelRegistry()
    reg.register(fake_provider)
    router = ModelRouter(reg)
    router.select = lambda project, model_override=None: fake_provider  # type: ignore[assignment]
    return router


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_single_tool_call_round_trip(db_session, tool_use_overrides):
    """Model returns one tool_use → handler dispatches → model's next turn returns text."""
    project = ProjectORM(
        user_id="matt",
        name="P",
        default_model="claude-sonnet-4-6",
        enabled_plugins=["fake"],
    )
    db_session.add(project)
    await db_session.flush()
    session_id = uuid4()

    fake_provider = FakeProvider(
        scripted_turns=[
            {"tool_calls": [{"id": "tu_1", "tool": "fake.echo", "args": {"text": "hi"}}]},
            {"text": "The echo was hi."},
        ]
    )
    # Spec must say "anthropic" so the handler attaches tools (frozen model → model_copy)
    fake_provider.spec = fake_provider.spec.model_copy(update={"provider": "anthropic"})
    app.dependency_overrides[get_model_router] = lambda: _make_router(fake_provider)

    async with (
        make_ws_client() as client,
        aconnect_ws(f"ws://test/api/v1/ws/{session_id}", client=client) as ws,
    ):
        await ws.send_json(
            {
                "type": "chat.message",
                "payload": {"text": "echo hi", "project_id": str(project.id)},
            }
        )
        events = []
        while True:
            e = await ws.receive_json()
            events.append(e)
            if e["type"] in ("chat.done", "chat.error"):
                break

    types = [e["type"] for e in events]
    assert "chat.tool_use" in types
    assert "chat.tool_result" in types
    assert any(
        e["type"] == "chat.token" and "echo was hi" in e["payload"].get("token", "")
        for e in events
    )

    # Verify the assistant Message row's tool_calls field was populated.
    await db_session.commit()  # ensure data is visible across the session boundary
    result = await db_session.execute(
        select(MessageORM).where(
            MessageORM.session_id == session_id,
            MessageORM.role == MessageRole.ASSISTANT.value,
        )
    )
    assistant_rows = result.scalars().all()
    assert len(assistant_rows) >= 1
    # Find the row that has tool_calls (the latest assistant message).
    rows_with_tools = [r for r in assistant_rows if r.tool_calls]
    assert len(rows_with_tools) >= 1, "expected at least one assistant row with tool_calls populated"
    latest = rows_with_tools[-1]
    assert len(latest.tool_calls) == 1
    assert latest.tool_calls[0]["tool"] == "fake.echo"


@pytest.mark.asyncio
async def test_ten_turn_cap_forces_final_summary(db_session, tool_use_overrides):
    """Model that always tool_calls hits the 10-turn cap and then must respond without tools."""
    project = ProjectORM(
        user_id="matt",
        name="P",
        default_model="claude-sonnet-4-6",
        enabled_plugins=["fake"],
    )
    db_session.add(project)
    await db_session.flush()
    session_id = uuid4()

    # 10 tool-calling turns + 1 final text turn (after handler force-disables tools).
    turns = [
        {"tool_calls": [{"id": f"tu_{i}", "tool": "fake.recurse", "args": {"depth": i}}]}
        for i in range(10)
    ] + [{"text": "Stopped recursing."}]

    fake_provider = FakeProvider(scripted_turns=turns)
    fake_provider.spec = fake_provider.spec.model_copy(update={"provider": "anthropic"})
    app.dependency_overrides[get_model_router] = lambda: _make_router(fake_provider)

    async with (
        make_ws_client() as client,
        aconnect_ws(f"ws://test/api/v1/ws/{session_id}", client=client) as ws,
    ):
        await ws.send_json(
            {
                "type": "chat.message",
                "payload": {"text": "go", "project_id": str(project.id)},
            }
        )
        events = []
        while True:
            e = await ws.receive_json()
            events.append(e)
            if e["type"] in ("chat.done", "chat.error"):
                break

    tool_use_events = [e for e in events if e["type"] == "chat.tool_use"]
    tool_result_events = [e for e in events if e["type"] == "chat.tool_result"]
    assert len(tool_use_events) == 10
    assert len(tool_result_events) == 10
    assert any(
        e["type"] == "chat.token" and "Stopped" in e["payload"].get("token", "") for e in events
    )

    # The 11th call to provider.stream MUST have tools=None — this is the
    # cap-firing assertion. Without it, the test would pass even if the cap
    # logic were silently removed, because the scripted turn 10 has no
    # tool_calls anyway.
    assert len(fake_provider.stream_calls) == 11
    assert fake_provider.stream_calls[10]["tools"] is None, (
        "11th stream call must drop tools= per the 10-turn cap behavior"
    )
    # Sanity: prior 10 calls did pass tools.
    for i in range(10):
        assert fake_provider.stream_calls[i]["tools"] is not None, (
            f"call {i}: tools should be set during the loop"
        )


@pytest.mark.asyncio
async def test_tool_failure_returns_error_in_tool_result_event(db_session, tool_use_overrides):
    """fake.fail raises; handler emits a tool_result event with ok=false."""
    project = ProjectORM(
        user_id="matt",
        name="P",
        default_model="claude-sonnet-4-6",
        enabled_plugins=["fake"],
    )
    db_session.add(project)
    await db_session.flush()
    session_id = uuid4()

    fake_provider = FakeProvider(
        scripted_turns=[
            {"tool_calls": [{"id": "tu_1", "tool": "fake.fail", "args": {}}]},
            {"text": "Tool failed but I'm telling you about it."},
        ]
    )
    fake_provider.spec = fake_provider.spec.model_copy(update={"provider": "anthropic"})
    app.dependency_overrides[get_model_router] = lambda: _make_router(fake_provider)

    async with (
        make_ws_client() as client,
        aconnect_ws(f"ws://test/api/v1/ws/{session_id}", client=client) as ws,
    ):
        await ws.send_json(
            {
                "type": "chat.message",
                "payload": {"text": "fail please", "project_id": str(project.id)},
            }
        )
        events = []
        while True:
            e = await ws.receive_json()
            events.append(e)
            if e["type"] in ("chat.done", "chat.error"):
                break

    tool_results = [e for e in events if e["type"] == "chat.tool_result"]
    assert len(tool_results) == 1
    assert tool_results[0]["payload"]["ok"] is False


@pytest.mark.asyncio
async def test_lmstudio_provider_does_not_get_tool_events(db_session, tool_use_overrides):
    """When provider is not anthropic, no tool-use events are emitted."""
    project = ProjectORM(
        user_id="matt",
        name="P",
        default_model="local-model",
        enabled_plugins=["fake"],
    )
    db_session.add(project)
    await db_session.flush()
    session_id = uuid4()

    # FakeProvider with provider="lmstudio" — scripted as text-only, no tool calls
    fake_provider = FakeProvider(scripted_turns=[{"text": "no tools here"}])
    fake_provider.spec = fake_provider.spec.model_copy(update={"provider": "lmstudio"})
    app.dependency_overrides[get_model_router] = lambda: _make_router(fake_provider)

    async with (
        make_ws_client() as client,
        aconnect_ws(f"ws://test/api/v1/ws/{session_id}", client=client) as ws,
    ):
        await ws.send_json(
            {
                "type": "chat.message",
                "payload": {"text": "hi", "project_id": str(project.id)},
            }
        )
        events = []
        while True:
            e = await ws.receive_json()
            events.append(e)
            if e["type"] in ("chat.done", "chat.error"):
                break

    assert not any(e["type"] in ("chat.tool_use", "chat.tool_result") for e in events)
