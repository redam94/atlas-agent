import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, beforeEach, vi } from "vitest";
import { useExplorerStore } from "@/stores/explorer-store";
import { ExplorerSidePanel } from "./explorer-side-panel";

describe("ExplorerSidePanel", () => {
  beforeEach(() => useExplorerStore.getState().reset());

  it("renders metadata of the selected node", () => {
    useExplorerStore.getState().mergeGraph({
      nodes: [
        {
          id: "a",
          type: "Entity",
          label: "Llama 3",
          pagerank: 0.5,
          metadata: { entity_type: "PRODUCT", mention_count: 7 },
        },
      ],
      edges: [],
      meta: { mode: "top_entities", truncated: false, hit_node_ids: [], degraded_stages: [] },
    });
    useExplorerStore.getState().selectNode("a");

    render(<ExplorerSidePanel onExpand={() => {}} />);
    expect(screen.getByText("Llama 3")).toBeInTheDocument();
    expect(screen.getByText(/PRODUCT/)).toBeInTheDocument();
    expect(screen.getByText(/7/)).toBeInTheDocument();
  });

  it("Expand button fires onExpand with the selected node id", async () => {
    useExplorerStore.getState().mergeGraph({
      nodes: [{ id: "a", type: "Entity", label: "A", pagerank: 0.1, metadata: {} }],
      edges: [],
      meta: { mode: "top_entities", truncated: false, hit_node_ids: [], degraded_stages: [] },
    });
    useExplorerStore.getState().selectNode("a");

    const onExpand = vi.fn();
    render(<ExplorerSidePanel onExpand={onExpand} />);
    await userEvent.click(screen.getByRole("button", { name: /expand neighborhood/i }));
    expect(onExpand).toHaveBeenCalledWith("a");
  });

  it("renders nothing when no node is selected", () => {
    const { container } = render(<ExplorerSidePanel onExpand={() => {}} />);
    expect(container.firstChild).toBeNull();
  });
});
