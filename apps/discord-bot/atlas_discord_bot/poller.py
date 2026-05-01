"""Ingestion-complete notification poller.

Runs every 10 seconds. Queries /internal/discord/jobs/pending for completed/failed
jobs with notified_at IS NULL (freshness enforced server-side to last 10 minutes).
Posts a notification to the job's discord_channel_id or the fallback channel, then
marks the job notified.

On startup, call api_client.mark_stale_notified() to silence old unnotified jobs.
"""

from __future__ import annotations

import asyncio
from typing import Any

import structlog

from atlas_discord_bot.api_client import APIClient

log = structlog.get_logger("atlas.discord.poller")

POLL_INTERVAL = 10  # seconds


async def poll_once(
    *,
    bot: Any,  # discord.Client
    api_client: APIClient,
    fallback_channel_id: str | None,
) -> None:
    try:
        jobs = await api_client.get_pending_jobs()
    except Exception as e:
        log.warning("discord.poller.fetch_failed", error=str(e))
        return

    for job in jobs:
        job_id = job["id"]
        status = job["status"]
        filename = job.get("source_filename") or "unknown source"
        error = job.get("error")
        channel_id_str = job.get("discord_channel_id") or fallback_channel_id

        if channel_id_str:
            channel = bot.get_channel(int(channel_id_str))
            if channel is not None:
                if status == "completed":
                    text = f"✅ ingested `{filename}`"
                else:
                    text = f"❌ ingestion failed for `{filename}`: {error or 'unknown error'}"
                try:
                    await channel.send(text)
                except Exception as e:
                    log.warning("discord.poller.send_failed", job_id=job_id, error=str(e))

        try:
            await api_client.mark_notified(job_id)
        except Exception as e:
            log.warning("discord.poller.mark_failed", job_id=job_id, error=str(e))


async def run_poller(
    *,
    bot: Any,
    api_client: APIClient,
    fallback_channel_id: str | None,
) -> None:
    """Infinite polling loop. Run as an asyncio task."""
    while True:
        await poll_once(bot=bot, api_client=api_client, fallback_channel_id=fallback_channel_id)
        await asyncio.sleep(POLL_INTERVAL)
