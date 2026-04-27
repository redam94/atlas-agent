# ATLAS Phase 1 — Plan 2: Domain Models + Postgres Schema + Project REST CRUD

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Implements:** `docs/superpowers/specs/2026-04-26-atlas-phase-1-foundation-design.md` — §3 (`Project` Pydantic models), §9 (project REST endpoints), §10 (Postgres schema for `projects` table only — other tables ship with the plans that need them).

**Goal:** Manage projects via REST against a real Postgres database. End state: `curl -X POST /api/v1/projects -d '{...}'` creates a row in `projects`; `GET /api/v1/projects` lists them; PATCH updates; DELETE soft-archives. Tests run against a real Postgres test database with per-test transaction rollback isolation.

**Architecture:** Async SQLAlchemy 2.x with asyncpg as the driver. Alembic owns the schema. Pydantic models in `atlas-core/models/projects.py` (domain DTOs); SQLAlchemy ORM models in `atlas-core/db/orm.py` (storage layer). FastAPI `Depends(get_session)` injects an async session per request. **No Repository layer** — query logic lives inline in the routers; we'll extract one if/when a second consumer needs it (YAGNI).

**Tech Stack:** SQLAlchemy 2.x async · asyncpg · Alembic · FastAPI dependency injection · pytest-asyncio with savepoint-rollback fixtures · `psycopg2-binary` (Alembic-only sync driver for offline migrations).

---

## File Structure

```
atlas-agent/
├── alembic.ini                                  # NEW (repo root)
├── conftest.py                                  # MODIFIED (add test-db fixtures)
├── apps/api/
│   ├── pyproject.toml                           # MODIFIED (no new deps; uses atlas-core)
│   └── atlas_api/
│       ├── deps.py                              # NEW (get_settings, get_session, app state)
│       ├── main.py                              # MODIFIED (lifespan creates/disposes engine, wires router)
│       └── routers/
│           ├── __init__.py                      # NEW
│           └── projects.py                      # NEW (5 endpoints)
├── packages/atlas-core/
│   ├── pyproject.toml                           # MODIFIED (add sqlalchemy, asyncpg, alembic, psycopg2-binary)
│   └── atlas_core/
│       ├── db/
│       │   ├── __init__.py                      # NEW
│       │   ├── base.py                          # NEW (DeclarativeBase)
│       │   ├── orm.py                           # NEW (Project ORM model)
│       │   └── session.py                       # NEW (engine factory + async_sessionmaker)
│       └── models/
│           └── projects.py                      # NEW (Pydantic: Project, ProjectCreate, ProjectUpdate, enums)
└── infra/alembic/
    ├── env.py                                   # NEW (async-aware env)
    ├── script.py.mako                           # NEW (default template)
    └── versions/
        └── 0001_create_projects_table.py        # NEW
```

**Responsibility per file:**
- `alembic.ini` — Alembic root config, points at `infra/alembic/`, database URL templated from env
- `infra/alembic/env.py` — async migration runner; reads `ATLAS_DB__DATABASE_URL` from env at runtime
- `atlas_core/db/base.py` — single `Base` (`DeclarativeBase`) so all ORM models share metadata
- `atlas_core/db/orm.py` — SQLAlchemy ORM models. This plan: just `ProjectORM`. Later plans append their own.
- `atlas_core/db/session.py` — `create_engine_from_config(config)`, `async_sessionmaker` factory; pure functions, no global state
- `atlas_core/models/projects.py` — Pydantic DTOs and enums
- `apps/api/atlas_api/deps.py` — FastAPI dependencies: `get_settings()`, `get_session()` (yields `AsyncSession` from app state)
- `apps/api/atlas_api/main.py` — modified to create the engine in lifespan, store on `app.state`, dispose on shutdown
- `apps/api/atlas_api/routers/projects.py` — 5 endpoints, single-user filter via hardcoded `user_id` from settings
- `conftest.py` — extended with: ensure `atlas_test` database exists, run Alembic migrations once per session, per-test session with savepoint rollback, FastAPI dependency override

---

## Task 1: Add SQLAlchemy, asyncpg, and Alembic dependencies

**Files:**
- Modify: `packages/atlas-core/pyproject.toml`

- [ ] **Step 1: Update `dependencies` in `packages/atlas-core/pyproject.toml`**

Open the file and replace the entire `dependencies = [...]` block with:

```toml
dependencies = [
    "pydantic>=2.10",
    "pydantic-settings>=2.7",
    "structlog>=24.4",
    "sqlalchemy[asyncio]>=2.0.36",
    "asyncpg>=0.30",
    "alembic>=1.14",
    "psycopg2-binary>=2.9",  # Alembic offline mode + tests use sync URL for DB creation
]
```

Leave the rest of the file (`[project]` metadata, `[build-system]`, `[tool.hatch.build.targets.wheel]`) unchanged.

- [ ] **Step 2: Re-sync the workspace**

Run: `uv sync --all-packages`
Expected: `uv` resolves and installs the new deps without errors. The lock file is updated.

- [ ] **Step 3: Verify imports**

Run:
```bash
uv run python -c "import sqlalchemy, asyncpg, alembic, psycopg2; print(sqlalchemy.__version__, asyncpg.__version__, alembic.__version__)"
```
Expected: prints version numbers, no `ModuleNotFoundError`.

- [ ] **Step 4: Commit**

```bash
git add packages/atlas-core/pyproject.toml uv.lock
git commit -m "chore(atlas-core): add sqlalchemy, asyncpg, alembic deps"
```

---

## Task 2: Add DB session layer (engine + async_sessionmaker)

