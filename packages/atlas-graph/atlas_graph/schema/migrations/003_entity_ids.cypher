// Plan 5 — Entity.id is needed by the Knowledge Explorer's UI subgraph fetches
// (fetch_top_entities, fetch_subgraph_by_seeds), which match nodes by id property.
// MERGE_ENTITIES_CYPHER now ON CREATE SETs id. This migration backfills existing
// Entity nodes that pre-date the change and adds a lookup index on id.
MATCH (e:Entity) WHERE e.id IS NULL SET e.id = toString(randomUUID());
CREATE INDEX entity_id IF NOT EXISTS
  FOR (e:Entity) ON (e.id);
