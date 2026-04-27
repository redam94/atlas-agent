"""Integration tests for the chat WebSocket using async httpx-ws client.

Uses ASGIWebSocketTransport so the WS handler and the test's db_session
share the same asyncio event loop — avoiding asyncpg cross-loop errors that
occur when using TestClient (which spawns its own portal thread).

The transport and AsyncClient are created per-test (inside each test body)
because ASGIWebSocketTransport uses anyio CancelScope internally, and
CancelScope must be entered and exited in the same asyncio Task. Pytest-asyncio
fixture teardown runs in a different task context, which causes RuntimeError on
exit if the transport is opened as a fixture-level context manager.
"""

import contextlib
from uuid import UUID

import httpx
import pytest
import pytest_asyncio
from atlas_core.config import AtlasConfig
from atlas_core.db.orm import MessageORM, SessionORM
from atlas_core.providers import FakeProvider
from atlas_core.providers.registry import ModelRegistry, ModelRouter
from atlas_knowledge.embeddings import FakeEmbedder
from atlas_knowledge.retrieval.retriever import Retriever
from atlas_knowledge.vector.chroma import ChromaVectorStore
from httpx_ws import aconnect_ws
from httpx_ws.transport import ASGIWebSocketTransport
from sqlalchemy import select

from atlas_api.deps import get_model_router, get_retriever, get_session, get_settings
from atlas_api.main import app


@pytest.fixture
def fake_router():
    """ModelRouter that always returns a FakeProvider regardless of project config."""
    reg = ModelRegistry()
    fp = FakeProvider(model_id="fake-1", token_chunks=["hello", " ", "world"])
    reg.register(fp)
    router = ModelRouter(reg)
    # Override select() to ignore project policy and always return our fake
    router.select = lambda project, model_override=None: fp  # type: ignore[assignment]
    return router


@pytest_asyncio.fixture
async def set_overrides(db_session, fake_router, tmp_path):
    """Set dependency overrides for DB session, model router, settings, retriever.

    Yields the fake_router so tests can mutate it to inject failures.
    Clears overrides in teardown.
    """

    async def _override_session():
        yield db_session

    def _override_router():
        return fake_router

    def _override_settings():
        return AtlasConfig()

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


@contextlib.asynccontextmanager
async def make_ws_client():
    """Per-call async context manager that yields an AsyncClient backed by
    ASGIWebSocketTransport. Both enter and exit happen in the same task,
    avoiding the CancelScope cross-task RuntimeError."""
    async with httpx.AsyncClient(
        transport=ASGIWebSocketTransport(app=app), base_url="http://test"
    ) as client:
        yield client


async def _seed_project(db_session) -> UUID:
    """Insert a project row and return its id."""
    from atlas_core.db.orm import ProjectORM

    p = ProjectORM(
        user_id="matt",
        name="WSTest",
        default_model="fake-1",
    )
    db_session.add(p)
    await db_session.flush()
    return p.id


@pytest.mark.asyncio
async def test_ws_chat_streams_tokens_and_persists_messages(set_overrides, db_session):
    project_id = await _seed_project(db_session)
    from uuid import uuid4

    session_id = uuid4()

    async with (
        make_ws_client() as client,
        aconnect_ws(
            f"ws://test/api/v1/ws/{session_id}",
            client=client,
        ) as ws,
    ):
        await ws.send_json(
            {
                "type": "chat.message",
                "payload": {"text": "hi", "project_id": str(project_id)},
            }
        )

        events = []
        while True:
            msg = await ws.receive_json()
            events.append(msg)
            if msg["type"] in ("chat.done", "chat.error"):
                break

    types = [e["type"] for e in events]
    assert "chat.token" in types
    assert types[-1] == "chat.done"
    # FakeProvider yields 3 token chunks
    token_count = sum(1 for t in types if t == "chat.token")
    assert token_count == 3

    # Persistence: 2 message rows (user + assistant) + 1 session row
    sessions = (await db_session.execute(select(SessionORM))).scalars().all()
    messages = (await db_session.execute(select(MessageORM))).scalars().all()
    assert len(sessions) == 1
    assert len(messages) == 2
    roles = {m.role for m in messages}
    assert roles == {"user", "assistant"}


