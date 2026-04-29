"""PageRank — gds projection + write + drop.

The graph projection is named uniquely per call so concurrent ingests in the
same project do not collide on the named projection. The drop is invoked
unconditionally in the GraphStore method's ``finally`` block.
"""

PROJECT_CYPHER = (
    "CALL gds.graph.project.cypher("
    "  $name, "
    "  'MATCH (n) WHERE n.project_id = $pid RETURN id(n) AS id', "
    "  'MATCH (a)-[r]-(b) WHERE a.project_id = $pid AND b.project_id = $pid "
    "   RETURN id(a) AS source, id(b) AS target', "
    "  {parameters: {pid: $pid}}"
    ")"
)


WRITE_CYPHER = (
    "CALL gds.pageRank.write($name, {writeProperty: 'pagerank_global'})"
)


DROP_CYPHER = "CALL gds.graph.drop($name, false)"
