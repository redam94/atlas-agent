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
