import { useEffect, useMemo, useRef } from "react";
import cytoscape, { type Core, type ElementDefinition } from "cytoscape";
import fcose from "cytoscape-fcose";
import { useExplorerStore } from "@/stores/explorer-store";

cytoscape.use(fcose);

const TYPE_COLORS: Record<string, string> = {
  Document: "#3b82f6",
  Chunk: "#9ca3af",
  Entity: "#10b981",
};

export function ExplorerCanvas() {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const cyRef = useRef<Core | null>(null);

  const nodes = useExplorerStore((s) => s.nodes);
  const edges = useExplorerStore((s) => s.edges);
  const hitNodeIds = useExplorerStore((s) => s.hitNodeIds);
  const visibleTypes = useExplorerStore((s) => s.visibleTypes);
  const selectedNodeId = useExplorerStore((s) => s.selectedNodeId);
  const selectNode = useExplorerStore((s) => s.selectNode);

  const elements = useMemo<ElementDefinition[]>(() => {
    const nodeEls: ElementDefinition[] = nodes.map((n) => ({
      group: "nodes",
      data: {
        id: n.id,
        label: n.label,
        type: n.type,
        pagerank: n.pagerank ?? 0,
        hit: hitNodeIds.has(n.id),
      },
    }));
    const edgeEls: ElementDefinition[] = edges.map((e) => ({
      group: "edges",
      data: { id: e.id, source: e.source, target: e.target, type: e.type },
    }));
    return [...nodeEls, ...edgeEls];
  }, [nodes, edges, hitNodeIds]);

  // Mount once.
  useEffect(() => {
    if (!containerRef.current) return;
    const cy = cytoscape({
      container: containerRef.current,
      elements: [],
      style: [
        {
          selector: "node",
          style: {
            "background-color": (ele: cytoscape.NodeSingular) =>
              TYPE_COLORS[ele.data("type")] ?? "#888",
            label: "data(label)",
            "font-size": 11,
            color: "#222",
            "text-wrap": "ellipsis",
            "text-max-width": "120px",
            width: (ele: cytoscape.NodeSingular) =>
              20 + Math.min(40, (ele.data("pagerank") ?? 0) * 200),
            height: (ele: cytoscape.NodeSingular) =>
              20 + Math.min(40, (ele.data("pagerank") ?? 0) * 200),
          },
        },
        {
          selector: "node[hit]",
          style: { "border-color": "#facc15", "border-width": 3 },
        },
        {
          selector: "node:selected",
          style: { "border-color": "#a855f7", "border-width": 4 },
        },
        {
          selector: "edge",
          style: {
            "line-color": "#cbd5e1",
            width: 1,
            "curve-style": "bezier",
          },
        },
      ],
      layout: { name: "preset" },
    });

    cy.on("tap", "node", (evt) => {
      selectNode(evt.target.id());
    });
    cy.on("tap", (evt) => {
      if (evt.target === cy) selectNode(null);
    });

    cyRef.current = cy;
    return () => {
      cy.destroy();
      cyRef.current = null;
    };
  }, [selectNode]);

  // Update elements when store changes.
  useEffect(() => {
    const cy = cyRef.current;
    if (!cy) return;
    cy.json({ elements });
    // cytoscape-fcose extends cytoscape's layout options at runtime but doesn't
    // provide TS types for the extended fields; cast lets us pass fcose-specific
    // options.
    cy.layout({
      name: "fcose",
      animate: false,
      randomize: false,
    } as cytoscape.LayoutOptions).run();
  }, [elements]);

  // Apply visibility filter.
  useEffect(() => {
    const cy = cyRef.current;
    if (!cy) return;
    cy.nodes().forEach((n) => {
      const t = n.data("type");
      n.style("display", visibleTypes.has(t) ? "element" : "none");
    });
  }, [visibleTypes]);

  // Keep selection in sync.
  useEffect(() => {
    const cy = cyRef.current;
    if (!cy) return;
    cy.elements().unselect();
    if (selectedNodeId) {
      cy.getElementById(selectedNodeId).select();
    }
  }, [selectedNodeId]);

  return <div ref={containerRef} className="h-full w-full" />;
}
