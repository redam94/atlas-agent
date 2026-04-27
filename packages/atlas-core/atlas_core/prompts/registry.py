"""Jinja2-based prompt template registry.

Templates live under ``atlas_core/prompts/templates/``. The default
``prompt_registry`` singleton is instantiated at import time and used
throughout the app; tests can construct fresh ``PromptRegistry()``
instances if they need isolation.
"""

from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined, TemplateNotFound

TEMPLATES_DIR = Path(__file__).parent / "templates"


class PromptRegistry:
    """Wraps a Jinja2 ``Environment`` and exposes simple render helpers."""

    def __init__(self, templates_dir: Path | None = None) -> None:
        self._templates_dir = templates_dir or TEMPLATES_DIR
        self._env = Environment(
            loader=FileSystemLoader(str(self._templates_dir)),
            undefined=StrictUndefined,
            trim_blocks=True,
            lstrip_blocks=True,
            autoescape=False,  # prompts are plaintext, not HTML
        )

    def get(self, template_path: str, **variables: object) -> str:
        """Render the named template with the given variables.

        ``template_path`` is relative to the templates root, without ``.j2``,
        e.g. ``"system/base"``.
        """
        template = self._env.get_template(f"{template_path}.j2")
        return template.render(**variables)

    def compose_system_prompt(self, sections: list[str], **variables: object) -> str:
        """Render multiple sections and join with double newlines."""
        return "\n\n".join(self.get(s, **variables) for s in sections)

    def template_exists(self, template_path: str) -> bool:
        try:
            self._env.get_template(f"{template_path}.j2")
            return True
        except TemplateNotFound:
            return False

    def reload(self) -> None:
        """Drop Jinja's compiled-template cache so on-disk edits take effect."""
        self._env.cache = {} if self._env.cache is not None else self._env.cache


# Module-level singleton — most callers use this.
prompt_registry = PromptRegistry()
