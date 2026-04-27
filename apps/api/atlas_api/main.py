"""ATLAS FastAPI application entry point."""

from contextlib import asynccontextmanager

import structlog
from atlas_core.config import AtlasConfig
from atlas_core.db.session import create_engine_from_config, create_session_factory
from atlas_core.logging import configure_logging
from atlas_core.providers.anthropic import AnthropicProvider
from atlas_core.providers.lmstudio import LMStudioProvider
from atlas_core.providers.registry import ModelRegistry, ModelRouter
from fastapi import FastAPI

from atlas_api import __version__
from atlas_api.routers import models as models_router
from atlas_api.routers import projects as projects_router
from atlas_api.ws import chat as ws_chat

config = AtlasConfig()
configure_logging(environment=config.environment, log_level=config.log_level)
log = structlog.get_logger("atlas.api")


def _build_registry(cfg: AtlasConfig) -> ModelRegistry:
    """Construct the model registry from config — Anthropic + LM Studio."""
    reg = ModelRegistry()

    if cfg.llm.anthropic_api_key is not None:
        for model_id in (
            "claude-opus-4-7",
            "claude-sonnet-4-6",
            "claude-haiku-4-5-20251001",
        ):
            reg.register(
                AnthropicProvider(
                    api_key=cfg.llm.anthropic_api_key.get_secret_value(),
                    model_id=model_id,
                )
            )

    if cfg.llm.local_model:
        reg.register(
            LMStudioProvider(
                base_url=str(cfg.llm.lmstudio_base_url),
                model_id=cfg.llm.local_model,
            )
        )
    else:
        log.warning(
            "lmstudio.skipped_registration",
            reason="ATLAS_LLM__LOCAL_MODEL not set; set it to the loaded LM Studio model",
        )

    return reg


@asynccontextmanager
async def lifespan(app: FastAPI):
    engine = create_engine_from_config(config)
    registry = _build_registry(config)
    app.state.config = config
    app.state.engine = engine
    app.state.session_factory = create_session_factory(engine)
    app.state.model_registry = registry
    app.state.model_router = ModelRouter(registry)
    log.info(
        "api.startup",
        environment=config.environment,
        version=__version__,
        registered_models=[s.model_id for s in registry.specs()],
    )
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
app.include_router(models_router.router, prefix="/api/v1")
app.include_router(ws_chat.router, prefix="/api/v1")


@app.get("/health")
async def health() -> dict[str, str]:
    """Liveness probe."""
    return {
        "status": "ok",
        "environment": config.environment,
        "version": __version__,
    }
