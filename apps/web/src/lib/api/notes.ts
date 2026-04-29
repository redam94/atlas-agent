export interface Note {
  id: string;
  user_id: string;
  project_id: string;
  knowledge_node_id: string | null;
  title: string;
  body_markdown: string;
  mention_entity_ids: string[];
  indexed_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface NoteListItem {
  id: string;
  title: string;
  updated_at: string;
  indexed_at: string | null;
}

export interface CreateNoteArgs {
  project_id: string;
  title?: string;
  body_markdown?: string;
}

export interface PatchNoteArgs {
  title?: string;
  body_markdown?: string;
  mention_entity_ids?: string[];
}

export interface IndexNoteResult {
  id: string;
  status: string;
  source_type: string;
  pagerank_status: string;
}

export class NotesApiError extends Error {
  constructor(message: string, public readonly status: number) {
    super(message);
    this.name = "NotesApiError";
  }
}

async function ok<T>(resp: Response): Promise<T> {
  if (!resp.ok) {
    const body = await resp.text();
    throw new NotesApiError(body || resp.statusText, resp.status);
  }
  if (resp.status === 204) return undefined as T;
  return resp.json();
}

export async function fetchNotes(projectId: string): Promise<NoteListItem[]> {
  const r = await fetch(`/api/v1/notes?project_id=${encodeURIComponent(projectId)}`);
  return ok<NoteListItem[]>(r);
}

export async function fetchNote(noteId: string): Promise<Note> {
  return ok<Note>(await fetch(`/api/v1/notes/${noteId}`));
}

export async function createNote(args: CreateNoteArgs): Promise<Note> {
  return ok<Note>(
    await fetch("/api/v1/notes", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(args),
    }),
  );
}

export async function patchNote(noteId: string, args: PatchNoteArgs): Promise<Note> {
  return ok<Note>(
    await fetch(`/api/v1/notes/${noteId}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(args),
    }),
  );
}

export async function indexNote(noteId: string): Promise<IndexNoteResult> {
  return ok<IndexNoteResult>(
    await fetch(`/api/v1/notes/${noteId}/index`, { method: "POST" }),
  );
}

export async function deleteNote(noteId: string): Promise<void> {
  await ok<void>(await fetch(`/api/v1/notes/${noteId}`, { method: "DELETE" }));
}
