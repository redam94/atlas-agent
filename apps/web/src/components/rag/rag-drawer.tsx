import { Sheet, SheetContent, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import { CitationCard } from "./citation-card";
import type { Citation } from "@/lib/ws-protocol";

export function RagDrawer(props: {
  open: boolean;
  citations: Citation[] | null;
  onOpenChange: (open: boolean) => void;
}) {
  return (
    <Sheet open={props.open} onOpenChange={props.onOpenChange}>
      <SheetContent>
        <SheetHeader>
          <SheetTitle>Sources</SheetTitle>
        </SheetHeader>
        {!props.citations || props.citations.length === 0 ? (
          <div className="text-sm text-muted-foreground">
            No sources used yet. Upload knowledge to get RAG-grounded answers.
          </div>
        ) : (
          <div className="flex flex-col gap-2 overflow-y-auto">
            {props.citations.map((c) => (
              <CitationCard key={c.id} cite={c} />
            ))}
          </div>
        )}
      </SheetContent>
    </Sheet>
  );
}
