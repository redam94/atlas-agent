"""Model registry + router.

The registry holds concrete provider instances keyed by model_id. The
router applies the Phase 1 simplified selection policy:

1. Explicit ``model_override`` from the request → that provider.
2. Project ``privacy_level == 'local_only'`` → first ``lmstudio`` provider.
3. Else → provider matching ``project.default_model``.
"""
from atlas_core.models.llm import ModelSpec
from atlas_core.models.projects import PrivacyLevel, Project
from atlas_core.providers.base import BaseModel


class ModelRegistry:
    """In-memory registry of provider instances."""

    def __init__(self) -> None:
        self._by_model_id: dict[str, BaseModel] = {}

    def register(self, provider: BaseModel) -> None:
        self._by_model_id[provider.spec.model_id] = provider

    def get(self, model_id: str) -> BaseModel | None:
        return self._by_model_id.get(model_id)

    def specs(self) -> list[ModelSpec]:
        return [p.spec for p in self._by_model_id.values()]

    def all(self) -> list[BaseModel]:
        return list(self._by_model_id.values())


class ModelRouter:
    """Phase 1 simplified routing — see module docstring."""

    def __init__(self, registry: ModelRegistry) -> None:
        self.registry = registry

    def select(
        self,
        project: Project,
        *,
        model_override: str | None = None,
    ) -> BaseModel:
        if model_override is not None:
            provider = self.registry.get(model_override)
            if provider is None:
                raise ValueError(f"Unknown model_override: {model_override}")
            return provider

        if project.privacy_level == PrivacyLevel.LOCAL_ONLY:
            for provider in self.registry.all():
                if provider.spec.provider == "lmstudio":
                    return provider
            raise ValueError("Project is local_only but no local (lmstudio) provider is registered")

        provider = self.registry.get(project.default_model)
        if provider is None:
            raise ValueError(
                f"Project default_model '{project.default_model}' not in registry"
            )
        return provider
