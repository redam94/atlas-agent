"""Entry point for the ATLAS Discord bot.

Starts three concurrent asyncio tasks:
  1. discord.py gateway connection (commands, events)
  2. uvicorn serving internal_app on port 8001 (inbound API→bot)
  3. notification_poller (10s tick)
"""

from __future__ import annotations

import asyncio

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
