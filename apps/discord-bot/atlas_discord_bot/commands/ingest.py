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
