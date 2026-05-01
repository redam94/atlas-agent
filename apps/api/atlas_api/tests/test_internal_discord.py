"""Tests for /api/v1/internal/discord/* endpoints."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest
from atlas_core.db.orm import IngestionJobORM, ProjectORM

SECRET = "test-secret-abc"


@pytest.fixture(autouse=True)
def patch_secret(monkeypatch):
    monkeypatch.setenv("ATLAS_DISCORD__INTERNAL_SECRET", SECRET)


@pytest.mark.asyncio
async def test_chat_missing_secret_returns_401(app_client):
    resp = await app_client.post(
        "/api/v1/internal/discord/chat",
        json={"project_id": "00000000-0000-0000-0000-000000000001", "prompt": "hi"},
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_chat_wrong_secret_returns_401(app_client):
    resp = await app_client.post(
        "/api/v1/internal/discord/chat",
        headers={"X-Internal-Secret": "wrong"},
        json={"project_id": "00000000-0000-0000-0000-000000000001", "prompt": "hi"},
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_chat_project_not_found_returns_404(app_client):
    resp = await app_client.post(
        "/api/v1/internal/discord/chat",
        headers={"X-Internal-Secret": SECRET},
        json={"project_id": "00000000-0000-0000-0000-000000000099", "prompt": "hi"},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_chat_happy_path_returns_text(app_client, db_session):
    proj = ProjectORM(
        id=uuid.uuid4(),
        user_id="test-user",
        name="test",
        default_model="claude-haiku-4-5-20251001",
        enabled_plugins=[],
    )
    db_session.add(proj)
    await db_session.flush()

    with patch(
        "atlas_api.routers._internal.discord.run_turn_collected",
        new=AsyncMock(return_value="hello from agent"),
    ):
        resp = await app_client.post(
            "/api/v1/internal/discord/chat",
            headers={"X-Internal-Secret": SECRET},
            json={"project_id": str(proj.id), "prompt": "hello"},
        )

    assert resp.status_code == 200
    assert resp.json()["text"] == "hello from agent"


@pytest.mark.asyncio
async def test_jobs_pending_missing_secret(app_client):
    resp = await app_client.get("/api/v1/internal/discord/jobs/pending")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_jobs_pending_returns_empty(app_client):
    resp = await app_client.get(
        "/api/v1/internal/discord/jobs/pending",
        headers={"X-Internal-Secret": SECRET},
    )
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.asyncio
async def test_mark_notified_updates_row(app_client, db_session):
    proj = ProjectORM(
        id=uuid.uuid4(),
        user_id="test-user",
        name="test",
        default_model="claude-haiku-4-5-20251001",
        enabled_plugins=[],
    )
    db_session.add(proj)
    await db_session.flush()

    job = IngestionJobORM(
        id=uuid.uuid4(),
        user_id="test-user",
        project_id=proj.id,
        source_type="url",
        status="completed",
        completed_at=datetime.now(UTC),
    )
    db_session.add(job)
    await db_session.flush()

    resp = await app_client.post(
        f"/api/v1/internal/discord/jobs/{job.id}/mark_notified",
        headers={"X-Internal-Secret": SECRET},
    )
    assert resp.status_code == 200
    await db_session.refresh(job)
    assert job.notified_at is not None


@pytest.mark.asyncio
async def test_discord_status_returns_postgres_ok(app_client):
    resp = await app_client.get(
        "/api/v1/internal/discord/status",
        headers={"X-Internal-Secret": SECRET},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["postgres"] == "ok"
    assert "running_jobs" in body


@pytest.mark.asyncio
async def test_mark_stale_notified_bulk_marks_old_jobs(app_client, db_session):
    """Jobs completed > 10 min ago with notified_at=None should be marked notified."""
    proj = ProjectORM(
        id=uuid.uuid4(),
        user_id="test-user",
        name="test",
        default_model="claude-haiku-4-5-20251001",
        enabled_plugins=[],
    )
    db_session.add(proj)
    await db_session.flush()

    old_job = IngestionJobORM(
        id=uuid.uuid4(),
        user_id="test-user",
        project_id=proj.id,
        source_type="url",
        status="completed",
        completed_at=datetime.now(UTC) - timedelta(minutes=15),
    )
    db_session.add(old_job)
    await db_session.flush()

    resp = await app_client.post(
        "/api/v1/internal/discord/jobs/mark_stale_notified",
        headers={"X-Internal-Secret": SECRET},
    )
    assert resp.status_code == 200
    await db_session.refresh(old_job)
    assert old_job.notified_at is not None
