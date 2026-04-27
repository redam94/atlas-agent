"""Tests for atlas_core.models.projects."""
from datetime import datetime, timezone
from uuid import uuid4

import pytest
from pydantic import ValidationError

from atlas_core.models.projects import (
    PrivacyLevel,
    Project,
    ProjectCreate,
    ProjectStatus,
    ProjectUpdate,
)


def _make_project(**overrides) -> Project:
    base = {
        "id": uuid4(),
        "user_id": "matt",
        "name": "Test",
        "description": None,
        "status": ProjectStatus.ACTIVE,
        "privacy_level": PrivacyLevel.CLOUD_OK,
        "default_model": "claude-sonnet-4-6",
        "enabled_plugins": [],
        "created_at": datetime.now(timezone.utc),
        "updated_at": datetime.now(timezone.utc),
    }
    return Project(**{**base, **overrides})


def test_privacy_level_values():
    assert PrivacyLevel.CLOUD_OK == "cloud_ok"
    assert PrivacyLevel.LOCAL_ONLY == "local_only"


def test_project_status_values():
    assert ProjectStatus.ACTIVE == "active"
    assert ProjectStatus.PAUSED == "paused"
    assert ProjectStatus.ARCHIVED == "archived"


def test_project_round_trip_via_python_dict():
    """Roundtrip preserves equality when dump uses mode='python' (enums stay enums).

    Note: ``Project`` is strict — a JSON-mode dump (``mode='json'``) converts
    enums to strings, which strict mode cannot revalidate. The router's
    ``_to_pydantic`` helper handles this explicitly.
    """
    p = _make_project()
    dumped = p.model_dump(mode="python")
    restored = Project.model_validate(dumped)
    assert restored == p


def test_project_create_accepts_minimal_payload():
    pc = ProjectCreate.model_validate({"name": "Foo", "default_model": "claude-sonnet-4-6"})
    assert pc.name == "Foo"
    assert pc.privacy_level == PrivacyLevel.CLOUD_OK  # default
    assert pc.description is None
    assert pc.enabled_plugins == []


def test_project_create_coerces_string_privacy_level():
    """AtlasRequestModel base allows JSON string → enum coercion."""
    pc = ProjectCreate.model_validate(
        {"name": "Foo", "default_model": "x", "privacy_level": "local_only"}
    )
    assert pc.privacy_level is PrivacyLevel.LOCAL_ONLY


def test_project_create_rejects_unknown_privacy_level():
    with pytest.raises(ValidationError):
        ProjectCreate.model_validate(
            {"name": "Foo", "default_model": "x", "privacy_level": "unknown"}
        )


def test_project_create_requires_non_empty_name():
    with pytest.raises(ValidationError):
        ProjectCreate.model_validate({"name": "", "default_model": "x"})


def test_project_update_all_fields_optional():
    pu = ProjectUpdate.model_validate({})
    assert pu.name is None
    assert pu.description is None
    assert pu.status is None


def test_project_update_partial_payload():
    pu = ProjectUpdate.model_validate({"name": "Renamed", "status": "paused"})
    assert pu.name == "Renamed"
    assert pu.status is ProjectStatus.PAUSED
    assert pu.privacy_level is None
