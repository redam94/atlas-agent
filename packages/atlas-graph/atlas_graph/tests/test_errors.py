"""Tests for GraphUnavailableError."""
from __future__ import annotations

from atlas_graph.errors import GraphUnavailableError


def test_graph_unavailable_is_runtime_error():
    err = GraphUnavailableError("neo4j unavailable: connection refused")
    assert isinstance(err, RuntimeError)
    assert str(err) == "neo4j unavailable: connection refused"


def test_graph_unavailable_chains_cause():
    cause = ConnectionRefusedError("nope")
    try:
        raise GraphUnavailableError("wrapped") from cause
    except GraphUnavailableError as e:
        assert e.__cause__ is cause
