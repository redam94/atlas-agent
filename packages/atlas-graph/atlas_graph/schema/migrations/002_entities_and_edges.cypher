CREATE CONSTRAINT entity_project_name_type IF NOT EXISTS
  FOR (e:Entity) REQUIRE (e.project_id, e.name, e.type) IS UNIQUE;
CREATE INDEX entity_project_id IF NOT EXISTS
  FOR (e:Entity) ON (e.project_id);
CREATE INDEX entity_type IF NOT EXISTS
  FOR (e:Entity) ON (e.type);
MATCH (d:Document) WHERE d.created_at IS NULL SET d.created_at = datetime();
