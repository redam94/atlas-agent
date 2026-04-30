import { create } from "zustand";

interface NotesState {
  draftBody: string;
  draftTitle: string;
  draftMentionIds: Set<string>;
  dirty: boolean;

  setDraft: (body: string, title: string, mentions: Set<string>) => void;
  markSaved: () => void;
  reset: () => void;
}

const INITIAL: Pick<NotesState, "draftBody" | "draftTitle" | "draftMentionIds" | "dirty"> = {
  draftBody: "",
  draftTitle: "",
  draftMentionIds: new Set(),
  dirty: false,
};

export const useNotesStore = create<NotesState>((set) => ({
  ...INITIAL,

  setDraft: (body, title, mentions) =>
    set({ draftBody: body, draftTitle: title, draftMentionIds: mentions, dirty: true }),

  markSaved: () => set({ dirty: false }),

  reset: () => set({ ...INITIAL, draftMentionIds: new Set() }),
}));
