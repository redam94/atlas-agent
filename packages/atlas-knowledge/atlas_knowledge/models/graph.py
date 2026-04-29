"""Knowledge-graph response models for the Plan 5 explorer endpoint."""

from __future__ import annotations

from typing import Any, Literal
from uuid import UUID

from atlas_core.models.base import AtlasModel
from pydantic import Field

NodeType = Literal["Document", "Chunk", "Entity"]
GraphMode = Literal["top_entities", "search", "expand"]


class GraphNode(AtlasModel):
    id: UUID
    type: NodeType
    label: str
    pagerank: float | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class GraphEdge(AtlasModel):
    id: str  # not a UUID — Neo4j relationships have integer ids; we stringify them
    source: UUID
    target: UUID
    type: str  # e.g. "HAS_CHUNK", "MENTIONS", "REFERENCES"


class GraphMeta(AtlasModel):
    mode: GraphMode
    truncated: bool = False
    hit_node_ids: list[UUID] = Field(default_factory=list)
    degraded_stages: list[str] = Field(default_factory=list)


class GraphResponse(AtlasModel):
    nodes: list[GraphNode]
    edges: list[GraphEdge]
    meta: GraphMeta


class EntitySuggestion(AtlasModel):
    """One row in the @-mention autocomplete dropdown."""
    id: UUID
    name: str
    entity_type: str | None = None
    pagerank: float = 0.0
