import { useEffect, useMemo, useRef } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { EditorContent, useEditor } from "@tiptap/react";
import StarterKit from "@tiptap/starter-kit";
import { marked } from "marked";
import TurndownService from "turndown";
import { Trash2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { cn } from "@/lib/cn";
import { useNotesStore } from "@/stores/notes-store";
import {
  useDeleteNote,
  useIndexNote,
  useNoteQuery,
  usePatchNote,
} from "./use-notes";
import { buildMention, extractMentionIds } from "./note-mention-extension";

const turndown = new TurndownService({ headingStyle: "atx" });

type SaveStatus = "Loading" | "Unsaved" | "Saving…" | "Saved" | "Indexed" | "Indexed (stale)";

function deriveStatus(opts: {
  loading: boolean;
  dirty: boolean;
  patching: boolean;
  indexed_at: string | null;
  updated_at: string;
}): SaveStatus {
  if (opts.loading) return "Loading";
  if (opts.patching) return "Saving…";
  if (opts.dirty) return "Unsaved";
  if (opts.indexed_at) {
    return new Date(opts.updated_at).getTime() > new Date(opts.indexed_at).getTime()
      ? "Indexed (stale)"
      : "Indexed";
  }
  return "Saved";
}

export function NoteEditor() {
  const { id: projectId, noteId } = useParams<{ id: string; noteId: string }>();
  const navigate = useNavigate();
  const noteQuery = useNoteQuery(noteId);
  const patchMutation = usePatchNote(projectId!);
  const indexMutation = useIndexNote(projectId!);
  const deleteMutation = useDeleteNote(projectId!);

  const draftBody = useNotesStore((s) => s.draftBody);
  const draftTitle = useNotesStore((s) => s.draftTitle);
  const draftMentionIds = useNotesStore((s) => s.draftMentionIds);
  const dirty = useNotesStore((s) => s.dirty);
  const setDraft = useNotesStore((s) => s.setDraft);
  const markSaved = useNotesStore((s) => s.markSaved);
  const reset = useNotesStore((s) => s.reset);

  const debounceTimer = useRef<number | null>(null);

  const mention = useMemo(
    () => (projectId ? buildMention(projectId) : null),
    [projectId],
  );

  const editor = useEditor({
    extensions: mention ? [StarterKit, mention] : [StarterKit],
    content: "",
    onUpdate: ({ editor }) => {
      const html = editor.getHTML();
      const md = turndown.turndown(html);
      const mentions = extractMentionIds(editor.getJSON() as never);
      setDraft(md, draftTitle, mentions);
    },
  });

  // Hydrate the editor when the note arrives.
  useEffect(() => {
    reset();
    if (!noteQuery.data || !editor) return;
    const html = marked.parse(noteQuery.data.body_markdown ?? "", { async: false }) as string;
    editor.commands.setContent(html);
    setDraft(noteQuery.data.body_markdown, noteQuery.data.title, new Set(noteQuery.data.mention_entity_ids));
    markSaved();  // freshly loaded = clean
  }, [noteQuery.data?.id]);  // eslint-disable-line react-hooks/exhaustive-deps

  // Debounced auto-save.
  useEffect(() => {
    if (!noteId || !dirty) return;
    if (debounceTimer.current) window.clearTimeout(debounceTimer.current);
    debounceTimer.current = window.setTimeout(() => {
      patchMutation.mutate(
        {
          noteId,
          args: {
            title: draftTitle,
            body_markdown: draftBody,
            mention_entity_ids: [...draftMentionIds],
          },
        },
        { onSuccess: () => markSaved() },
      );
    }, 2000);
    return () => {
      if (debounceTimer.current) window.clearTimeout(debounceTimer.current);
    };
  }, [draftBody, draftTitle, draftMentionIds, dirty, noteId]);  // eslint-disable-line react-hooks/exhaustive-deps

  const handleSaveAndIndex = async () => {
    if (!noteId) return;
    if (dirty) {
      await patchMutation.mutateAsync({
        noteId,
        args: {
          title: draftTitle,
          body_markdown: draftBody,
          mention_entity_ids: [...draftMentionIds],
        },
      });
      markSaved();
    }
    await indexMutation.mutateAsync(noteId);
  };

  const handleDelete = async () => {
    if (!noteId) return;
    if (!window.confirm("Delete this note? This will also remove its chunks from search.")) {
      return;
    }
    await deleteMutation.mutateAsync(noteId);
    navigate(`/projects/${projectId}/notes`);
  };

  if (!noteQuery.data && noteQuery.isPending) {
    return <div className="p-4 text-sm text-muted-foreground">Loading…</div>;
  }
  if (noteQuery.error) {
    return <div className="p-4 text-sm text-destructive">Failed to load note.</div>;
  }
  if (!noteQuery.data) return null;

  const status = deriveStatus({
    loading: noteQuery.isPending,
    dirty,
    patching: patchMutation.isPending,
    indexed_at: noteQuery.data.indexed_at,
    updated_at: noteQuery.data.updated_at,
  });
  const indexing = indexMutation.isPending;

  return (
    <div className="flex h-full flex-col">
      <div className="flex items-center gap-3 border-b p-3">
        <Input
          value={draftTitle}
          onChange={(e) => setDraft(draftBody, e.target.value, draftMentionIds)}
          placeholder="Untitled"
          className="border-0 text-base font-semibold focus-visible:ring-0"
        />
        <span
          className={cn(
            "rounded px-2 py-0.5 text-xs",
            status === "Indexed" && "bg-blue-100 text-blue-800",
            status === "Indexed (stale)" && "bg-amber-100 text-amber-900",
            status === "Saving…" && "bg-muted text-muted-foreground animate-pulse",
            status === "Saved" && "bg-muted text-muted-foreground",
            status === "Unsaved" && "bg-amber-50 text-amber-800",
          )}
        >
          {indexing ? "Indexing…" : status}
        </span>
        <Button onClick={handleSaveAndIndex} disabled={indexing}>
          Save & Index
        </Button>
        <Button variant="ghost" size="icon" onClick={handleDelete} aria-label="Delete">
          <Trash2 className="h-4 w-4" />
        </Button>
      </div>
      <div
        className="flex-1 cursor-text overflow-y-auto p-6"
        onClick={() => editor?.commands.focus()}
      >
        <div className="mx-auto max-w-3xl">
          <EditorContent editor={editor} />
        </div>
      </div>
    </div>
  );
}
