"""Alembic env, async-aware. Reads DB URL from AtlasConfig at runtime."""
import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from atlas_core.config import AtlasConfig
from atlas_core.db.base import Base
from atlas_core.db.session import _normalize_url

# Import ORM models so Base.metadata sees them. Imports are deliberate
# (do not remove unused-import noqa) — registration is a side effect.
from atlas_core.db import orm  # noqa: F401

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _resolved_url() -> str:
    return _normalize_url(AtlasConfig().db.database_url.get_secret_value())


def run_migrations_offline() -> None:
    """Run migrations without a live DB connection (generates SQL)."""
    context.configure(
        url=_resolved_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """Run migrations against a live DB connection."""
    from sqlalchemy.ext.asyncio import create_async_engine

    url = _resolved_url()
    connectable = create_async_engine(
        url,
        echo=False,
        pool_pre_ping=True,
    )
    async with connectable.begin() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
