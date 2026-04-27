"""Tests for SystemPromptBuilder."""
from datetime import UTC, datetime
from uuid import uuid4

from atlas_core.models.projects import PrivacyLevel, Project, ProjectStatus
from atlas_core.prompts.builder import SystemPromptBuilder
from atlas_core.prompts.registry import prompt_registry


def _project(name: str = "TestProject") -> Project:
    return Project(
        id=uuid4(),
        user_id="matt",
        name=name,
        description="A description",
        status=ProjectStatus.ACTIVE,
        privacy_level=PrivacyLevel.CLOUD_OK,
        default_model="claude-sonnet-4-6",
        enabled_plugins=[],
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )


def test_build_includes_project_name_and_date():
    builder = SystemPromptBuilder(prompt_registry)
    out = builder.build(_project(name="MMM Project"))
    assert "MMM Project" in out
    assert "ATLAS" in out
    assert "Markdown" in out  # output_format section


def test_build_respects_user_name_override():
    builder = SystemPromptBuilder(prompt_registry)
    out = builder.build(_project(), user_name="Matt")
    assert "Matt" in out


def test_build_includes_privacy_level():
    builder = SystemPromptBuilder(prompt_registry)
    out = builder.build(_project())
    assert "cloud_ok" in out