**Files:**
- Create: `packages/atlas-core/atlas_core/db/__init__.py`
- Create: `packages/atlas-core/atlas_core/db/base.py`
- Create: `packages/atlas-core/atlas_core/db/session.py`
- Create: `packages/atlas-core/atlas_core/tests/test_db_session.py`

- [ ] **Step 1: Create the `db/` package**

```bash
mkdir -p packages/atlas-core/atlas_core/db
```

`packages/atlas-core/atlas_core/db/__init__.py`:
```python
"""Database layer: ORM models, session factory, declarative base."""

from atlas_core.db.base import Base
from atlas_core.db.session import create_engine_from_config, create_session_factory

__all__ = ["Base", "create_engine_from_config", "create_session_factory"]
```

- [ ] **Step 2: Create the declarative base**

`packages/atlas-core/atlas_core/db/base.py`:
```python
"""Single SQLAlchemy DeclarativeBase shared by every ORM model in atlas-core."""
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """All ATLAS ORM models inherit from this base so they share metadata."""
```

- [ ] **Step 3: Write failing tests for the session layer**

`packages/atlas-core/atlas_core/tests/test_db_session.py`:
```python
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
```

Run: `uv run pytest packages/atlas-core/atlas_core/tests/test_db_session.py -v`
Expected: ImportError on `atlas_core.db.session`.

- [ ] **Step 4: Implement `session.py`**

`packages/atlas-core/atlas_core/db/session.py`:
```python
"""Async engine and session factory construction.

Pure functions, no global state. The FastAPI app builds these once at
startup (in `lifespan`) and stashes them on `app.state`.
"""
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
        parsed = parsed.set(drivername="postgresql+asyncpg")
    return str(parsed)


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
```

- [ ] **Step 5: Verify tests pass**

Run: `uv run pytest packages/atlas-core/atlas_core/tests/test_db_session.py -v`
Expected: 3 passed.

- [ ] **Step 6: Commit**

```bash
git add packages/atlas-core/atlas_core/db/ packages/atlas-core/atlas_core/tests/test_db_session.py
git commit -m "feat(atlas-core): add async SQLAlchemy session layer"
```

---

## Task 3: Initialize Alembic with async migration runner

**Files:**
- Create: `alembic.ini`
- Create: `infra/alembic/env.py`
- Create: `infra/alembic/script.py.mako`
- Create: `infra/alembic/versions/` (empty dir, populated next task)

- [ ] **Step 1: Create `alembic.ini` at repo root**

```ini
[alembic]
script_location = infra/alembic
prepend_sys_path = .
version_path_separator = os

# Note: sqlalchemy.url is intentionally blank — env.py reads it from
# AtlasConfig() at runtime so test/dev/prod can each point at their own DB.
sqlalchemy.url =

[post_write_hooks]
hooks = ruff_format
ruff_format.type = console_scripts
ruff_format.entrypoint = ruff
ruff_format.options = format REVISION_SCRIPT_FILENAME

[loggers]
keys = root,sqlalchemy,alembic

[handlers]
keys = console

[formatters]
keys = generic

[logger_root]
level = WARN
handlers = console
qualname =

[logger_sqlalchemy]
level = WARN
handlers =
qualname = sqlalchemy.engine

[logger_alembic]
level = INFO
handlers =
qualname = alembic

[handler_console]
class = StreamHandler
args = (sys.stderr,)
level = NOTSET
formatter = generic

[formatter_generic]
format = %(levelname)-5.5s [%(name)s] %(message)s
datefmt = %H:%M:%S
```

- [ ] **Step 2: Create the directory + script template**

```bash
mkdir -p infra/alembic/versions
```

`infra/alembic/script.py.mako`:
```python
"""${message}

Revision ID: ${up_revision}
Revises: ${down_revision | comma,n}
Create Date: ${create_date}
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
${imports if imports else ""}

# revision identifiers, used by Alembic.
revision: str = ${repr(up_revision)}
down_revision: Union[str, None] = ${repr(down_revision)}
branch_labels: Union[str, Sequence[str], None] = ${repr(branch_labels)}
depends_on: Union[str, Sequence[str], None] = ${repr(depends_on)}


def upgrade() -> None:
    ${upgrades if upgrades else "pass"}


def downgrade() -> None:
    ${downgrades if downgrades else "pass"}
```

- [ ] **Step 3: Create the async-aware `env.py`**

`infra/alembic/env.py`:
```python
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
    config.set_main_option("sqlalchemy.url", _resolved_url())
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
```

- [ ] **Step 4: Verify Alembic can introspect (no migrations exist yet, but `current` should run cleanly)**

Run:
```bash
ATLAS_DB__DATABASE_URL=postgresql://atlas:atlas@localhost:5432/atlas uv run alembic current
```
Expected: prints nothing (no current revision) or a "INFO" banner. No tracebacks.

If you get `Can't load plugin: sqlalchemy.dialects:postgresql.asyncpg`, the deps from Task 1 didn't install correctly — re-run `uv sync --all-packages`.

- [ ] **Step 5: Commit**

```bash
git add alembic.ini infra/alembic/
git commit -m "feat(infra): initialize Alembic with async runner"
```

---

## Task 4: Add `projects` table migration

**Files:**
- Create: `infra/alembic/versions/0001_create_projects_table.py`

- [ ] **Step 1: Generate the revision**

Run:
```bash
ATLAS_DB__DATABASE_URL=postgresql://atlas:atlas@localhost:5432/atlas \
  uv run alembic revision -m "create projects table" --rev-id 0001
```
Expected: A new file appears under `infra/alembic/versions/0001_*.py` with stub `upgrade()` and `downgrade()`. The `--rev-id 0001` flag pins the filename + revision id rather than using a random hash.

