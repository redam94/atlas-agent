import { useNavigate, useParams } from "react-router-dom";
import { Plus } from "lucide-react";
import { Button } from "@/components/ui/button";
import { useCreateNote, useNotesQuery } from "./use-notes";
import { NoteListItem } from "./note-list-item";

export function NoteListRail() {
  const { id: projectId } = useParams<{ id: string }>();
  const { data, isPending, error } = useNotesQuery(projectId!);
  const createMutation = useCreateNote(projectId!);
  const navigate = useNavigate();

  const handleCreate = async () => {
    const note = await createMutation.mutateAsync({ project_id: projectId! });
    navigate(`/projects/${projectId}/notes/${note.id}`);
  };

  return (
    <aside className="flex w-64 flex-col border-r bg-muted/20">
      <div className="border-b p-2">
        <Button
          size="sm"
          className="w-full"
          onClick={handleCreate}
          disabled={createMutation.isPending}
        >
          <Plus className="mr-1 h-4 w-4" /> New note
        </Button>
      </div>
      <div className="flex-1 overflow-y-auto p-2">
        {isPending && (
          <div className="text-xs text-muted-foreground">Loading…</div>
        )}
        {error && (
          <div className="text-xs text-destructive">Failed to load notes.</div>
        )}
        {data && data.length === 0 && (
          <div className="text-xs text-muted-foreground">
            No notes yet — click "+ New note" to start.
          </div>
        )}
        {data && data.length > 0 && (
          <ul className="space-y-1">
            {data.map((n) => (
              <li key={n.id}>
                <NoteListItem projectId={projectId!} note={n} />
              </li>
            ))}
          </ul>
        )}
      </div>
    </aside>
  );
}
