"""Tests for atlas_core.db.session."""
import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from atlas_core.config import AtlasConfig
from atlas_core.db.session import create_engine_from_config, create_session_factory


def _config_with_url(monkeypatch, url: str) -> AtlasConfig:
    monkeypatch.setenv("ATLAS_DB__DATABASE_URL", url)
    return AtlasConfig()


def test_create_engine_returns_async_engine(monkeypatch):
    cfg = _config_with_url(monkeypatch, "postgresql+asyncpg://atlas:atlas@localhost:5432/atlas")
    engine = create_engine_from_config(cfg)
    assert isinstance(engine, AsyncEngine)


def test_create_engine_rewrites_postgres_scheme_to_asyncpg(monkeypatch):
    """A bare postgresql:// URL is silently upgraded to postgresql+asyncpg://."""
    cfg = _config_with_url(monkeypatch, "postgresql://atlas:atlas@localhost:5432/atlas")
    engine = create_engine_from_config(cfg)
    assert engine.url.drivername == "postgresql+asyncpg"


def test_create_session_factory_returns_async_sessionmaker(monkeypatch):
    cfg = _config_with_url(monkeypatch, "postgresql+asyncpg://atlas:atlas@localhost:5432/atlas")
    engine = create_engine_from_config(cfg)
    factory = create_session_factory(engine)
    assert isinstance(factory, async_sessionmaker)
