import { AlertTriangle, Loader2, Network } from "lucide-react";

export type EmptyStateVariant = "loading" | "error" | "empty" | "degraded";

interface Props {
  variant: EmptyStateVariant;
  message?: string;
}

export function ExplorerEmptyState({ variant, message }: Props) {
  if (variant === "loading") {
    return (
      <div className="flex h-full items-center justify-center text-muted-foreground">
        <Loader2 className="mr-2 h-4 w-4 animate-spin" />
        Loading graph…
      </div>
    );
  }
  if (variant === "error") {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-2 text-destructive">
        <AlertTriangle className="h-6 w-6" />
        <div className="text-sm">{message || "Failed to load graph."}</div>
      </div>
    );
  }
  if (variant === "degraded") {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-2 text-amber-700">
        <AlertTriangle className="h-6 w-6" />
        <div className="text-sm">
          Graph data unavailable — showing semantic results only.
        </div>
      </div>
    );
  }
  return (
    <div className="flex h-full flex-col items-center justify-center gap-2 text-muted-foreground">
      <Network className="h-6 w-6" />
      <div className="text-sm">No entities yet — ingest content to populate the graph.</div>
    </div>
  );
}
