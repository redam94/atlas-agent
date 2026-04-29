CREATE CONSTRAINT entity_project_name_type IF NOT EXISTS
  FOR (e:Entity) REQUIRE (e.project_id, e.name, e.type) IS UNIQUE;
CREATE INDEX entity_project_id IF NOT EXISTS
  FOR (e:Entity) ON (e.project_id);
CREATE INDEX entity_type IF NOT EXISTS
  FOR (e:Entity) ON (e.type);
// One-shot fixup: backfills NULL Document.created_at with migration time so TEMPORAL_NEAR
// (Plan 3) can compute date deltas. Pre-Plan-3 docs will cluster as same-day; post-Plan-3
// ingests set the actual creation time via write_document_chunks.
MATCH (d:Document) WHERE d.created_at IS NULL SET d.created_at = datetime();