@pytest.mark.asyncio
async def test_ws_chat_emits_error_event_on_provider_failure(set_overrides, db_session):
    project_id = await _seed_project(db_session)
    # Reconfigure the fake to fail
    failing = FakeProvider(model_id="fake-1", error_on_call=True)
    set_overrides.select = lambda project, model_override=None: failing  # type: ignore[assignment]

    from uuid import uuid4

    session_id = uuid4()

    async with (
        make_ws_client() as client,
        aconnect_ws(
            f"ws://test/api/v1/ws/{session_id}",
            client=client,
        ) as ws,
    ):
        await ws.send_json(
            {
                "type": "chat.message",
                "payload": {"text": "hi", "project_id": str(project_id)},
            }
        )
        msg = await ws.receive_json()
        # The first event should be the error
        assert msg["type"] == "chat.error"


@pytest.mark.asyncio
async def test_ws_chat_rejects_unknown_message_type(set_overrides, db_session):
    from uuid import uuid4

    async with (
        make_ws_client() as client,
        aconnect_ws(
            f"ws://test/api/v1/ws/{uuid4()}",
            client=client,
        ) as ws,
    ):
        await ws.send_json({"type": "weird.unknown", "payload": {}})
        msg = await ws.receive_json()
        assert msg["type"] == "chat.error"
        assert "unknown" in msg["payload"]["message"].lower()


@pytest.mark.asyncio
async def test_ws_chat_partial_assistant_not_persisted_on_disconnect(set_overrides, db_session):
    """DoD #9: closing the WS mid-stream does not commit the partial assistant turn.

    The handler flushes the user_row BEFORE the streaming loop. If the client
    disconnects during streaming, send_json raises WebSocketDisconnect before the
    assistant_row is ever added to the session. Because the test db_session lives
    in a savepoint (never committed), we can verify that no assistant MessageORM row
    exists after the WS closes.
    """
    import asyncio

    from atlas_core.models.llm import ModelEvent, ModelEventType, ModelSpec
    from atlas_core.providers.base import BaseModel as ProviderBase

    class _SlowProvider(ProviderBase):
        """Yields many tokens with tiny sleeps so the next send_json detects disconnect."""

        def __init__(self):
            self.spec = ModelSpec(
                provider="fake",
                model_id="slow-1",
                context_window=1024,
                supports_tools=False,
                supports_streaming=True,
            )

        async def stream(self, messages, tools=None, temperature=0.7, max_tokens=4096):
            for i in range(50):
                yield ModelEvent(type=ModelEventType.TOKEN, data={"text": f"t{i}"})
                await asyncio.sleep(0.02)

    slow = _SlowProvider()
    set_overrides.select = lambda project, model_override=None: slow  # type: ignore[assignment]

    project_id = await _seed_project(db_session)
    from uuid import uuid4

    session_id = uuid4()

    async with (
        make_ws_client() as client,
        aconnect_ws(
            f"ws://test/api/v1/ws/{session_id}",
            client=client,
        ) as ws,
    ):
        await ws.send_json(
            {
                "type": "chat.message",
                "payload": {"text": "hi", "project_id": str(project_id)},
            }
        )
        # Receive the first token, then close — server will detect disconnect
        # on the next send_json attempt.
        first = await ws.receive_json()
        assert first["type"] == "chat.token"
        # Exiting the context manager closes the WS connection.

    # Give the server a moment to process the disconnect.
    await asyncio.sleep(0.1)

    # The assistant_row is only added AFTER the streaming loop completes (ws/chat.py line 185+).
    # A mid-stream disconnect exits the loop early via WebSocketDisconnect before that point,
    # so no assistant row should ever be flushed.
    messages = (await db_session.execute(select(MessageORM))).scalars().all()
    assistant_messages = [m for m in messages if m.role == "assistant"]
    assert len(assistant_messages) == 0, "no partial assistant row should persist after disconnect"
