"""ATLAS FastAPI application entry point."""

import os
from contextlib import asynccontextmanager
from pathlib import Path

import atlas_graph
import httpx
import structlog
from atlas_core.config import AtlasConfig
from atlas_core.db.session import create_engine_from_config, create_session_factory, session_scope
from atlas_core.logging import configure_logging
from atlas_core.providers.anthropic import AnthropicProvider
from atlas_core.providers.lmstudio import LMStudioProvider
from atlas_core.providers.registry import ModelRegistry, ModelRouter
from atlas_graph import GraphStore, MigrationRunner, backfill_phase1
from atlas_graph.ingestion.ner import NerExtractor
from atlas_knowledge.embeddings import SentenceTransformersEmbedder
from atlas_knowledge.ingestion.service import IngestionService
from atlas_knowledge.retrieval.hybrid.hybrid import HybridRetriever
from atlas_knowledge.retrieval.hybrid.rerank import Reranker
from atlas_knowledge.retrieval.retriever import Retriever
from atlas_knowledge.vector.chroma import ChromaVectorStore
from atlas_plugins import CredentialStore, PluginRegistry
from atlas_plugins.credentials import SqlAlchemyBackend
from atlas_plugins.registry import REGISTERED_PLUGINS
from fastapi import FastAPI
from neo4j import AsyncGraphDatabase

from atlas_api import __version__
from atlas_api.routers import knowledge as knowledge_router
from atlas_api.routers import models as models_router
from atlas_api.routers import notes as notes_router
from atlas_api.routers import plugins as plugins_router
from atlas_api.routers import projects as projects_router
from atlas_api.routers import sessions as sessions_router
from atlas_api.routers._internal import discord as _internal_discord
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
    # Graph layer setup.
    graph_driver = AsyncGraphDatabase.driver(
        str(config.graph.uri),
        auth=(config.graph.user, config.graph.password.get_secret_value()),
    )
    migrations_dir = Path(atlas_graph.__file__).parent / "schema" / "migrations"
    applied = await MigrationRunner(graph_driver, migrations_dir).run_pending()
    log.info("graph.migrations.applied", ids=applied)
    # NER backend for Plan 3 entity extraction.
    ner_extractor = None
    http_client = None
    if config.graph.ner_enabled:
        http_client = httpx.AsyncClient()
        ner_extractor = NerExtractor(
            client=http_client,
            base_url=str(config.llm.lmstudio_base_url),
            max_entities=config.graph.ner_max_entities_per_chunk,
            model=config.llm.local_model or "ner",
        )
        app.state.http_client = http_client
    graph_store = GraphStore(graph_driver, ner_extractor=ner_extractor)
    app.state.graph_driver = graph_driver
    app.state.graph_store = graph_store
    embedder = SentenceTransformersEmbedder()
    vector_store = ChromaVectorStore(
        persist_dir=config.db.chroma_path,
        user_id=config.user_id,
    )
    app.state.embedder = embedder
    app.state.vector_store = vector_store
    app.state.ingestion_service = IngestionService(
        embedder=embedder,
        vector_store=vector_store,
        graph_writer=graph_store,
        semantic_near_threshold=config.graph.semantic_near_threshold,
        semantic_near_top_k=config.graph.semantic_near_top_k,
        temporal_near_window_days=config.graph.temporal_near_window_days,
        pagerank_enabled=config.graph.pagerank_enabled,
    )
    if config.retrieval.mode == "hybrid":
        reranker = Reranker(model_name=config.retrieval.reranker_model)
        app.state.reranker = reranker
        app.state.retriever = HybridRetriever(
            embedder=embedder,
            vector_store=vector_store,
            graph_store=graph_store,
            reranker=reranker,
            session_factory=app.state.session_factory,
        )
        log.info("retriever.mode", mode="hybrid")
    else:
        app.state.retriever = Retriever(embedder=embedder, vector_store=vector_store)
        log.info("retriever.mode", mode="vector")
    if config.graph.backfill_on_start:
        log.info("graph.backfill.start")
        async with session_scope(app.state.session_factory) as backfill_db:
            result = await backfill_phase1(
                db=backfill_db, graph=graph_store,
                progress_cb=lambda b, t: log.info(
                    "graph.backfill.progress", batch=b, total=t,
                ),
            )
        log.info(
            "graph.backfill.done",
            documents=result.documents, chunks=result.chunks, batches=result.batches,
        )
    # Plugin framework setup (Phase 3, Plan 1).
    master_key = os.getenv("ATLAS_PLUGINS__MASTER_KEY")
    backend = SqlAlchemyBackend(
        session_factory=lambda: session_scope(app.state.session_factory),
    )
    credential_store = CredentialStore(backend=backend, master_key=master_key)
    plugins = [PluginCls(credentials=credential_store) for PluginCls in REGISTERED_PLUGINS]
    plugin_registry = PluginRegistry(plugins)
    await plugin_registry.warm()
    app.state.credential_store = credential_store
    app.state.plugin_registry = plugin_registry
    log.info(
        "plugins.lifespan_ready",
        count=len(plugins),
        master_key_present=master_key is not None,
    )
    log.info(
        "api.startup",
        environment=config.environment,
        version=__version__,
        registered_models=[s.model_id for s in registry.specs()],
    )
    try:
        yield
    finally:
        if http_client is not None:
            await http_client.aclose()
        await graph_store.close()
        await engine.dispose()
        log.info("api.shutdown")


app = FastAPI(
    title="ATLAS API",
    version=__version__,
    description="Personal AI consultant — Phase 1 Foundation",
    lifespan=lifespan,
)

app.include_router(projects_router.router, prefix="/api/v1")
app.include_router(models_router.router, prefix="/api/v1")
app.include_router(ws_chat.router, prefix="/api/v1")
app.include_router(knowledge_router.router, prefix="/api/v1")
app.include_router(sessions_router.router, prefix="/api/v1")
app.include_router(notes_router.router, prefix="/api/v1")
app.include_router(plugins_router.router, prefix="/api/v1")
app.include_router(_internal_discord.router, prefix="/api/v1")


@app.get("/health")
async def health() -> dict[str, str]:
    """Liveness probe."""
    return {
        "status": "ok",
        "environment": config.environment,
        "version": __version__,
    }
