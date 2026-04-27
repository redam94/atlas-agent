"""Compose a system prompt for a chat turn from modular template sections."""
from datetime import UTC, datetime

from atlas_core.models.projects import Project
from atlas_core.prompts.registry import PromptRegistry


class SystemPromptBuilder:
    """Pick the right Jinja sections based on request context, render, join."""

    def __init__(self, registry: PromptRegistry) -> None:
        self.registry = registry

    def build(
        self,
        project: Project,
        *,
        user_name: str | None = None,
        current_date: str | None = None,
    ) -> str:
        sections = ["system/base", "system/project_context", "system/output_format"]
        variables = {
            "agent_name": "ATLAS",
            "current_date": current_date or datetime.now(UTC).strftime("%Y-%m-%d"),
            "user_name": user_name,
            "project_name": project.name,
            "project_description": project.description,
            "privacy_level": str(project.privacy_level),
        }
        return self.registry.compose_system_prompt(sections, **variables)
