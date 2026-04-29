import { useExplorerStore } from "@/stores/explorer-store";
import { cn } from "@/lib/cn";
import type { NodeType } from "@/lib/api/knowledge-graph";

const TYPES: { type: NodeType; color: string }[] = [
  { type: "Document", color: "bg-blue-500/20 text-blue-700 ring-blue-500" },
  { type: "Chunk", color: "bg-gray-500/20 text-gray-700 ring-gray-500" },
  { type: "Entity", color: "bg-emerald-500/20 text-emerald-700 ring-emerald-500" },
];

export function ExplorerFilterPills() {
  const visibleTypes = useExplorerStore((s) => s.visibleTypes);
  const toggleType = useExplorerStore((s) => s.toggleType);

  return (
    <div className="flex gap-2">
      {TYPES.map(({ type, color }) => {
        const active = visibleTypes.has(type);
        return (
          <button
            key={type}
            type="button"
            aria-pressed={active}
            onClick={() => toggleType(type)}
            className={cn(
              "rounded-full px-3 py-1 text-xs font-medium ring-1 ring-inset transition",
              active ? color : "bg-transparent text-muted-foreground ring-muted",
            )}
          >
            {type}
          </button>
        );
      })}
    </div>
  );
}
