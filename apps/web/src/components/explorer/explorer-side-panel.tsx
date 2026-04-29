import { useExplorerStore } from "@/stores/explorer-store";
import { Button } from "@/components/ui/button";

interface Props {
  onExpand: (seedId: string) => void;
}

export function ExplorerSidePanel({ onExpand }: Props) {
  const selectedNodeId = useExplorerStore((s) => s.selectedNodeId);
  const node = useExplorerStore((s) =>
    s.nodes.find((n) => n.id === s.selectedNodeId) ?? null
  );
  const selectNode = useExplorerStore((s) => s.selectNode);

  if (!selectedNodeId || !node) return null;

  return (
    <aside className="absolute right-0 top-0 z-10 flex h-full w-80 flex-col border-l bg-background shadow-lg">
      <header className="flex items-center justify-between border-b p-3">
        <div className="min-w-0">
          <div className="text-xs uppercase tracking-wider text-muted-foreground">
            {node.type}
          </div>
          <div className="truncate text-base font-semibold">{node.label}</div>
        </div>
        <button
          type="button"
          aria-label="Close panel"
          onClick={() => selectNode(null)}
          className="text-muted-foreground hover:text-foreground"
        >
          ×
        </button>
      </header>
      <div className="flex-1 overflow-y-auto p-3 text-sm">
        {node.pagerank !== null && (
          <div className="mb-2">
            <span className="text-muted-foreground">pagerank: </span>
            <span>{node.pagerank.toFixed(4)}</span>
          </div>
        )}
        <dl className="space-y-1">
          {Object.entries(node.metadata).map(([k, v]) => (
            <div key={k} className="grid grid-cols-[8rem_1fr] gap-2">
              <dt className="text-muted-foreground">{k}</dt>
              <dd className="break-words">{String(v)}</dd>
            </div>
          ))}
        </dl>
      </div>
      <footer className="border-t p-3">
        <Button
          variant="default"
          className="w-full"
          onClick={() => onExpand(node.id)}
        >
          Expand neighborhood
        </Button>
      </footer>
    </aside>
  );
}
