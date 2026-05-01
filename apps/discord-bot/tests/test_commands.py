"""Tests for bot slash command handlers."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import UUID

import pytest
from atlas_discord_bot.chunker import MAX_CHUNK
from atlas_discord_bot.commands.ask import ask_handler
from atlas_discord_bot.commands.ingest import ingest_handler
from atlas_discord_bot.commands.status import status_handler


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
    assert sent is not None
