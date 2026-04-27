# ATLAS — Adaptive Task & Learning Assistant System

Personal AI consultant dashboard. See `docs/atlas_design_document.md` for the full vision.

## Status

Phase 1 — Foundation. See `docs/superpowers/specs/` and `docs/superpowers/plans/`.

## Quick start

Prerequisites: Python 3.13, uv, Docker, Docker Compose.

```bash
# 1. Install Python deps (--all-packages installs every workspace member)
uv sync --all-packages

# 2. Configure environment
cp .env.example .env
# Edit .env — set ATLAS_LLM__ANTHROPIC_API_KEY at minimum

# 3. Start data layer (Postgres + Redis)
docker-compose -f infra/docker-compose.yml up -d

# 4. Start the API
uv run uvicorn atlas_api.main:app --reload --host 0.0.0.0 --port 8000

# 5. Verify
curl http://localhost:8000/health
# {"status": "ok", "environment": "development", "version": "0.1.0"}

# 6. Run tests
uv run pytest
```

## Repository structure

- `apps/api/` — FastAPI service (entry point: `atlas_api.main:app`)
- `packages/atlas-core/` — shared library: config, models, providers, prompts, agent
- `packages/atlas-knowledge/` — RAG: embeddings, vector store, ingestion, retrieval
- `infra/docker-compose.yml` — local dev data layer
- `docs/` — design documents and implementation plans
