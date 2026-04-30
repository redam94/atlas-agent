import { Link, useParams } from "react-router-dom";
import { cn } from "@/lib/cn";
import type { NoteListItem as NoteListItemType } from "@/lib/api/notes";

interface Props {
  projectId: string;
  note: NoteListItemType;
}

export function NoteListItem({ projectId, note }: Props) {
  const { noteId: activeId } = useParams<{ noteId: string }>();
  const isStale =
    note.indexed_at === null ||
    new Date(note.updated_at).getTime() > new Date(note.indexed_at).getTime();
  const updated = new Date(note.updated_at).toLocaleDateString();
  return (
    <Link
      to={`/projects/${projectId}/notes/${note.id}`}
      className={cn(
        "flex flex-col gap-1 rounded-md px-2 py-2 text-sm hover:bg-accent",
        activeId === note.id && "bg-accent font-medium",
      )}
    >
      <div className="flex items-center gap-2">
        <span className="truncate flex-1">{note.title || "Untitled"}</span>
        {isStale && (
          <span
            aria-label="Index out of date"
            className="h-2 w-2 rounded-full bg-amber-500"
          />
        )}
      </div>
      <div className="text-xs text-muted-foreground">{updated}</div>
    </Link>
  );
}
