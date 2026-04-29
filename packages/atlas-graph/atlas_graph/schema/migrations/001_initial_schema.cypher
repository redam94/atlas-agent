CREATE CONSTRAINT project_id_unique IF NOT EXISTS
  FOR (p:Project) REQUIRE p.id IS UNIQUE;
CREATE CONSTRAINT document_id_unique IF NOT EXISTS
  FOR (d:Document) REQUIRE d.id IS UNIQUE;
CREATE CONSTRAINT chunk_id_unique IF NOT EXISTS
  FOR (c:Chunk) REQUIRE c.id IS UNIQUE;
CREATE INDEX chunk_project_id IF NOT EXISTS FOR (c:Chunk) ON (c.project_id);
CREATE INDEX document_project_id IF NOT EXISTS FOR (d:Document) ON (d.project_id);