- [ ] **Step 2: Replace the stub `upgrade()` and `downgrade()` bodies**

Open `infra/alembic/versions/0001_create_projects_table.py` and replace the `upgrade()` / `downgrade()` functions with:

```python
def upgrade() -> None:
    op.execute('CREATE EXTENSION IF NOT EXISTS "pgcrypto"')  # for gen_random_uuid()
    op.create_table(
        "projects",
        sa.Column(
            "id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("user_id", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "status",
            sa.Text(),
            nullable=False,
            server_default=sa.text("'active'"),
        ),
        sa.Column(
            "privacy_level",
            sa.Text(),
            nullable=False,
            server_default=sa.text("'cloud_ok'"),
        ),
        sa.Column("default_model", sa.Text(), nullable=False),
        sa.Column(
            "enabled_plugins",
            sa.dialects.postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
    )
    op.create_index("projects_user_idx", "projects", ["user_id"])

    # Trigger: refresh updated_at on UPDATE so in-memory model_copy
    # staleness can never bleed into the DB row.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION set_updated_at()
        RETURNS TRIGGER AS $$
        BEGIN
            NEW.updated_at = NOW();
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE TRIGGER projects_set_updated_at
            BEFORE UPDATE ON projects
            FOR EACH ROW EXECUTE FUNCTION set_updated_at();
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS projects_set_updated_at ON projects")
    op.drop_index("projects_user_idx", table_name="projects")
    op.drop_table("projects")
    # set_updated_at() is intentionally not dropped — later tables reuse it.
```

The two `upgrade()` blocks above need:
- `import sqlalchemy as sa` (already in stub)
- `import sqlalchemy.dialects.postgresql` — add this import at the top if it isn't there

If `op.execute(...)` lines have linting complaints from ruff about `E501` (line length), they're suppressed by the project ruff config. If anything else complains, fix the formatting.

- [ ] **Step 3: Apply the migration to dev DB**

Make sure `docker-compose -f infra/docker-compose.yml up -d` is running. Then:

```bash
ATLAS_DB__DATABASE_URL=postgresql://atlas:atlas@localhost:5432/atlas uv run alembic upgrade head
```

Expected: prints `INFO  [alembic.runtime.migration] Running upgrade  -> 0001, create projects table`.

- [ ] **Step 4: Verify the table exists with the trigger**

Run:
```bash
docker exec atlas-postgres psql -U atlas -d atlas -c "\d projects" \
  && docker exec atlas-postgres psql -U atlas -d atlas -c "\d+ projects" | grep -i trigger
```

Expected: `\d projects` shows all 10 columns + the index. `\d+` output mentions `projects_set_updated_at` trigger.

Quick functional check of the trigger:
```bash
docker exec atlas-postgres psql -U atlas -d atlas -c \
  "INSERT INTO projects (user_id, name, default_model) VALUES ('matt', 'smoke', 'claude-sonnet-4-6') RETURNING id, created_at, updated_at;"
docker exec atlas-postgres psql -U atlas -d atlas -c \
  "UPDATE projects SET name='smoke2' WHERE name='smoke' RETURNING name, created_at, updated_at;"
docker exec atlas-postgres psql -U atlas -d atlas -c \
  "DELETE FROM projects WHERE name='smoke2';"
```

Expected: After the UPDATE, `updated_at > created_at` (trigger fired). Cleanup deletes the test row.

- [ ] **Step 5: Commit**

```bash
git add infra/alembic/versions/0001_create_projects_table.py
git commit -m "feat(db): add projects table migration with updated_at trigger"
```

---

## Task 5: Add `Project` SQLAlchemy ORM model

**Files:**
- Create: `packages/atlas-core/atlas_core/db/orm.py`
- Create: `packages/atlas-core/atlas_core/tests/test_db_orm.py`

- [ ] **Step 1: Write the failing tests**

`packages/atlas-core/atlas_core/tests/test_db_orm.py`:
```python
"""Tests for atlas_core.db.orm — pure structural checks (no DB roundtrip)."""
from sqlalchemy import inspect

from atlas_core.db.orm import ProjectORM


def test_project_orm_is_mapped_to_projects_table():
    assert ProjectORM.__tablename__ == "projects"


def test_project_orm_has_expected_columns():
    columns = {c.name for c in inspect(ProjectORM).columns}
    assert columns == {
        "id",
        "user_id",
        "name",
        "description",
        "status",
        "privacy_level",
        "default_model",
        "enabled_plugins",
        "created_at",
        "updated_at",
    }


def test_project_orm_id_is_uuid_primary_key():
    pk_cols = inspect(ProjectORM).primary_key
    assert len(pk_cols) == 1
    assert pk_cols[0].name == "id"
```

Run: `uv run pytest packages/atlas-core/atlas_core/tests/test_db_orm.py -v`
Expected: ImportError on `atlas_core.db.orm`.

- [ ] **Step 2: Implement `orm.py`**

