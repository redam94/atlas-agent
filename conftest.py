"""Pytest configuration for ATLAS test suites.

Sets required environment variables BEFORE pytest collects test modules
(some modules construct AtlasConfig at import time). Also provides
session-scoped DB fixtures: ensures the `atlas_test` database exists,
runs Alembic migrations to head once per session, and yields per-test
async sessions wrapped in savepoints for isolation.
"""

import contextlib
import os
from collections.abc import AsyncIterator
from pathlib import Path

# Defaults BEFORE imports below — they trigger pydantic-settings.
os.environ.setdefault(
    "ATLAS_DB__DATABASE_URL", "postgresql://atlas:atlas@localhost:5432/atlas_test"
)
os.environ.setdefault("ATLAS_GRAPH__PASSWORD", "test")
os.environ.setdefault("ATLAS_ENVIRONMENT", "development")

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

REPO_ROOT = Path(__file__).parent
TEST_DB_NAME = "atlas_test"
ADMIN_DB_URL = "postgresql+asyncpg://atlas:atlas@localhost:5432/postgres"
TEST_DB_URL = f"postgresql+asyncpg://atlas:atlas@localhost:5432/{TEST_DB_NAME}"


def _ensure_test_database_exists() -> None:
    """Create the `atlas_test` DB if it doesn't already exist (sync, one-shot)."""
    import psycopg2
    from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT

    conn = psycopg2.connect(
        host="localhost", port=5432, user="atlas", password="atlas", dbname="postgres"
    )
    conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (TEST_DB_NAME,))
            if cur.fetchone() is None:
                cur.execute(f'CREATE DATABASE "{TEST_DB_NAME}"')
    finally:
        conn.close()


def _run_migrations_to_head() -> None:
    """Run Alembic upgrade head against the test DB (sync — Alembic spawns its own loop)."""
    from alembic import command
    from alembic.config import Config

    cfg = Config(str(REPO_ROOT / "alembic.ini"))
    # Point Alembic at the test DB. env.py reads the URL from AtlasConfig,
    # which already has ATLAS_DB__DATABASE_URL set to the test URL above.
    command.upgrade(cfg, "head")


@pytest.fixture(scope="session", autouse=True)
def setup_test_database():
    """Once per test session: create test DB, migrate to head."""
    _ensure_test_database_exists()
    _run_migrations_to_head()


@pytest_asyncio.fixture(scope="session")
async def db_engine():
    """Session-scoped async engine for the test DB."""
    engine = create_async_engine(TEST_DB_URL, pool_pre_ping=True, poolclass=NullPool)
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture(scope="function")
async def db_session(db_engine) -> AsyncIterator[AsyncSession]:
    """Per-test async session with savepoint rollback for isolation.

    The outer transaction is rolled back at the end of every test, so any
    INSERT/UPDATE inside the test vanishes — no truncates needed.
    """
    async with db_engine.connect() as conn:
        outer_tx = await conn.begin()
        async with AsyncSession(bind=conn, expire_on_commit=False) as session:
            await session.begin_nested()  # savepoint so handler-level commits stay scoped
            try:
                yield session
            finally:
                await session.close()
        await outer_tx.rollback()


@pytest_asyncio.fixture(scope="function")
async def app_client(db_session):
    """FastAPI ASGI test client with `get_session` overridden to use db_session.

    Yields an `httpx.AsyncClient` bound to the app via ASGITransport.
    Also manually invokes the lifespan to ensure app.state is initialized.
    """
    from atlas_api.deps import get_session
    from atlas_api.main import app
    from httpx import ASGITransport, AsyncClient

    async def _override_session():
        yield db_session

    app.dependency_overrides[get_session] = _override_session

    # Manually trigger lifespan startup
    lifespan_manager = app.router.lifespan_context(app)
    await lifespan_manager.__aenter__()

    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            yield client
    finally:
        with contextlib.suppress(Exception):
            await lifespan_manager.__aexit__(None, None, None)
        app.dependency_overrides.clear()
