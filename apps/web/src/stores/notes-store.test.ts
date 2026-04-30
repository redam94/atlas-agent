import { describe, expect, it, beforeEach } from "vitest";
import { useNotesStore } from "./notes-store";

describe("notes-store", () => {
  beforeEach(() => useNotesStore.getState().reset());

  it("setDraft updates fields and marks dirty", () => {
    useNotesStore.getState().setDraft("body", "Title", new Set(["e1"]));
    const s = useNotesStore.getState();
    expect(s.draftBody).toBe("body");
    expect(s.draftTitle).toBe("Title");
    expect(s.draftMentionIds).toEqual(new Set(["e1"]));
    expect(s.dirty).toBe(true);
  });

  it("markSaved clears dirty flag", () => {
    useNotesStore.getState().setDraft("body", "Title", new Set());
    expect(useNotesStore.getState().dirty).toBe(true);
    useNotesStore.getState().markSaved();
    expect(useNotesStore.getState().dirty).toBe(false);
  });

  it("reset returns to initial state", () => {
    useNotesStore.getState().setDraft("body", "Title", new Set(["e1"]));
    useNotesStore.getState().reset();
    const s = useNotesStore.getState();
    expect(s.draftBody).toBe("");
    expect(s.draftTitle).toBe("");
    expect(s.draftMentionIds.size).toBe(0);
    expect(s.dirty).toBe(false);
  });
});
