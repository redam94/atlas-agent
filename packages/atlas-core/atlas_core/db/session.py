"""Async engine and session factory construction.

Pure functions, no global state. The FastAPI app builds these once at
startup (in `lifespan`) and stashes them on `app.state`.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.engine.url import make_url
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from atlas_core.config import AtlasConfig


def _normalize_url(url: str) -> str:
    """Rewrite postgresql:// → postgresql+asyncpg:// so users don't have to."""
    parsed = make_url(url)
    if parsed.drivername == "postgresql":
        # Use .render_as_string(hide_password=False) to avoid masking password
        parsed = parsed.set(drivername="postgresql+asyncpg")
        return parsed.render_as_string(hide_password=False)
    return parsed.render_as_string(hide_password=False)


def create_engine_from_config(config: AtlasConfig) -> AsyncEngine:
    """Build an AsyncEngine from `AtlasConfig`. Disposes are the caller's job."""
    url = _normalize_url(config.db.database_url.get_secret_value())
    return create_async_engine(
        url,
        echo=False,
        pool_pre_ping=True,  # cheap reconnect-on-stale-connection
    )


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """Build the per-request session factory bound to an engine."""
    return async_sessionmaker(
        bind=engine,
        class_=AsyncSession,
        expire_on_commit=False,  # detach instances survive commit (Pydantic-friendly)
        autoflush=False,
    )


@asynccontextmanager
async def session_scope(
    session_factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncSession]:
    """Yield a session from the factory. Commit on clean exit, rollback on error.

    Usable from any async context — FastAPI HTTP, FastAPI WebSocket, Celery,
    plain scripts. The HTTP-specific `apps/api/atlas_api/deps.get_session`
    delegates to this helper.
    """
    async with session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
