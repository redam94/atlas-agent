import { useState } from "react";
import { ChevronRight } from "lucide-react";
import type { ToolCard } from "@/hooks/use-atlas-chat";
import { cn } from "@/lib/cn";

export function ToolUseCard({ card }: { card: ToolCard }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="my-2 rounded-md border bg-background text-xs">
      <button
        className="flex w-full items-center gap-1 px-2 py-1.5 text-left hover:bg-accent"
        onClick={() => setOpen((v) => !v)}
      >
        <ChevronRight className={cn("h-3 w-3 transition-transform", open && "rotate-90")} />
        <span className="font-medium">🔧 {card.name ?? "tool call"}</span>
      </button>
      {open && (
        <div className="border-t p-2 font-mono text-[11px] text-muted-foreground">
          <div className="mb-1">arguments:</div>
          <pre className="whitespace-pre-wrap">{JSON.stringify(card.arguments ?? {}, null, 2)}</pre>
          {card.result !== undefined && (
            <>
              <div className="mt-2 mb-1">result:</div>
              <pre className="whitespace-pre-wrap">{JSON.stringify(card.result, null, 2)}</pre>
            </>
          )}
        </div>
      )}
    </div>
  );
}
