"""FastAPI dependency providers.

These wrap stateful resources (config, DB session) so handlers stay
testable. Tests override these via `app.dependency_overrides`.

Parameters use ``HTTPConnection`` (the common base of ``Request`` and
``WebSocket``) so the same dependency works for both REST and WebSocket
routes.
"""

from collections.abc import AsyncIterator

from atlas_core.config import AtlasConfig
from atlas_core.db.session import session_scope
from atlas_core.providers.registry import ModelRegistry, ModelRouter
from atlas_graph import GraphStore
from atlas_knowledge.ingestion.service import IngestionService
from atlas_knowledge.retrieval import RetrieverProtocol
from atlas_plugins import CredentialStore, PluginRegistry
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import HTTPConnection


def get_settings(connection: HTTPConnection) -> AtlasConfig:
    """Return the AtlasConfig stored on app.state by the lifespan."""
    return connection.app.state.config


async def get_session(connection: HTTPConnection) -> AsyncIterator[AsyncSession]:
    """Dependency that yields an AsyncSession for both HTTP and WebSocket routes.

    Delegates to ``session_scope``, which is also reusable directly from
    Celery tasks where there is no connection. Tests override THIS function
    (not session_scope) via ``app.dependency_overrides``.
    """
    async with session_scope(connection.app.state.session_factory) as session:
        yield session


def get_model_registry(connection: HTTPConnection) -> ModelRegistry:
    return connection.app.state.model_registry


def get_model_router(connection: HTTPConnection) -> ModelRouter:
    return connection.app.state.model_router


def get_ingestion_service(connection: HTTPConnection) -> IngestionService:
    return connection.app.state.ingestion_service


def get_retriever(connection: HTTPConnection) -> RetrieverProtocol:
    return connection.app.state.retriever


def get_graph_store(connection: HTTPConnection) -> GraphStore:
    return connection.app.state.graph_store


def get_plugin_registry(connection: HTTPConnection) -> PluginRegistry | None:
    return getattr(connection.app.state, "plugin_registry", None)


def get_credential_store(connection: HTTPConnection) -> CredentialStore:
    return connection.app.state.credential_store
