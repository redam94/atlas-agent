import Mention from "@tiptap/extension-mention";
import type { SuggestionOptions } from "@tiptap/suggestion";
import { fetchEntities } from "@/lib/api/entities";
import { renderMentionSuggestion } from "./note-mention-list";

interface DocNode {
  type: string;
  content?: DocNode[];
  attrs?: { id?: string; label?: string };
}

export function extractMentionIds(doc: DocNode): Set<string> {
  const out = new Set<string>();
  const walk = (n: DocNode) => {
    if (n.type === "mention" && n.attrs?.id) out.add(n.attrs.id);
    if (n.content) n.content.forEach(walk);
  };
  walk(doc);
  return out;
}

export const buildMention = (projectId: string) =>
  Mention.configure({
    HTMLAttributes: { class: "mention-chip" },
    suggestion: {
      char: "@",
      items: async ({ query }: { query: string }) =>
        fetchEntities(projectId, query, 10),
      render: renderMentionSuggestion,
    } as unknown as Partial<SuggestionOptions>,
  });