`packages/atlas-core/atlas_core/db/orm.py`:
```python
"""SQLAlchemy ORM models for ATLAS.

Each table in the spec maps to one ORM class here. Plan 2 ships
`ProjectORM`; later plans append `SessionORM`, `MessageORM`, etc.
"""
from datetime import datetime
from uuid import UUID

from sqlalchemy import Index, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from atlas_core.db.base import Base


class ProjectORM(Base):
    """Maps to the `projects` table."""

    __tablename__ = "projects"

    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
        server_default=func.gen_random_uuid(),
    )
    user_id: Mapped[str] = mapped_column(Text, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="active")
    privacy_level: Mapped[str] = mapped_column(
        Text, nullable=False, server_default="cloud_ok"
    )
    default_model: Mapped[str] = mapped_column(Text, nullable=False)
    enabled_plugins: Mapped[list] = mapped_column(
        JSONB, nullable=False, server_default="[]"
    )
    created_at: Mapped[datetime] = mapped_column(
        nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        nullable=False, server_default=func.now()
    )

    __table_args__ = (
        Index("projects_user_idx", "user_id"),
    )
```

- [ ] **Step 3: Verify tests pass**

Run: `uv run pytest packages/atlas-core/atlas_core/tests/test_db_orm.py -v`
Expected: 3 passed.

- [ ] **Step 4: Commit**

```bash
git add packages/atlas-core/atlas_core/db/orm.py packages/atlas-core/atlas_core/tests/test_db_orm.py
git commit -m "feat(atlas-core): add Project ORM model"
```

---

## Task 6: Add `Project` Pydantic models and enums

**Files:**
- Create: `packages/atlas-core/atlas_core/models/projects.py`
- Modify: `packages/atlas-core/atlas_core/models/__init__.py`
- Create: `packages/atlas-core/atlas_core/tests/test_models_projects.py`

- [ ] **Step 1: Write the failing tests**

`packages/atlas-core/atlas_core/tests/test_models_projects.py`:
```python
"""Tests for atlas_core.models.projects."""
from datetime import datetime, timezone
from uuid import uuid4

import pytest
from pydantic import ValidationError

from atlas_core.models.projects import (
    PrivacyLevel,
    Project,
    ProjectCreate,
    ProjectStatus,
    ProjectUpdate,
)


def _make_project(**overrides) -> Project:
    base = {
        "id": uuid4(),
        "user_id": "matt",
        "name": "Test",
        "description": None,
        "status": ProjectStatus.ACTIVE,
        "privacy_level": PrivacyLevel.CLOUD_OK,
        "default_model": "claude-sonnet-4-6",
        "enabled_plugins": [],
        "created_at": datetime.now(timezone.utc),
        "updated_at": datetime.now(timezone.utc),
    }
    return Project(**{**base, **overrides})


def test_privacy_level_values():
    assert PrivacyLevel.CLOUD_OK == "cloud_ok"
    assert PrivacyLevel.LOCAL_ONLY == "local_only"


def test_project_status_values():
    assert ProjectStatus.ACTIVE == "active"
    assert ProjectStatus.PAUSED == "paused"
    assert ProjectStatus.ARCHIVED == "archived"


def test_project_round_trip_via_python_dict():
    """Roundtrip preserves equality when dump uses mode='python' (enums stay enums).

    Note: ``Project`` is strict — a JSON-mode dump (``mode='json'``) converts
    enums to strings, which strict mode cannot revalidate. The router's
    ``_to_pydantic`` helper handles this explicitly.
    """
    p = _make_project()
    dumped = p.model_dump(mode="python")
    restored = Project.model_validate(dumped)
    assert restored == p


def test_project_create_accepts_minimal_payload():
    pc = ProjectCreate.model_validate({"name": "Foo", "default_model": "claude-sonnet-4-6"})
    assert pc.name == "Foo"
    assert pc.privacy_level == PrivacyLevel.CLOUD_OK  # default
    assert pc.description is None
    assert pc.enabled_plugins == []


def test_project_create_coerces_string_privacy_level():
    """AtlasRequestModel base allows JSON string → enum coercion."""
    pc = ProjectCreate.model_validate(
        {"name": "Foo", "default_model": "x", "privacy_level": "local_only"}
    )
    assert pc.privacy_level is PrivacyLevel.LOCAL_ONLY


def test_project_create_rejects_unknown_privacy_level():
    with pytest.raises(ValidationError):
        ProjectCreate.model_validate(
            {"name": "Foo", "default_model": "x", "privacy_level": "unknown"}
        )


def test_project_create_requires_non_empty_name():
    with pytest.raises(ValidationError):
        ProjectCreate.model_validate({"name": "", "default_model": "x"})


def test_project_update_all_fields_optional():
    pu = ProjectUpdate.model_validate({})
    assert pu.name is None
    assert pu.description is None
    assert pu.status is None


def test_project_update_partial_payload():
    pu = ProjectUpdate.model_validate({"name": "Renamed", "status": "paused"})
    assert pu.name == "Renamed"
    assert pu.status is ProjectStatus.PAUSED
    assert pu.privacy_level is None
```

Run: `uv run pytest packages/atlas-core/atlas_core/tests/test_models_projects.py -v`
Expected: ImportError on `atlas_core.models.projects`.

- [ ] **Step 2: Implement `projects.py`**

