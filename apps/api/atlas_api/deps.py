"""FastAPI dependency providers.

These wrap stateful resources (config, DB session) so handlers stay
testable. Tests override these via `app.dependency_overrides`.
"""

from collections.abc import AsyncIterator

from atlas_core.config import AtlasConfig
from atlas_core.db.session import session_scope
from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession


def get_settings(request: Request) -> AtlasConfig:
    """Return the AtlasConfig stored on app.state by the lifespan."""
    return request.app.state.config


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    """HTTP-route dependency that yields an AsyncSession.

    Delegates to `session_scope`, which is also reusable directly from
    WebSocket handlers and Celery tasks where there is no `Request`.
    Tests override THIS function (not session_scope) via
    `app.dependency_overrides`.
    """
    async with session_scope(request.app.state.session_factory) as session:
        yield session
