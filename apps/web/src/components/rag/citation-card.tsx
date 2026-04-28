import { useState } from "react";
import type { Citation } from "@/lib/ws-protocol";
import { cn } from "@/lib/cn";

export function CitationCard({ cite }: { cite: Citation }) {
  const [expanded, setExpanded] = useState(false);
  return (
    <div
      data-citation-card
      data-expanded={expanded}
      className={cn(
        "cursor-pointer rounded-md border bg-card p-3 hover:bg-accent",
        expanded && "bg-accent",
      )}
      onClick={() => setExpanded((v) => !v)}
    >
      <div className="flex items-baseline justify-between gap-2">
        <div className="font-medium text-sm">{cite.title}</div>
        <div className="font-mono text-xs text-muted-foreground">{cite.score.toFixed(2)}</div>
      </div>
      <div className={cn("mt-1 text-xs text-muted-foreground", expanded ? "" : "line-clamp-3")}>
        {cite.text_preview}
      </div>
    </div>
  );
}