`packages/atlas-core/atlas_core/models/projects.py`:
```python
"""Pydantic domain models for Project + related enums.

Three model variants:
- ``Project`` — full domain entity, returned from the API
- ``ProjectCreate`` — POST body for creating a project
- ``ProjectUpdate`` — PATCH body, all fields optional
"""
from enum import StrEnum

from pydantic import Field

from atlas_core.models.base import (
    AtlasRequestModel,
    TimestampedModel,
)


class PrivacyLevel(StrEnum):
    CLOUD_OK = "cloud_ok"
    LOCAL_ONLY = "local_only"


class ProjectStatus(StrEnum):
    ACTIVE = "active"
    PAUSED = "paused"
    ARCHIVED = "archived"


class Project(TimestampedModel):
    """Full project entity. Returned from GET / POST / PATCH endpoints."""

    user_id: str
    name: str
    description: str | None = None
    status: ProjectStatus = ProjectStatus.ACTIVE
    privacy_level: PrivacyLevel = PrivacyLevel.CLOUD_OK
    default_model: str
    enabled_plugins: list[str] = Field(default_factory=list)


class ProjectCreate(AtlasRequestModel):
    """POST /projects body."""

    name: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    privacy_level: PrivacyLevel = PrivacyLevel.CLOUD_OK
    default_model: str = Field(min_length=1)
    enabled_plugins: list[str] = Field(default_factory=list)


class ProjectUpdate(AtlasRequestModel):
    """PATCH /projects/{id} body. All fields optional; provided fields overwrite."""

    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    status: ProjectStatus | None = None
    privacy_level: PrivacyLevel | None = None
    default_model: str | None = Field(default=None, min_length=1)
    enabled_plugins: list[str] | None = None
```

- [ ] **Step 3: Re-export from `models/__init__.py`**

Replace the entire contents of `packages/atlas-core/atlas_core/models/__init__.py` with:

```python
"""Pydantic models shared across ATLAS."""

from atlas_core.models.base import (
    AtlasModel,
    AtlasRequestModel,
    MutableAtlasModel,
    TimestampedModel,
)
from atlas_core.models.projects import (
    PrivacyLevel,
    Project,
    ProjectCreate,
    ProjectStatus,
    ProjectUpdate,
)

__all__ = [
    "AtlasModel",
    "AtlasRequestModel",
    "MutableAtlasModel",
    "PrivacyLevel",
    "Project",
    "ProjectCreate",
    "ProjectStatus",
    "ProjectUpdate",
    "TimestampedModel",
]
```

- [ ] **Step 4: Verify tests pass**

Run: `uv run pytest packages/atlas-core/atlas_core/tests/test_models_projects.py -v`
Expected: 9 passed.

- [ ] **Step 5: Commit**

```bash
git add packages/atlas-core/atlas_core/models/projects.py packages/atlas-core/atlas_core/models/__init__.py packages/atlas-core/atlas_core/tests/test_models_projects.py
git commit -m "feat(atlas-core): add Project Pydantic models and enums"
```

---

## Task 7: Add FastAPI dependencies (`get_settings`, `get_session`) and lifespan engine wiring

**Files:**
- Create: `apps/api/atlas_api/deps.py`
- Modify: `apps/api/atlas_api/main.py`

- [ ] **Step 1: Create `deps.py`**

`apps/api/atlas_api/deps.py`:
```python
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
```

- [ ] **Step 2: Modify `main.py` to construct engine in lifespan and stash on app.state**

Replace the entire contents of `apps/api/atlas_api/main.py` with:

```python
"""ATLAS FastAPI application entry point."""
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI

from atlas_api import __version__
from atlas_api.routers import projects as projects_router
from atlas_core.config import AtlasConfig
from atlas_core.db.session import create_engine_from_config, create_session_factory
from atlas_core.logging import configure_logging

config = AtlasConfig()
configure_logging(environment=config.environment, log_level=config.log_level)
log = structlog.get_logger("atlas.api")


@asynccontextmanager
async def lifespan(app: FastAPI):
    engine = create_engine_from_config(config)
    app.state.config = config
    app.state.engine = engine
    app.state.session_factory = create_session_factory(engine)
    log.info("api.startup", environment=config.environment, version=__version__)
    try:
        yield
    finally:
        log.info("api.shutdown")
        await engine.dispose()


app = FastAPI(
    title="ATLAS API",
    version=__version__,
    description="Personal AI consultant — Phase 1 Foundation",
    lifespan=lifespan,
)

app.include_router(projects_router.router, prefix="/api/v1")


@app.get("/health")
async def health() -> dict[str, str]:
    """Liveness probe. Returns 200 with environment + version metadata."""
    return {
        "status": "ok",
        "environment": config.environment,
        "version": __version__,
    }
```

This step references `atlas_api.routers.projects` which doesn't exist yet — Task 8 creates it. The import will fail until then. That's expected; the next task fixes it.

- [ ] **Step 3: Commit**

