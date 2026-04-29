# syntax=docker/dockerfile:1.7
FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Copy workspace (metadata, source, and lock).
COPY pyproject.toml uv.lock ./
COPY apps/api apps/api
COPY packages/atlas-core packages/atlas-core
COPY packages/atlas-knowledge packages/atlas-knowledge
COPY packages/atlas-graph packages/atlas-graph

# Create venv and sync all workspace packages.
RUN --mount=type=cache,target=/root/.cache/uv \
    uv venv && uv sync --all-packages

# Install Chromium for Playwright URL ingestion (~200 MB; spec §5.1 accepts the tax).
RUN uv run playwright install --with-deps chromium

ENV PATH="/app/.venv/bin:$PATH"

EXPOSE 8000
CMD ["uvicorn", "atlas_api.main:app", "--host", "0.0.0.0", "--port", "8000"]
