import { Check, Loader2, X } from "lucide-react";
import { cn } from "@/lib/cn";

interface Props {
  toolName: string;
  status: "pending" | "ok" | "error";
  durationMs?: number;
}

export function ToolCallChip({ toolName, status, durationMs }: Props) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-xs font-mono",
        status === "pending" && "border-blue-300 bg-blue-50 text-blue-900",
        status === "ok" && "border-emerald-300 bg-emerald-50 text-emerald-900",
        status === "error" && "border-red-300 bg-red-50 text-red-900",
      )}
    >
      {status === "pending" && (
        <Loader2 aria-label="calling tool" className="h-3 w-3 animate-spin" />
      )}
      {status === "ok" && <Check className="h-3 w-3" />}
      {status === "error" && (
        <X aria-label="tool failed" className="h-3 w-3" />
      )}
      <span>{toolName}</span>
      {status !== "pending" && durationMs !== undefined && (
        <span className="text-muted-foreground">({durationMs}ms)</span>
      )}
    </span>
  );
}
