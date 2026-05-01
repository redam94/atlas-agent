"""Internal Discord endpoints — callable only by the discord-bot service.

Auth: X-Internal-Secret header, validated against ATLAS_DISCORD__INTERNAL_SECRET env var.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import structlog
from atlas_core.config import AtlasConfig
from atlas_core.db.orm import IngestionJobORM, ProjectORM
from atlas_core.prompts.builder import SystemPromptBuilder
from atlas_core.prompts.registry import prompt_registry
from atlas_plugins import PluginRegistry
from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from atlas_api.deps import get_model_router, get_plugin_registry, get_session, get_settings
from atlas_api.services.agent_runner import run_turn_collected, to_anthropic_tool

log = structlog.get_logger("atlas.api.internal.discord")
router = APIRouter(tags=["internal-discord"])
_prompt_builder = SystemPromptBuilder(prompt_registry)


async def _require_secret(x_internal_secret: str | None = Header(default=None, alias="X-Internal-Secret")) -> None:
    expected = os.getenv("ATLAS_DISCORD__INTERNAL_SECRET")
    if not expected or x_internal_secret != expected:
        raise HTTPException(status_code=401, detail="unauthorized")


class ChatRequest(BaseModel):
    project_id: UUID
    prompt: str
    temperature: float = 1.0


class ChatResponse(BaseModel):
    text: str


class PendingJob(BaseModel):
    id: UUID
    status: str
    source_filename: str | None
    discord_channel_id: str | None
    error: str | None


@router.post("/internal/discord/chat", response_model=ChatResponse)
async def discord_chat(
    req: ChatRequest,
    _: None = Depends(_require_secret),
    db: AsyncSession = Depends(get_session),
    model_router: Any = Depends(get_model_router),
    plugin_registry: PluginRegistry | None = Depends(get_plugin_registry),
    settings: AtlasConfig = Depends(get_settings),
) -> ChatResponse:
    project = await db.get(ProjectORM, req.project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="project not found")

    from atlas_core.db.converters import project_from_orm
    proj = project_from_orm(project)

    try:
        provider = model_router.select(proj)
    except ValueError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e

    tools_payload = None
    if plugin_registry is not None and provider.spec.provider == "anthropic":
        enabled = list(project.enabled_plugins or [])
        schemas = plugin_registry.get_tool_schemas(enabled=enabled)
        if schemas:
            tools_payload = [to_anthropic_tool(s) for s in schemas]

    system_prompt = _prompt_builder.build(proj)
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": req.prompt},
    ]

    text = await run_turn_collected(
        provider=provider,
        messages=messages,
        tools_payload=tools_payload,
        plugin_registry=plugin_registry,
        interactive=False,
        temperature=req.temperature,
    )
    return ChatResponse(text=text)


@router.post("/internal/discord/jobs/mark_stale_notified")
async def mark_stale_notified(
    _: None = Depends(_require_secret),
    db: AsyncSession = Depends(get_session),
) -> dict:
    cutoff = datetime.now(UTC) - timedelta(minutes=10)
    now = datetime.now(UTC)
    await db.execute(
        update(IngestionJobORM)
        .where(
            IngestionJobORM.status.in_(["completed", "failed"]),
            IngestionJobORM.notified_at.is_(None),
            IngestionJobORM.completed_at < cutoff,
        )
        .values(notified_at=now)
    )
    await db.flush()
    return {"ok": True}


@router.get("/internal/discord/jobs/pending", response_model=list[PendingJob])
async def get_pending_jobs(
    _: None = Depends(_require_secret),
    db: AsyncSession = Depends(get_session),
) -> list[PendingJob]:
    cutoff = datetime.now(UTC) - timedelta(minutes=10)
    result = await db.execute(
        select(IngestionJobORM).where(
            IngestionJobORM.status.in_(["completed", "failed"]),
            IngestionJobORM.notified_at.is_(None),
            IngestionJobORM.completed_at >= cutoff,
        )
    )
    rows = result.scalars().all()
    return [
        PendingJob(
            id=r.id,
            status=r.status,
            source_filename=r.source_filename,
            discord_channel_id=r.discord_channel_id,
            error=r.error,
        )
        for r in rows
    ]


@router.post("/internal/discord/jobs/{job_id}/mark_notified")
async def mark_notified(
    job_id: UUID,
    _: None = Depends(_require_secret),
    db: AsyncSession = Depends(get_session),
) -> dict:
    job = await db.get(IngestionJobORM, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    job.notified_at = datetime.now(UTC)
    await db.flush()
    return {"ok": True}


@router.get("/internal/discord/status")
async def discord_status(
    _: None = Depends(_require_secret),
    db: AsyncSession = Depends(get_session),
) -> dict:
    running = await db.scalar(
        select(func.count(IngestionJobORM.id)).where(IngestionJobORM.status == "running")
    )
    return {"postgres": "ok", "running_jobs": running or 0}
