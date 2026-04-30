import { describe, expect, it } from "vitest";
import { extractMentionIds } from "./note-mention-extension";

describe("extractMentionIds", () => {
  it("returns empty set on empty doc", () => {
    expect(extractMentionIds({ type: "doc", content: [] })).toEqual(new Set());
  });

  it("collects ids from mention nodes", () => {
    const doc = {
      type: "doc",
      content: [
        {
          type: "paragraph",
          content: [
            { type: "text", text: "Hi " },
            { type: "mention", attrs: { id: "e1", label: "Llama" } },
            { type: "text", text: " and " },
            { type: "mention", attrs: { id: "e2", label: "Sky" } },
          ],
        },
      ],
    };
    expect(extractMentionIds(doc)).toEqual(new Set(["e1", "e2"]));
  });

  it("dedupes repeated mentions of same id", () => {
    const doc = {
      type: "doc",
      content: [
        { type: "mention", attrs: { id: "e1", label: "X" } },
        { type: "mention", attrs: { id: "e1", label: "X" } },
      ],
    };
    expect(extractMentionIds(doc)).toEqual(new Set(["e1"]));
  });
});
