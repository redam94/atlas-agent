import { describe, expect, it } from "vitest";
import { parseStreamEvent, isToken, isRagContext, isError } from "@/lib/ws-protocol";

describe("parseStreamEvent", () => {
  it("returns null for non-JSON", () => {
    expect(parseStreamEvent("not json")).toBeNull();
  });

  it("returns null for missing fields", () => {
    expect(parseStreamEvent(JSON.stringify({ type: "chat.token" }))).toBeNull();
    expect(parseStreamEvent(JSON.stringify({ payload: {}, sequence: 0 }))).toBeNull();
  });

  it("returns null for unknown event types", () => {
    expect(
      parseStreamEvent(JSON.stringify({ type: "made.up", payload: {}, sequence: 0 })),
    ).toBeNull();
  });

  it("parses every documented event", () => {
    const cases = [
      { type: "chat.token", payload: { token: "hi" }, sequence: 0 },
      { type: "chat.tool_use", payload: { name: "x", arguments: {} }, sequence: 1 },
      { type: "chat.tool_result", payload: { result: null }, sequence: 2 },
      {
        type: "rag.context",
        payload: { citations: [{ id: 1, title: "T", score: 0.9, chunk_id: "x", text_preview: "..." }] },
        sequence: 3,
      },
      { type: "chat.done", payload: {}, sequence: 4 },
      { type: "chat.error", payload: { code: "x", message: "y" }, sequence: 5 },
    ];
    for (const c of cases) {
      const parsed = parseStreamEvent(JSON.stringify(c));
      expect(parsed).not.toBeNull();
      expect(parsed?.type).toBe(c.type);
    }
  });

  it("type guards narrow correctly", () => {
    const tok = parseStreamEvent(JSON.stringify({ type: "chat.token", payload: { token: "a" }, sequence: 0 }));
    expect(tok && isToken(tok)).toBe(true);
    expect(tok && isToken(tok) && tok.payload.token).toBe("a");

    const rag = parseStreamEvent(JSON.stringify({
      type: "rag.context",
      payload: { citations: [] },
      sequence: 0,
    }));
    expect(rag && isRagContext(rag)).toBe(true);

    const err = parseStreamEvent(JSON.stringify({
      type: "chat.error",
      payload: { code: "bad", message: "boom" },
      sequence: 0,
    }));
    expect(err && isError(err)).toBe(true);
  });
});
