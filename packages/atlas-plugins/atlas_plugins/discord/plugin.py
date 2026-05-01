"""Discord plugin — exposes discord.send_message to the agent.

Credential schema (stored in plugin_credentials):
  {"default_channel_id": "<channel-snowflake>"}

Env vars (not in credential store):
  ATLAS_DISCORD__INTERNAL_SECRET  — shared secret for bot HTTP calls
  ATLAS_DISCORD__BOT_URL          — bot internal app URL (default: http://discord-bot:8001)

Confirmation gate:
  Interactive callers (WS chat): first call returns draft+token; second call executes.
  Non-interactive callers (/atlas ask): gate bypassed, posts immediately.
"""

from __future__ import annotations

import os
import time
import uuid
from typing import Any

import httpx
import structlog
from atlas_core.models.llm import ToolSchema

from atlas_plugins.base import AtlasPlugin, HealthStatus
from atlas_plugins.context import is_interactive

log = structlog.get_logger("atlas.plugins.discord")

_TOKEN_TTL_SECONDS = 300


class DiscordPlugin(AtlasPlugin):
    name = "discord"
    description = "Send messages to Discord and manage bot interactions."

    def __init__(self, credentials) -> None:
        super().__init__(credentials)
        # In-memory confirmation gate: {token: {"body": str, "channel_id": str, "expires": float}}
        self._pending: dict[str, dict[str, Any]] = {}

    def get_tools(self) -> list[ToolSchema]:
        return [
            ToolSchema(
                name="discord.send_message",
                description=(
                    "Send a message to the configured Discord channel. "
                    "In interactive sessions, the first call returns a draft preview + token; "
                    "call again with confirm_token to actually send. "
                    "body: the message text to send. "
                    "confirm_token: token from a previous draft call to confirm and send."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "body": {"type": "string", "description": "Message text to send."},
                        "confirm_token": {
                            "type": "string",
                            "description": "Confirmation token from a prior draft call.",
                        },
                    },
                },
                plugin="discord",
            )
        ]

    async def health(self) -> HealthStatus:
        try:
            creds = await self._get_credentials()
            if not creds.get("default_channel_id"):
                return HealthStatus(ok=False, detail="default_channel_id missing from credentials")
            return HealthStatus(ok=True)
        except Exception as e:
            return HealthStatus(ok=False, detail=str(e))

    async def invoke(self, tool_name: str, args: dict[str, Any]) -> Any:
        if tool_name != "discord.send_message":
            raise ValueError(f"unknown tool {tool_name!r}")
        return await self._send_message(args)

    async def _send_message(self, args: dict[str, Any]) -> dict[str, Any]:
        self._expire_pending()
        creds = await self._get_credentials()
        channel_id = creds["default_channel_id"]

        confirm_token = args.get("confirm_token")
        body = args.get("body")

        if confirm_token is not None:
            entry = self._pending.pop(confirm_token, None)
            if entry is None or entry["expires"] < time.monotonic():
                raise ValueError("confirm_token expired or invalid")
            return await self._post_to_bot(entry["channel_id"], entry["body"])

        if body is None:
            raise ValueError("either body or confirm_token must be provided")

        if is_interactive():
            token = str(uuid.uuid4())
            self._pending[token] = {
                "body": body,
                "channel_id": channel_id,
                "expires": time.monotonic() + _TOKEN_TTL_SECONDS,
            }
            return {"preview": {"body": body, "channel_id": channel_id}, "draft_token": token}

        return await self._post_to_bot(channel_id, body)

    async def _post_to_bot(self, channel_id: str, body: str) -> dict[str, Any]:
        bot_url = os.getenv("ATLAS_DISCORD__BOT_URL", "http://discord-bot:8001")
        secret = os.getenv("ATLAS_DISCORD__INTERNAL_SECRET", "")
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f"{bot_url}/internal/discord/send",
                json={"channel_id": channel_id, "body": body},
                headers={"X-Internal-Secret": secret},
            )
            resp.raise_for_status()
            data = resp.json()
        return {"posted": True, "message_id": data.get("message_id")}

    def _expire_pending(self) -> None:
        now = time.monotonic()
        expired = [k for k, v in self._pending.items() if v["expires"] < now]
        for k in expired:
            del self._pending[k]
