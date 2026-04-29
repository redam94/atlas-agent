import { describe, expect, it, beforeEach } from "vitest";
import type { GraphResponse } from "@/lib/api/knowledge-graph";
import { useExplorerStore } from "./explorer-store";

const mkResponse = (overrides: Partial<GraphResponse> = {}): GraphResponse => ({
  nodes: [],
  edges: [],
  meta: { mode: "top_entities", truncated: false, hit_node_ids: [], degraded_stages: [] },
  ...overrides,
});

describe("explorer-store", () => {
  beforeEach(() => {
    useExplorerStore.getState().reset();
  });

  it("replaceGraph swaps nodes/edges/meta", () => {
    useExplorerStore.getState().mergeGraph(
      mkResponse({ nodes: [{ id: "a", type: "Entity", label: "A", pagerank: 0.1, metadata: {} }] }),
    );
    useExplorerStore.getState().replaceGraph(
      mkResponse({
        nodes: [{ id: "b", type: "Entity", label: "B", pagerank: 0.2, metadata: {} }],
        meta: { mode: "search", truncated: false, hit_node_ids: ["b"], degraded_stages: [] },
      }),
    );
    const s = useExplorerStore.getState();
    expect(s.nodes.map((n) => n.id)).toEqual(["b"]);
    expect(s.hitNodeIds).toEqual(new Set(["b"]));
    expect(s.mode).toBe("search");
  });

  it("mergeGraph dedupes by id", () => {
    useExplorerStore.getState().mergeGraph(
      mkResponse({
        nodes: [{ id: "a", type: "Entity", label: "A", pagerank: 0.1, metadata: {} }],
      }),
    );
    useExplorerStore.getState().mergeGraph(
      mkResponse({
        nodes: [
          { id: "a", type: "Entity", label: "A2", pagerank: 0.5, metadata: {} },
          { id: "b", type: "Chunk", label: "B", pagerank: null, metadata: {} },
        ],
        meta: { mode: "expand", truncated: false, hit_node_ids: [], degraded_stages: [] },
      }),
    );
    const s = useExplorerStore.getState();
    expect(s.nodes.map((n) => n.id).sort()).toEqual(["a", "b"]);
    // Latest wins on dedupe.
    expect(s.nodes.find((n) => n.id === "a")?.pagerank).toBe(0.5);
  });

  it("toggleType flips set membership", () => {
    useExplorerStore.getState().toggleType("Chunk");
    expect(useExplorerStore.getState().visibleTypes.has("Chunk")).toBe(false);
    useExplorerStore.getState().toggleType("Chunk");
    expect(useExplorerStore.getState().visibleTypes.has("Chunk")).toBe(true);
  });

  it("selectNode sets selectedNodeId; passing null clears", () => {
    useExplorerStore.getState().selectNode("a");
    expect(useExplorerStore.getState().selectedNodeId).toBe("a");
    useExplorerStore.getState().selectNode(null);
    expect(useExplorerStore.getState().selectedNodeId).toBeNull();
  });
});
