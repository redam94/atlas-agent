import { create } from "zustand";
import type {
  GraphEdge,
  GraphMode,
  GraphNode,
  GraphResponse,
  NodeType,
} from "@/lib/api/knowledge-graph";

interface ExplorerState {
  nodes: GraphNode[];
  edges: GraphEdge[];
  hitNodeIds: Set<string>;
  selectedNodeId: string | null;
  visibleTypes: Set<NodeType>;
  query: string;
  mode: GraphMode;
  loading: boolean;
  error: string | null;
  degradedStages: string[];
  truncated: boolean;

  replaceGraph: (response: GraphResponse) => void;
  mergeGraph: (response: GraphResponse) => void;
  selectNode: (id: string | null) => void;
  toggleType: (t: NodeType) => void;
  setQuery: (q: string) => void;
  setLoading: (loading: boolean) => void;
  setError: (error: string | null) => void;
  reset: () => void;
}

const ALL_TYPES: NodeType[] = ["Document", "Chunk", "Entity"];

const INITIAL: Omit<
  ExplorerState,
  | "replaceGraph"
  | "mergeGraph"
  | "selectNode"
  | "toggleType"
  | "setQuery"
  | "setLoading"
  | "setError"
  | "reset"
> = {
  nodes: [],
  edges: [],
  hitNodeIds: new Set(),
  selectedNodeId: null,
  visibleTypes: new Set(ALL_TYPES),
  query: "",
  mode: "top_entities",
  loading: false,
  error: null,
  degradedStages: [],
  truncated: false,
};

export const useExplorerStore = create<ExplorerState>((set) => ({
  ...INITIAL,

  replaceGraph: (response) =>
    set({
      nodes: response.nodes,
      edges: response.edges,
      hitNodeIds: new Set(response.meta.hit_node_ids),
      mode: response.meta.mode,
      truncated: response.meta.truncated,
      degradedStages: response.meta.degraded_stages,
      error: null,
    }),

  mergeGraph: (response) =>
    set((state) => {
      const byId = new Map(state.nodes.map((n) => [n.id, n]));
      for (const n of response.nodes) byId.set(n.id, n);
      const edgeIds = new Set(state.edges.map((e) => e.id));
      const mergedEdges = [...state.edges];
      for (const e of response.edges) {
        if (!edgeIds.has(e.id)) {
          mergedEdges.push(e);
          edgeIds.add(e.id);
        }
      }
      return {
        nodes: [...byId.values()],
        edges: mergedEdges,
        hitNodeIds: new Set(response.meta.hit_node_ids),
        mode: response.meta.mode,
        truncated: state.truncated || response.meta.truncated,
        degradedStages: response.meta.degraded_stages,
        error: null,
      };
    }),

  selectNode: (id) => set({ selectedNodeId: id }),

  toggleType: (t) =>
    set((state) => {
      const next = new Set(state.visibleTypes);
      if (next.has(t)) next.delete(t);
      else next.add(t);
      return { visibleTypes: next };
    }),

  setQuery: (q) => set({ query: q }),
  setLoading: (loading) => set({ loading }),
  setError: (error) => set({ error }),

  reset: () => set({ ...INITIAL, visibleTypes: new Set(ALL_TYPES), hitNodeIds: new Set() }),
}));