(Don't run tests yet — `main.py` is broken until the router lands. The next task includes a clean run.)

```bash
git add apps/api/atlas_api/deps.py apps/api/atlas_api/main.py
git commit -m "feat(atlas-api): add deps + lifespan engine wiring (router pending)"
```

---

## Task 8: Add Projects REST router

**Files:**
- Create: `apps/api/atlas_api/routers/__init__.py`
- Create: `apps/api/atlas_api/routers/projects.py`

- [ ] **Step 1: Create the routers package**

```bash
mkdir -p apps/api/atlas_api/routers
touch apps/api/atlas_api/routers/__init__.py
```

`apps/api/atlas_api/routers/__init__.py`: leave empty (just a package marker).

- [ ] **Step 2: Implement the projects router**

`apps/api/atlas_api/routers/projects.py`:
```python
"""REST endpoints for /projects.

Single-user-aware: every query filters by the configured user_id from
AtlasConfig. Plan 2 has no auth — the user_id is hardcoded in config.
"""
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from atlas_api.deps import get_session, get_settings
from atlas_core.config import AtlasConfig
from atlas_core.db.orm import ProjectORM
from atlas_core.models.projects import (
    Project,
    ProjectCreate,
    ProjectStatus,
    ProjectUpdate,
)

router = APIRouter(tags=["projects"])


def _to_pydantic(orm_obj: ProjectORM) -> Project:
    """Convert an ORM row to the Project Pydantic model.

    Enums are constructed explicitly because ``Project`` inherits ``strict=True``
    from ``AtlasModel`` and the ORM stores enum fields as their underlying string
    value — a raw string would not coerce under strict mode.
    """
    from atlas_core.models.projects import PrivacyLevel, ProjectStatus

    return Project(
        id=orm_obj.id,
        user_id=orm_obj.user_id,
        name=orm_obj.name,
        description=orm_obj.description,
        status=ProjectStatus(orm_obj.status),
        privacy_level=PrivacyLevel(orm_obj.privacy_level),
        default_model=orm_obj.default_model,
        enabled_plugins=list(orm_obj.enabled_plugins or []),
        created_at=orm_obj.created_at,
        updated_at=orm_obj.updated_at,
    )


@router.get("/projects", response_model=list[Project])
async def list_projects(
    session: AsyncSession = Depends(get_session),
    settings: AtlasConfig = Depends(get_settings),
) -> list[Project]:
    result = await session.execute(
        select(ProjectORM)
        .where(ProjectORM.user_id == settings.user_id)
        .order_by(ProjectORM.created_at.desc())
    )
    return [_to_pydantic(row) for row in result.scalars().all()]


@router.post(
    "/projects",
    response_model=Project,
    status_code=status.HTTP_201_CREATED,
)
async def create_project(
    payload: ProjectCreate,
    session: AsyncSession = Depends(get_session),
    settings: AtlasConfig = Depends(get_settings),
) -> Project:
    row = ProjectORM(
        user_id=settings.user_id,
        name=payload.name,
        description=payload.description,
        privacy_level=payload.privacy_level.value,
        default_model=payload.default_model,
        enabled_plugins=payload.enabled_plugins,
    )
    session.add(row)
    await session.flush()
    await session.refresh(row)
    return _to_pydantic(row)


@router.get("/projects/{project_id}", response_model=Project)
async def get_project(
    project_id: UUID,
    session: AsyncSession = Depends(get_session),
    settings: AtlasConfig = Depends(get_settings),
) -> Project:
    row = await session.get(ProjectORM, project_id)
    if row is None or row.user_id != settings.user_id:
        raise HTTPException(status_code=404, detail="Project not found")
    return _to_pydantic(row)


@router.patch("/projects/{project_id}", response_model=Project)
async def update_project(
    project_id: UUID,
    payload: ProjectUpdate,
    session: AsyncSession = Depends(get_session),
    settings: AtlasConfig = Depends(get_settings),
) -> Project:
    row = await session.get(ProjectORM, project_id)
    if row is None or row.user_id != settings.user_id:
        raise HTTPException(status_code=404, detail="Project not found")

    updates = payload.model_dump(exclude_unset=True)
    for field, value in updates.items():
        # Enum fields stored as their string value in Postgres
        if hasattr(value, "value"):
            value = value.value
        setattr(row, field, value)

    await session.flush()
    await session.refresh(row)
    return _to_pydantic(row)


@router.delete("/projects/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_project(
    project_id: UUID,
    session: AsyncSession = Depends(get_session),
    settings: AtlasConfig = Depends(get_settings),
) -> None:
    """Soft delete: set status='archived'. Hard delete is not exposed via REST."""
    row = await session.get(ProjectORM, project_id)
    if row is None or row.user_id != settings.user_id:
        raise HTTPException(status_code=404, detail="Project not found")
    row.status = ProjectStatus.ARCHIVED.value
    await session.flush()
```

- [ ] **Step 3: Verify the app imports cleanly**

Run:
```bash
ATLAS_DB__DATABASE_URL=postgresql://atlas:atlas@localhost:5432/atlas \
  uv run python -c "from atlas_api.main import app; print(app.title, [r.path for r in app.routes if r.path.startswith('/api')])"
```
Expected: prints `ATLAS API ['/api/v1/projects', '/api/v1/projects', '/api/v1/projects/{project_id}', '/api/v1/projects/{project_id}', '/api/v1/projects/{project_id}']` (5 route entries — one per HTTP verb).

- [ ] **Step 4: Verify the existing health tests still pass**

Run: `uv run pytest apps/api/atlas_api/tests/test_health.py -v`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add apps/api/atlas_api/routers/
git commit -m "feat(atlas-api): add projects REST router"
```

---

## Task 9: Extend `conftest.py` with test database fixtures

**Files:**
- Modify: `conftest.py`

- [ ] **Step 1: Replace `conftest.py` at the repo root**

`conftest.py`:
```python
"""Pytest configuration for ATLAS test suites.

Sets required environment variables BEFORE pytest collects test modules
(some modules construct AtlasConfig at import time). Also provides
session-scoped DB fixtures: ensures the `atlas_test` database exists,
runs Alembic migrations to head once per session, and yields per-test
async sessions wrapped in savepoints for isolation.
"""
import asyncio
import os
from collections.abc import AsyncIterator
from pathlib import Path

# Defaults BEFORE imports below — they trigger pydantic-settings.
os.environ.setdefault("ATLAS_DB__DATABASE_URL", "postgresql://atlas:atlas@localhost:5432/atlas_test")
os.environ.setdefault("ATLAS_ENVIRONMENT", "development")

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

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
    engine = create_async_engine(TEST_DB_URL, pool_pre_ping=True)
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
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


@pytest_asyncio.fixture
async def app_client(db_session):
    """FastAPI ASGI test client with `get_session` overridden to use db_session.

    Yields an `httpx.AsyncClient` bound to the app via ASGITransport.
    """
    from httpx import ASGITransport, AsyncClient

    from atlas_api.deps import get_session
    from atlas_api.main import app

    async def _override_session():
        yield db_session

    app.dependency_overrides[get_session] = _override_session
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            yield client
    finally:
        app.dependency_overrides.clear()
```

- [ ] **Step 2: Verify the test DB gets created and migrated on first run**

Run: `uv run pytest -v 2>&1 | tail -30`

Expected: All previously-passing tests still pass. The first run takes a few extra seconds while it creates `atlas_test` and runs the migration. On subsequent runs the migration is a no-op.

Sanity check the test DB has the table:
```bash
docker exec atlas-postgres psql -U atlas -d atlas_test -c "\dt"
```
Expected: shows `projects` and `alembic_version` tables.

- [ ] **Step 3: Commit**

```bash
git add conftest.py
git commit -m "test: add test database fixtures with savepoint isolation"
```

---

## Task 10: Add Projects router integration tests (TDD verification)

**Files:**
- Create: `apps/api/atlas_api/tests/test_projects_router.py`

- [ ] **Step 1: Write the tests**

`apps/api/atlas_api/tests/test_projects_router.py`:
```python
"""Integration tests for the /projects router against a real Postgres test DB."""
from uuid import uuid4

import pytest


async def test_list_projects_empty(app_client):
    response = await app_client.get("/api/v1/projects")
    assert response.status_code == 200
    assert response.json() == []


async def test_create_project_minimal(app_client):
    response = await app_client.post(
        "/api/v1/projects",
        json={"name": "First", "default_model": "claude-sonnet-4-6"},
    )
    assert response.status_code == 201
    body = response.json()
    assert body["name"] == "First"
    assert body["status"] == "active"
    assert body["privacy_level"] == "cloud_ok"
    assert body["enabled_plugins"] == []
    assert body["user_id"] == "matt"
    assert "id" in body
    assert "created_at" in body


async def test_create_project_with_all_fields(app_client):
    response = await app_client.post(
        "/api/v1/projects",
        json={
            "name": "Full",
            "description": "with everything",
            "privacy_level": "local_only",
            "default_model": "gemma-3-12b",
            "enabled_plugins": ["github", "gmail"],
        },
    )
    assert response.status_code == 201
    body = response.json()
    assert body["description"] == "with everything"
    assert body["privacy_level"] == "local_only"
    assert body["enabled_plugins"] == ["github", "gmail"]


async def test_create_then_list_returns_one(app_client):
    await app_client.post(
        "/api/v1/projects",
        json={"name": "Listed", "default_model": "x"},
    )
    response = await app_client.get("/api/v1/projects")
    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["name"] == "Listed"


async def test_create_rejects_empty_name(app_client):
    response = await app_client.post(
        "/api/v1/projects",
        json={"name": "", "default_model": "x"},
    )
    assert response.status_code == 422


async def test_create_rejects_unknown_privacy_level(app_client):
    response = await app_client.post(
        "/api/v1/projects",
        json={"name": "x", "default_model": "x", "privacy_level": "not_a_value"},
    )
    assert response.status_code == 422


async def test_get_project_by_id(app_client):
    created = (
        await app_client.post(
            "/api/v1/projects",
            json={"name": "Findable", "default_model": "x"},
        )
    ).json()
    response = await app_client.get(f"/api/v1/projects/{created['id']}")
    assert response.status_code == 200
    assert response.json()["id"] == created["id"]


async def test_get_project_returns_404_for_missing_id(app_client):
    response = await app_client.get(f"/api/v1/projects/{uuid4()}")
    assert response.status_code == 404


async def test_patch_project_updates_provided_fields_only(app_client):
    created = (
        await app_client.post(
            "/api/v1/projects",
            json={"name": "Original", "description": "keep me", "default_model": "x"},
        )
    ).json()

    response = await app_client.patch(
        f"/api/v1/projects/{created['id']}",
        json={"name": "Renamed"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["name"] == "Renamed"
    assert body["description"] == "keep me"  # unchanged


async def test_patch_project_changes_privacy_level(app_client):
    created = (
        await app_client.post(
            "/api/v1/projects",
            json={"name": "P", "default_model": "x"},
        )
    ).json()

    response = await app_client.patch(
        f"/api/v1/projects/{created['id']}",
        json={"privacy_level": "local_only"},
    )
    assert response.status_code == 200
    assert response.json()["privacy_level"] == "local_only"


async def test_patch_returns_404_for_missing_id(app_client):
    response = await app_client.patch(
        f"/api/v1/projects/{uuid4()}", json={"name": "x"}
    )
    assert response.status_code == 404


async def test_delete_project_soft_archives(app_client):
    created = (
        await app_client.post(
            "/api/v1/projects",
            json={"name": "Deletable", "default_model": "x"},
        )
    ).json()

    delete_response = await app_client.delete(f"/api/v1/projects/{created['id']}")
    assert delete_response.status_code == 204

    # Soft delete: row still exists with status='archived'
    get_response = await app_client.get(f"/api/v1/projects/{created['id']}")
    assert get_response.status_code == 200
    assert get_response.json()["status"] == "archived"


async def test_delete_returns_404_for_missing_id(app_client):
    response = await app_client.delete(f"/api/v1/projects/{uuid4()}")
    assert response.status_code == 404
```

- [ ] **Step 2: Run the tests**

Make sure docker-compose services are up: `docker-compose -f infra/docker-compose.yml ps` should show both healthy.

Run: `uv run pytest apps/api/atlas_api/tests/test_projects_router.py -v`
Expected: 13 passed.

If `test_create_then_list_returns_one` fails because list returns 0 or 2: the savepoint rollback is not isolating — verify `db_session` fixture and `app_client.dependency_overrides` were both wired correctly.

If any test gets `Connection refused` or `database "atlas_test" does not exist`: the autouse `setup_test_database` didn't run — verify `psycopg2-binary` is installed and Postgres is reachable.

- [ ] **Step 3: Commit**

```bash
git add apps/api/atlas_api/tests/test_projects_router.py
git commit -m "test(atlas-api): add Projects router integration tests"
```

---

## Task 11: Run the full test suite + ruff + smoke against live API

**Files:** none modified.

- [ ] **Step 1: Full clean test suite**

```bash
uv run pytest -v 2>&1 | tail -25
```

Expected: All tests pass. Approximate count after Plan 2:
- 24 from Plan 1 (still passing)
- 3 from `test_db_session.py` (Task 2)
- 3 from `test_db_orm.py` (Task 5)
- 9 from `test_models_projects.py` (Task 6)
- 13 from `test_projects_router.py` (Task 10)

**Total ≈ 52 tests passing.**

- [ ] **Step 2: Ruff**

```bash
uv run ruff check . && uv run ruff format --check .
```

Expected: All clean. If `ruff check` reports issues, run `ruff check --fix .`. If `ruff format --check` flags files, run `ruff format .` and review the diff before committing.

If you needed to apply autofixes:
```bash
git add -u
git commit -m "chore: ruff autofix and format"
```

- [ ] **Step 3: End-to-end smoke against the live API**

Start docker compose if not already running:
```bash
docker-compose -f infra/docker-compose.yml up -d
```

Apply migrations to the dev DB (separate from `atlas_test`):
```bash
ATLAS_DB__DATABASE_URL=postgresql://atlas:atlas@localhost:5432/atlas \
  uv run alembic upgrade head
```

Start the API in a separate terminal:
```bash
uv run uvicorn atlas_api.main:app --reload --host 0.0.0.0 --port 8000
```

Then in another shell, run the full CRUD path:

```bash
# Create
CREATE=$(curl -s -X POST http://localhost:8000/api/v1/projects \
  -H 'Content-Type: application/json' \
  -d '{"name":"Smoke Project","default_model":"claude-sonnet-4-6","description":"e2e check"}')
echo "Created: $CREATE"
ID=$(echo "$CREATE" | python -c "import sys,json; print(json.load(sys.stdin)['id'])")

# List
curl -s http://localhost:8000/api/v1/projects | python -m json.tool

# Get
curl -s "http://localhost:8000/api/v1/projects/$ID" | python -m json.tool

# Patch
curl -s -X PATCH "http://localhost:8000/api/v1/projects/$ID" \
  -H 'Content-Type: application/json' \
  -d '{"description":"updated","privacy_level":"local_only"}' | python -m json.tool

# Delete (soft)
curl -s -o /dev/null -w "Delete status: %{http_code}\n" -X DELETE "http://localhost:8000/api/v1/projects/$ID"

# Verify archived
curl -s "http://localhost:8000/api/v1/projects/$ID" | python -m json.tool
```

Expected: all 5 calls succeed; the final GET shows `"status": "archived"`. The PATCH returns the row with the new description, new privacy_level, and an `updated_at` strictly later than `created_at` (proves the trigger from Task 4 fires).

Stop the API (Ctrl-C in its terminal). Confirm the structlog `api.shutdown` line appears.

- [ ] **Step 4: Verify the trigger actually fired during the smoke run**

```bash
docker exec atlas-postgres psql -U atlas -d atlas -c \
  "SELECT name, created_at, updated_at, updated_at > created_at AS trigger_fired FROM projects;"
```

Expected: at least one row where `trigger_fired = t` (the patched smoke project). If `trigger_fired = f` for every row that was patched, the trigger isn't installed — investigate Task 4 step 4.

Cleanup the smoke row:
```bash
docker exec atlas-postgres psql -U atlas -d atlas -c "DELETE FROM projects WHERE name='Smoke Project';"
```

---

## Definition of Done for Plan 2

All of the following must be true:

1. `uv sync --all-packages` succeeds from a clean `.venv`.
2. `uv run pytest -v` shows ~52 tests passing, 0 failing, 0 errors. Test DB (`atlas_test`) is auto-created and migrated on first run.
3. `uv run ruff check .` and `ruff format --check .` both clean.
4. `alembic upgrade head` runs cleanly against a fresh DB and creates the `projects` table with the `set_updated_at` trigger installed.
5. `alembic downgrade base` undoes everything cleanly (drop trigger, drop index, drop table).
6. The 5 REST endpoints work end-to-end via curl against the live API:
   - `POST /api/v1/projects` returns 201 with full Project body
   - `GET /api/v1/projects` returns array, ordered newest first
   - `GET /api/v1/projects/{id}` returns 200 for existing, 404 for missing
   - `PATCH /api/v1/projects/{id}` updates and returns the row; `updated_at` advances (trigger fired)
   - `DELETE /api/v1/projects/{id}` returns 204 and the row's status flips to `archived`
7. Validation rejections work: empty name → 422, unknown privacy_level → 422.
8. `git log --oneline` shows clean commit history (one per task that produced files).

When all checks pass, this plan is complete. Plan 3 (LLM provider abstraction + chat WebSocket) builds on this foundation: it adds the `sessions`, `messages`, `model_usage` tables (their own migrations), the provider abstraction in `atlas-core`, and the WebSocket handler that uses `get_session` to persist chat turns.
