"""Entity-and-REFERENCES Cypher helpers shared by GraphStore.write_entities."""
from __future__ import annotations

from uuid import UUID

from atlas_graph.ingestion.ner import Entity


def to_entity_param(project_id: UUID, e: Entity) -> dict:
    return {"project_id": str(project_id), "name": e.name, "type": e.type}


def to_reference_param(project_id: UUID, chunk_id: UUID, e: Entity) -> dict:
    return {
        "project_id": str(project_id),
        "chunk_id": str(chunk_id),
        "name": e.name,
        "type": e.type,
    }


MERGE_ENTITIES_CYPHER = (
    "UNWIND $entities AS row "
    "MERGE (e:Entity {project_id: row.project_id, name: row.name, type: row.type})"
)


MERGE_REFERENCES_CYPHER = (
    "UNWIND $references AS ref "
    "MATCH (c:Chunk {id: ref.chunk_id}), "
    "      (e:Entity {project_id: ref.project_id, name: ref.name, type: ref.type}) "
    "MERGE (c)-[:REFERENCES]->(e)"
)


def flatten(
    project_id: UUID,
    chunk_entities: dict[UUID, list[Entity]],
) -> tuple[list[dict], list[dict]]:
    """Return (entity_params, reference_params) deduped by entity identity."""
    seen: set[tuple[str, str]] = set()
    entities: list[dict] = []
    references: list[dict] = []
    for chunk_id, ents in chunk_entities.items():
        for e in ents:
            key = (e.name, e.type)
            if key not in seen:
                seen.add(key)
                entities.append(to_entity_param(project_id, e))
            references.append(to_reference_param(project_id, chunk_id, e))
    return entities, references
