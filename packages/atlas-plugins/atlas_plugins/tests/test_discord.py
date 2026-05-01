"""Tests for DiscordPlugin."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from atlas_plugins import CredentialStore
from atlas_plugins.context import reset_interactive, set_interactive
from atlas_plugins.credentials import InMemoryBackend
from atlas_plugins.discord.plugin import DiscordPlugin


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
