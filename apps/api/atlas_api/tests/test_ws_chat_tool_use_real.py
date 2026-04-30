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
        user_id="matt",
        name="P",
        default_model="claude-sonnet-4-6",
        enabled_plugins=["fake"],
    )
    db_session.add(project)
    await db_session.flush()
    session_id = uuid4()

    async with app_client.websocket_connect(f"/api/v1/ws/{session_id}") as ws:
        await ws.send_json(
            {
                "type": "chat.message",
                "payload": {
                    "text": "Use the fake.echo tool to repeat the word 'banana'.",
                    "project_id": str(project.id),
                },
            }
        )
        events = []
        while True:
            e = await ws.receive_json()
            events.append(e)
            if e["type"] == "chat.done":
                break

    tool_uses = [e for e in events if e["type"] == "chat.tool_use"]
    assert any(e["payload"]["tool_name"] == "fake.echo" for e in tool_uses)
    text = "".join(
        e["payload"].get("text", "") for e in events if e["type"] == "chat.token"
    )
    assert "banana" in text.lower()
