"""Tests for atlas_core.models.base."""

from datetime import datetime
from enum import StrEnum
from uuid import UUID

import pytest
from pydantic import ValidationError

from atlas_core.models.base import (
    AtlasModel,
    AtlasRequestModel,
    MutableAtlasModel,
    TimestampedModel,
)


class _Sample(AtlasModel):
    name: str
    count: int


class _MutableSample(MutableAtlasModel):
    value: int


class _TimedSample(TimestampedModel):
    label: str


def test_atlas_model_is_strict_no_int_to_str_coercion():
    with pytest.raises(ValidationError):
        _Sample(name=123, count=1)


def test_atlas_model_is_strict_no_str_to_int_coercion():
    with pytest.raises(ValidationError):
        _Sample(name="hello", count="1")


def test_atlas_model_is_frozen():
    instance = _Sample(name="hello", count=1)
    with pytest.raises(ValidationError):
        instance.name = "changed"


def test_atlas_model_copy_with_update_works():
    instance = _Sample(name="hello", count=1)
    updated = instance.model_copy(update={"name": "world"})
    assert updated.name == "world"
    assert instance.name == "hello"  # original unchanged


def test_mutable_atlas_model_allows_assignment():
    instance = _MutableSample(value=1)
    instance.value = 2
    assert instance.value == 2


def test_mutable_atlas_model_validates_assignment():
    instance = _MutableSample(value=1)
    with pytest.raises(ValidationError):
        instance.value = "not an int"


def test_timestamped_model_provides_id_and_timestamps():
    instance = _TimedSample(label="x")
    assert isinstance(instance.id, UUID)
    assert isinstance(instance.created_at, datetime)
    assert isinstance(instance.updated_at, datetime)


def test_timestamped_model_id_is_unique_per_instance():
    a = _TimedSample(label="a")
    b = _TimedSample(label="b")
    assert a.id != b.id


class _Status(StrEnum):
    ACTIVE = "active"
    PAUSED = "paused"


class _WithEnum(AtlasModel):
    status: _Status


def test_enum_field_keeps_enum_member_after_init():
    """Without use_enum_values=True, enum fields preserve the enum type."""
    instance = _WithEnum(status=_Status.ACTIVE)
    assert instance.status is _Status.ACTIVE
    assert isinstance(instance.status, _Status)


def test_mutable_atlas_model_is_subclass_of_atlas_model():
    """Inheritance contract: isinstance(MutableAtlasModel(), AtlasModel) is True."""
    instance = _MutableSample(value=1)
    assert isinstance(instance, AtlasModel)


class _RequestSample(AtlasRequestModel):
    name: str
    status: _Status


def test_atlas_request_model_coerces_string_to_enum():
    """AtlasRequestModel accepts string values for enum fields (FastAPI path)."""
    instance = _RequestSample.model_validate({"name": "x", "status": "active"})
    assert instance.status is _Status.ACTIVE


def test_atlas_request_model_is_frozen():
    instance = _RequestSample(name="x", status=_Status.ACTIVE)
    with pytest.raises(ValidationError):
        instance.name = "y"


def test_atlas_request_model_via_fastapi_post():
    """Round-trip through FastAPI proves AtlasRequestModel + StrEnum works on real requests."""
    import asyncio

    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient

    app = FastAPI()

    @app.post("/test")
    async def endpoint(payload: _RequestSample) -> dict:
        return {"name": payload.name, "status": payload.status.value}

    async def run() -> tuple[int, dict]:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            r = await c.post("/test", json={"name": "x", "status": "active"})
            return r.status_code, r.json()

    status_code, body = asyncio.run(run())
    assert status_code == 200
    assert body == {"name": "x", "status": "active"}
