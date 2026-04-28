# syntax=docker/dockerfile:1.7
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS base

ENV UV_LINK_MODE=copy \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Workspace metadata first for layer cache.
COPY pyproject.toml uv.lock ./
COPY apps/api/pyproject.toml apps/api/
COPY packages/atlas-core/pyproject.toml packages/atlas-core/
COPY packages/atlas-knowledge/pyproject.toml packages/atlas-knowledge/

# Sync without dev deps; uv resolves the workspace as one unit.
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev

# Source after deps so changes don't bust the cache.
COPY apps/api/atlas_api apps/api/atlas_api
COPY packages/atlas-core/atlas_core packages/atlas-core/atlas_core
COPY packages/atlas-knowledge/atlas_knowledge packages/atlas-knowledge/atlas_knowledge

ENV PATH="/app/.venv/bin:$PATH"

EXPOSE 8000
CMD ["uvicorn", "atlas_api.main:app", "--host", "0.0.0.0", "--port", "8000"]
