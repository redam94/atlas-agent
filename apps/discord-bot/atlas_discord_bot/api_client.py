"""Typed httpx wrapper for calls from the bot to the ATLAS API."""

from __future__ import annotations

from uuid import UUID

import httpx


class APIClient:
    def __init__(self, *, base_url: str, internal_secret: str, timeout: float = 60.0) -> None:
        self._base = base_url.rstrip("/")
        self._secret = internal_secret
        self._timeout = timeout

    def _headers(self) -> dict[str, str]:
        return {"X-Internal-Secret": self._secret}

    async def chat(self, project_id: UUID | str, prompt: str) -> str:
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.post(
                f"{self._base}/api/v1/internal/discord/chat",
                headers=self._headers(),
                json={"project_id": str(project_id), "prompt": prompt},
            )
            resp.raise_for_status()
            return resp.json()["text"]

    async def ingest_url(
        self, project_id: UUID | str, url: str, discord_channel_id: str | None = None
    ) -> dict:
        payload: dict = {"project_id": str(project_id), "url": url}
        if discord_channel_id:
            payload["discord_channel_id"] = discord_channel_id
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f"{self._base}/api/v1/knowledge/ingest/url",
                json=payload,
            )
            resp.raise_for_status()
            return resp.json()

    async def get_pending_jobs(self) -> list[dict]:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                f"{self._base}/api/v1/internal/discord/jobs/pending",
                headers=self._headers(),
            )
            resp.raise_for_status()
            return resp.json()

    async def mark_notified(self, job_id: str) -> None:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                f"{self._base}/api/v1/internal/discord/jobs/{job_id}/mark_notified",
                headers=self._headers(),
            )
            resp.raise_for_status()

    async def mark_stale_notified(self) -> None:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                f"{self._base}/api/v1/internal/discord/jobs/mark_stale_notified",
                headers=self._headers(),
            )
            resp.raise_for_status()

    async def get_status(self) -> dict:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                f"{self._base}/api/v1/internal/discord/status",
                headers=self._headers(),
            )
            resp.raise_for_status()
            return resp.json()

    async def healthz(self) -> dict:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(f"{self._base}/health")
            resp.raise_for_status()
            return resp.json()
