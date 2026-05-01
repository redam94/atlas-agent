"""Tests for the notification poller."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

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
