"""Tests for the ChunkSpec dataclass."""
from __future__ import annotations

import dataclasses
from uuid import uuid4

import pytest

from atlas_graph.protocols import ChunkSpec


def test_chunk_spec_round_trips_via_to_param():
    cid = uuid4()
    spec = ChunkSpec(id=cid, position=3, token_count=128, text_preview="Hello world.")
    param = spec.to_param()
    assert param == {
        "id": str(cid),
        "position": 3,
        "token_count": 128,
        "text_preview": "Hello world.",
    }


def test_chunk_spec_is_frozen():
    spec = ChunkSpec(id=uuid4(), position=0, token_count=10, text_preview="x")
    with pytest.raises(dataclasses.FrozenInstanceError):
        spec.position = 1  # type: ignore[misc]


def test_chunk_spec_to_param_uses_str_uuid():
    cid = uuid4()
    spec = ChunkSpec(id=cid, position=0, token_count=1, text_preview="")
    assert isinstance(spec.to_param()["id"], str)
    assert spec.to_param()["id"] == str(cid)
