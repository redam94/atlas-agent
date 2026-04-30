export interface Entity {
  id: string;
  name: string;
  entity_type: string | null;
  pagerank: number;
}

export async function fetchEntities(
  projectId: string,
  prefix: string,
  limit = 10,
): Promise<Entity[]> {
  const params = new URLSearchParams({
    project_id: projectId,
    prefix,
    limit: String(limit),
  });
  const r = await fetch(`/api/v1/knowledge/entities?${params}`);
  if (!r.ok) {
    if (r.status === 503) return [];  // graph offline → no suggestions
    throw new Error(await r.text());
  }
  return r.json();
}
