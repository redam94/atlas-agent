import { StickyNote } from "lucide-react";

export function NoteEmpty() {
  return (
    <div className="flex h-full flex-col items-center justify-center gap-2 text-muted-foreground">
      <StickyNote className="h-8 w-8" />
      <div className="text-sm">Select a note or create a new one.</div>
    </div>
  );
}
