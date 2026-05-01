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
