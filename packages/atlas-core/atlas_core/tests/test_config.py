"""Tests for atlas_core.config."""

import pytest
from pydantic import SecretStr, ValidationError

from atlas_core.config import AtlasConfig, DatabaseConfig, LLMConfig


def test_llm_config_defaults():
    cfg = LLMConfig()
    assert cfg.anthropic_api_key is None
    assert str(cfg.lmstudio_base_url).rstrip("/") == "http://100.91.155.118:1234/v1"
    assert cfg.default_model == "claude-sonnet-4-6"
    assert cfg.local_model is None


def test_database_config_requires_database_url(monkeypatch):
    monkeypatch.delenv("ATLAS_DB__DATABASE_URL", raising=False)
    with pytest.raises(ValidationError):
        DatabaseConfig()


def test_atlas_config_loads_from_env(monkeypatch):
    monkeypatch.setenv("ATLAS_LLM__ANTHROPIC_API_KEY", "sk-ant-test")
    monkeypatch.setenv("ATLAS_DB__DATABASE_URL", "postgresql://x:y@localhost/z")
    monkeypatch.setenv("ATLAS_USER_ID", "alice")
    monkeypatch.setenv("ATLAS_LOG_LEVEL", "DEBUG")

    cfg = AtlasConfig()

    assert isinstance(cfg.llm.anthropic_api_key, SecretStr)
    assert cfg.llm.anthropic_api_key.get_secret_value() == "sk-ant-test"
    assert cfg.user_id == "alice"
    assert cfg.log_level == "DEBUG"
    assert cfg.environment == "development"


def test_atlas_config_environment_must_be_known(monkeypatch):
    monkeypatch.setenv("ATLAS_DB__DATABASE_URL", "postgresql://x:y@localhost/z")
    monkeypatch.setenv("ATLAS_ENVIRONMENT", "staging")  # not in literal
    with pytest.raises(ValidationError):
        AtlasConfig()


def test_graph_config_reads_env(monkeypatch):
    from atlas_core.config import GraphConfig

    monkeypatch.setenv("ATLAS_GRAPH__URI", "bolt://example.local:7687")
    monkeypatch.setenv("ATLAS_GRAPH__USER", "neo4j")
    monkeypatch.setenv("ATLAS_GRAPH__PASSWORD", "s3cret")
    monkeypatch.setenv("ATLAS_GRAPH__BACKFILL_ON_START", "true")
    cfg = GraphConfig()
    assert str(cfg.uri) == "bolt://example.local:7687"
    assert cfg.user == "neo4j"
    assert isinstance(cfg.password, SecretStr)
    assert cfg.password.get_secret_value() == "s3cret"
    assert cfg.backfill_on_start is True


def test_graph_config_defaults(monkeypatch):
    from atlas_core.config import GraphConfig

    # Only password is required.
    monkeypatch.setenv("ATLAS_GRAPH__PASSWORD", "p")
    # Clear other ATLAS_GRAPH__ env vars that might persist from prior tests.
    for k in ("ATLAS_GRAPH__URI", "ATLAS_GRAPH__USER", "ATLAS_GRAPH__BACKFILL_ON_START"):
        monkeypatch.delenv(k, raising=False)
    cfg = GraphConfig()
    assert str(cfg.uri).startswith("bolt://")
    assert cfg.user == "neo4j"
    assert cfg.backfill_on_start is False


def test_graph_config_requires_password(monkeypatch):
    from atlas_core.config import GraphConfig

    monkeypatch.delenv("ATLAS_GRAPH__PASSWORD", raising=False)
    with pytest.raises(ValidationError):
        GraphConfig()


def test_atlas_config_mounts_graph_subconfig(monkeypatch):
    from atlas_core.config import GraphConfig

    monkeypatch.setenv("ATLAS_GRAPH__PASSWORD", "p")
    monkeypatch.setenv("ATLAS_DB__DATABASE_URL", "postgresql://x:y@localhost/z")
    cfg = AtlasConfig()
    assert isinstance(cfg.graph, GraphConfig)


def test_graph_config_plan3_defaults(monkeypatch):
    """Plan 3 — NER + edge builder + PageRank knobs default to sensible values."""
    monkeypatch.setenv("ATLAS_DB__DATABASE_URL", "postgresql://x")
    monkeypatch.setenv("ATLAS_GRAPH__PASSWORD", "pw")
    cfg = AtlasConfig()
    assert cfg.graph.ner_enabled is True
    assert cfg.graph.ner_max_entities_per_chunk == 20
    assert cfg.graph.semantic_near_threshold == 0.85
    assert cfg.graph.semantic_near_top_k == 50
    assert cfg.graph.temporal_near_window_days == 7
    assert cfg.graph.pagerank_enabled is True


def test_graph_config_plan3_env_override(monkeypatch):
    """All Plan 3 knobs are overridable via ATLAS_GRAPH__* env vars."""
    monkeypatch.setenv("ATLAS_DB__DATABASE_URL", "postgresql://x")
    monkeypatch.setenv("ATLAS_GRAPH__PASSWORD", "pw")
    monkeypatch.setenv("ATLAS_GRAPH__NER_ENABLED", "false")
    monkeypatch.setenv("ATLAS_GRAPH__NER_MAX_ENTITIES_PER_CHUNK", "5")
    monkeypatch.setenv("ATLAS_GRAPH__SEMANTIC_NEAR_THRESHOLD", "0.9")
    monkeypatch.setenv("ATLAS_GRAPH__SEMANTIC_NEAR_TOP_K", "10")
    monkeypatch.setenv("ATLAS_GRAPH__TEMPORAL_NEAR_WINDOW_DAYS", "3")
    monkeypatch.setenv("ATLAS_GRAPH__PAGERANK_ENABLED", "false")
    cfg = AtlasConfig()
    assert cfg.graph.ner_enabled is False
    assert cfg.graph.ner_max_entities_per_chunk == 5
    assert cfg.graph.semantic_near_threshold == 0.9
    assert cfg.graph.semantic_near_top_k == 10
    assert cfg.graph.temporal_near_window_days == 3
    assert cfg.graph.pagerank_enabled is False
