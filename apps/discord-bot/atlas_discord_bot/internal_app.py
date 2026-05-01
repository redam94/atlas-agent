"""Mini FastAPI app the bot runs for inbound API→bot calls.

Endpoints:
  POST /internal/discord/send  — agent-initiated message send
"""

from __future__ import annotations

import os

import discord
from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel

from atlas_discord_bot.chunker import chunk_text

app = FastAPI(title="atlas-discord-bot-internal")

# Injected by __main__ after the discord client is created
_bot: discord.Client | None = None


def set_bot(bot: discord.Client) -> None:
    global _bot
    _bot = bot


async def _require_secret(x_internal_secret: str | None = Header(default=None, alias="X-Internal-Secret")) -> None:
    expected = os.getenv("ATLAS_DISCORD__INTERNAL_SECRET")
    if not expected or x_internal_secret != expected:
        raise HTTPException(status_code=401, detail="unauthorized")


class SendRequest(BaseModel):
    channel_id: str
    body: str


class SendResponse(BaseModel):
    message_id: str | None = None


@app.post("/internal/discord/send", response_model=SendResponse)
async def send_message(
    req: SendRequest,
    _: None = Depends(_require_secret),
) -> SendResponse:
    if _bot is None:
        raise HTTPException(status_code=503, detail="bot not ready")
    channel = _bot.get_channel(int(req.channel_id))
    if channel is None:
        raise HTTPException(status_code=404, detail=f"channel {req.channel_id} not found")
    chunks = chunk_text(req.body)
    last_msg = None
    for chunk in chunks:
        last_msg = await channel.send(chunk)
    return SendResponse(message_id=str(last_msg.id) if last_msg else None)
