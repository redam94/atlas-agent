"""Handler for /atlas ask."""

from __future__ import annotations

from typing import Any
from uuid import UUID

import discord

from atlas_discord_bot.api_client import APIClient
from atlas_discord_bot.chunker import chunk_text


async def ask_handler(
    interaction: Any,
    prompt: str,
    *,
    api_client: APIClient,
    project_id: UUID,
) -> None:
    await interaction.response.defer()
    try:
        text = await api_client.chat(project_id, prompt)
    except TimeoutError:
        await interaction.followup.send("❌ chat timed out")
        return
    except Exception as e:
        await interaction.followup.send(f"❌ chat failed: {e}")
        return
    for chunk in chunk_text(text):
        await interaction.followup.send(chunk)


def setup(tree: discord.app_commands.CommandTree, guild: discord.Object, *, settings, api_client: APIClient) -> None:
    @tree.command(name="ask", description="Ask ATLAS a question", guild=guild)
    @discord.app_commands.describe(prompt="Your question for the ATLAS agent")
    async def _ask(interaction: discord.Interaction, prompt: str) -> None:
        await ask_handler(interaction, prompt, api_client=api_client, project_id=settings.default_project_id)
