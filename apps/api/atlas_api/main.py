"""ATLAS FastAPI application entry point."""

from contextlib import asynccontextmanager

import structlog
from atlas_core.config import AtlasConfig
from atlas_core.logging import configure_logging
from fastapi import FastAPI

from atlas_api import __version__

config = AtlasConfig()
configure_logging(environment=config.environment, log_level=config.log_level)
log = structlog.get_logger("atlas.api")


@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("api.startup", environment=config.environment, version=__version__)
    yield
    log.info("api.shutdown")


app = FastAPI(
    title="ATLAS API",
    version=__version__,
    description="Personal AI consultant — Phase 1 Foundation",
    lifespan=lifespan,
)


@app.get("/health")
async def health() -> dict[str, str]:
    """Liveness probe. Returns 200 with environment + version metadata."""
    return {
        "status": "ok",
        "environment": config.environment,
        "version": __version__,
    }
