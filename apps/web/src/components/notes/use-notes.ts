import {
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import {
  createNote,
  deleteNote,
  fetchNote,
  fetchNotes,
  indexNote,
  patchNote,
  type CreateNoteArgs,
  type Note,
  type NoteListItem,
  type PatchNoteArgs,
} from "@/lib/api/notes";
import { fetchEntities, type Entity } from "@/lib/api/entities";

export function useNotesQuery(projectId: string) {
  return useQuery<NoteListItem[]>({
    queryKey: ["notes", projectId],
    queryFn: () => fetchNotes(projectId),
    staleTime: 30_000,
  });
}

export function useNoteQuery(noteId: string | undefined) {
  return useQuery<Note>({
    queryKey: ["notes", "detail", noteId],
    enabled: !!noteId,
    queryFn: () => fetchNote(noteId!),
  });
}

export function useEntitiesQuery(projectId: string, prefix: string) {
  return useQuery<Entity[]>({
    queryKey: ["entities", projectId, prefix],
    queryFn: () => fetchEntities(projectId, prefix, 10),
    staleTime: 60_000,
    enabled: !!projectId,
  });
}

export function useCreateNote(projectId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (args: CreateNoteArgs) => createNote(args),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["notes", projectId] }),
  });
}

export function usePatchNote(projectId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ noteId, args }: { noteId: string; args: PatchNoteArgs }) =>
      patchNote(noteId, args),
    onSuccess: (note) => {
      qc.setQueryData<Note>(["notes", "detail", note.id], note);
      qc.invalidateQueries({ queryKey: ["notes", projectId] });
    },
  });
}

export function useIndexNote(projectId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (noteId: string) => indexNote(noteId),
    onSuccess: (_, noteId) => {
      qc.invalidateQueries({ queryKey: ["notes", "detail", noteId] });
      qc.invalidateQueries({ queryKey: ["notes", projectId] });
    },
  });
}

export function useDeleteNote(projectId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (noteId: string) => deleteNote(noteId),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["notes", projectId] }),
  });
}
