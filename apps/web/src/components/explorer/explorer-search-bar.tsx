import { Search, X } from "lucide-react";
import { useExplorerStore } from "@/stores/explorer-store";
import { Input } from "@/components/ui/input";

interface Props {
  onSubmit: (query: string) => void;
  onClear?: () => void;
}

export function ExplorerSearchBar({ onSubmit, onClear }: Props) {
  const query = useExplorerStore((s) => s.query);
  const setQuery = useExplorerStore((s) => s.setQuery);

  return (
    <div className="relative flex-1 max-w-md">
      <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
      <Input
        value={query}
        onChange={(e) => setQuery(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter" && query.trim()) onSubmit(query.trim());
        }}
        placeholder="Search the graph…"
        className="pl-9 pr-8"
      />
      {query && (
        <button
          type="button"
          aria-label="Clear search"
          onClick={() => {
            setQuery("");
            onClear?.();
          }}
          className="absolute right-2 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground"
        >
          <X className="h-4 w-4" />
        </button>
      )}
    </div>
  );
}
