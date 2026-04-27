"""Tests for the PromptRegistry."""
import pytest
from jinja2 import UndefinedError

from atlas_core.prompts import PromptRegistry, prompt_registry


def test_registry_renders_known_template():
    out = prompt_registry.get(
        "system/base",
        agent_name="ATLAS",
        current_date="2026-04-27",
    )
    assert "ATLAS" in out
    assert "2026-04-27" in out


def test_registry_strict_undefined_raises_on_missing_var():
    with pytest.raises(UndefinedError):
        prompt_registry.get("system/base")  # missing current_date


def test_registry_compose_joins_sections():
    out = prompt_registry.compose_system_prompt(
        sections=["system/base", "system/output_format"],
        agent_name="ATLAS",
        current_date="2026-04-27",
    )
    assert "ATLAS" in out
    assert "Markdown" in out
    # Sections joined by double newline
    assert "\n\n" in out


def test_registry_template_exists():
    assert prompt_registry.template_exists("system/base")
    assert not prompt_registry.template_exists("system/does_not_exist")


def test_registry_reload_clears_cache():
    """reload() must not raise; subsequent renders still work."""
    prompt_registry.reload()
    out = prompt_registry.get(
        "system/base",
        agent_name="X",
        current_date="2026-04-27",
    )
    assert "X" in out


def test_new_registry_can_be_constructed():
    reg = PromptRegistry()
    out = reg.get("system/output_format")  # template with no required vars
    assert "Markdown" in out
