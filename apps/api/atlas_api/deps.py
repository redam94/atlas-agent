"""FastAPI dependency providers.

These wrap stateful resources (config, DB session) so handlers stay
testable. Tests override these via `app.dependency_overrides`.
"""
from collections.abc import AsyncIterator

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from atlas_core.config import AtlasConfig


def get_settings(request: Request) -> AtlasConfig:
    """Return the AtlasConfig stored on app.state by the lifespan."""
    return request.app.state.config


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    """Yield an AsyncSession from the per-app session factory.

    Commits on clean exit; rolls back on exception. The test suite overrides
    this to inject a savepointed session for per-test isolation.
    """
    session_factory = request.app.state.session_factory
    async with session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


# Re-exported for type clarity at import sites.
SessionDep = Depends(get_session)
SettingsDep = Depends(get_settings)
