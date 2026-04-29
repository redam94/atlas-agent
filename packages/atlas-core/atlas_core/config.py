"""ATLAS runtime configuration.

Settings are loaded from environment variables (with optional ``.env`` file)
using ``pydantic-settings``. Nested groups are supported via the
``ATLAS_GROUP__FIELD`` env-var convention.
"""

from typing import Literal

from pydantic import AnyUrl, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class LLMConfig(BaseSettings):
    """Provider configuration."""

    model_config = SettingsConfigDict(env_prefix="ATLAS_LLM__", extra="ignore")

    anthropic_api_key: SecretStr | None = None
    lmstudio_base_url: AnyUrl = Field(default="http://100.91.155.118:1234/v1")
    default_model: str = "claude-sonnet-4-6"
    local_model: str | None = None  # auto-discovered from LM Studio if None


class DatabaseConfig(BaseSettings):
    """Postgres / Redis / Chroma configuration."""

    model_config = SettingsConfigDict(env_prefix="ATLAS_DB__", extra="ignore")

    database_url: SecretStr  # required
    redis_url: AnyUrl = Field(default="redis://localhost:6379")
    chroma_path: str = "./data/chroma"


class GraphConfig(BaseSettings):
    """Neo4j configuration."""

    model_config = SettingsConfigDict(env_prefix="ATLAS_GRAPH__", extra="ignore")

    uri: AnyUrl = Field(default="bolt://neo4j:7687")
    user: str = "neo4j"
    password: SecretStr  # required
    backfill_on_start: bool = False

    # Plan 3 knobs.
    ner_enabled: bool = True
    ner_max_entities_per_chunk: int = 20
    semantic_near_threshold: float = 0.85
    semantic_near_top_k: int = 50
    temporal_near_window_days: int = 7
    pagerank_enabled: bool = True


class RetrievalConfig(BaseSettings):
    """Plan 4 hybrid retrieval configuration."""

    model_config = SettingsConfigDict(env_prefix="ATLAS_RETRIEVAL__", extra="ignore")

    mode: Literal["vector", "hybrid"] = "hybrid"
    reranker_model: str = "cross-encoder/ms-marco-MiniLM-L-6-v2"


class AtlasConfig(BaseSettings):
    """Top-level config. Construct once at app startup."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="ATLAS_",
        env_nested_delimiter="__",
        case_sensitive=False,
        extra="ignore",
    )

    llm: LLMConfig = Field(default_factory=LLMConfig)
    db: DatabaseConfig = Field(default_factory=DatabaseConfig)
    graph: GraphConfig = Field(default_factory=GraphConfig)
    retrieval: RetrievalConfig = Field(default_factory=RetrievalConfig)
    environment: Literal["development", "production"] = "development"
    log_level: str = "INFO"
    user_id: str = "matt"
