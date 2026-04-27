"""Tests for atlas_core.config."""
import pytest
from pydantic import SecretStr

from atlas_core.config import AtlasConfig, DatabaseConfig, LLMConfig


def test_llm_config_defaults():
    cfg = LLMConfig()
    assert cfg.anthropic_api_key is None
    assert str(cfg.lmstudio_base_url).rstrip("/") == "http://100.91.155.118:1234/v1"
    assert cfg.default_model == "claude-sonnet-4-6"
    assert cfg.local_model is None


def test_database_config_requires_database_url(monkeypatch):
    monkeypatch.delenv("ATLAS_DB__DATABASE_URL", raising=False)
    with pytest.raises(Exception):
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
    with pytest.raises(Exception):
        AtlasConfig()
