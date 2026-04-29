export type NodeType = "Document" | "Chunk" | "Entity";
export type GraphMode = "top_entities" | "search" | "expand";

export interface GraphNode {
  id: string;
  type: NodeType;
  label: string;
  pagerank: number | null;
  metadata: Record<string, unknown>;
}

export interface GraphEdge {
  id: string;
  source: string;
  target: string;
  type: string;
}

export interface GraphMeta {
  mode: GraphMode;
  truncated: boolean;
  hit_node_ids: string[];
  degraded_stages: string[];
}

export interface GraphResponse {
  nodes: GraphNode[];
  edges: GraphEdge[];
  meta: GraphMeta;
}

export interface FetchKnowledgeGraphArgs {
  projectId: string;
  q?: string;
  seedNodeIds?: string[];
  seedChunkIds?: string[];
  nodeTypes?: NodeType[];
  limit?: number;
}

export class KnowledgeGraphError extends Error {
  constructor(
    message: string,
    public readonly status: number,
    public readonly degraded?: boolean,
  ) {
    super(message);
    this.name = "KnowledgeGraphError";
  }
}

export async function fetchKnowledgeGraph(
  args: FetchKnowledgeGraphArgs,
  signal?: AbortSignal,
): Promise<GraphResponse> {
  const params = new URLSearchParams({ project_id: args.projectId });
  if (args.q) params.set("q", args.q);
  if (args.seedNodeIds?.length) params.set("seed_node_ids", args.seedNodeIds.join(","));
  if (args.seedChunkIds?.length) params.set("seed_chunk_ids", args.seedChunkIds.join(","));
  if (args.nodeTypes?.length) params.set("node_types", args.nodeTypes.join(","));
  if (args.limit !== undefined) params.set("limit", String(args.limit));

  const resp = await fetch(`/api/v1/knowledge/graph?${params}`, { signal });
  if (resp.status === 503) {
    throw new KnowledgeGraphError("graph_unavailable", 503, true);
  }
  if (!resp.ok) {
    const detail = await resp.text();
    throw new KnowledgeGraphError(detail || resp.statusText, resp.status);
  }
  return resp.json();
}
