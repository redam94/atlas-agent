"""Tests for ModelRegistry + ModelRouter."""

from uuid import uuid4

import pytest

from atlas_core.models.llm import ModelSpec
from atlas_core.models.projects import PrivacyLevel, Project, ProjectStatus
from atlas_core.providers import FakeProvider
from atlas_core.providers.registry import ModelRegistry, ModelRouter


def _project(
    privacy: PrivacyLevel = PrivacyLevel.CLOUD_OK, default_model: str = "claude-sonnet-4-6"
) -> Project:
    from datetime import UTC, datetime

    return Project(
        id=uuid4(),
        user_id="matt",
        name="P",
        description=None,
        status=ProjectStatus.ACTIVE,
        privacy_level=privacy,
        default_model=default_model,
        enabled_plugins=[],
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )


def test_model_registry_register_and_get():
    reg = ModelRegistry()
    fp = FakeProvider(model_id="fake-1")
    reg.register(fp)
    assert reg.get("fake-1") is fp


def test_model_registry_specs_returns_all():
    reg = ModelRegistry()
    reg.register(FakeProvider(model_id="a"))
    reg.register(FakeProvider(model_id="b"))
    specs = reg.specs()
    ids = {s.model_id for s in specs}
    assert ids == {"a", "b"}


def test_model_router_uses_explicit_override():
    reg = ModelRegistry()
    cloud = FakeProvider(model_id="cloud-1")
    local = FakeProvider(model_id="local-1")
    reg.register(cloud)
    reg.register(local)
    router = ModelRouter(reg)

    # Manually set fake provider provider name to mimic real ones for the policy
    cloud.spec = ModelSpec(
        provider="anthropic", model_id="cloud-1", context_window=1, supports_tools=False
    )
    local.spec = ModelSpec(
        provider="lmstudio", model_id="local-1", context_window=1, supports_tools=False
    )

    chosen = router.select(_project(), model_override="local-1")
    assert chosen is local


def test_model_router_local_only_picks_lmstudio():
    reg = ModelRegistry()
    cloud = FakeProvider(model_id="cloud-1")
    local = FakeProvider(model_id="local-1")
    cloud.spec = ModelSpec(
        provider="anthropic", model_id="cloud-1", context_window=1, supports_tools=False
    )
    local.spec = ModelSpec(
        provider="lmstudio", model_id="local-1", context_window=1, supports_tools=False
    )
    reg.register(cloud)
    reg.register(local)

    router = ModelRouter(reg)
    chosen = router.select(_project(privacy=PrivacyLevel.LOCAL_ONLY))
    assert chosen.spec.provider == "lmstudio"


def test_model_router_falls_back_to_default_model():
    reg = ModelRegistry()
    fp = FakeProvider(model_id="claude-sonnet-4-6")
    fp.spec = ModelSpec(
        provider="anthropic", model_id="claude-sonnet-4-6", context_window=1, supports_tools=False
    )
    reg.register(fp)

    router = ModelRouter(reg)
    chosen = router.select(_project())  # cloud_ok, default model
    assert chosen.spec.model_id == "claude-sonnet-4-6"


def test_model_router_raises_if_local_only_and_no_local_provider():
    reg = ModelRegistry()
    cloud = FakeProvider(model_id="cloud-1")
    cloud.spec = ModelSpec(
        provider="anthropic", model_id="cloud-1", context_window=1, supports_tools=False
    )
    reg.register(cloud)

    router = ModelRouter(reg)
    with pytest.raises(ValueError, match="local"):
        router.select(_project(privacy=PrivacyLevel.LOCAL_ONLY))
