// Mirrors atlas_core.models.messages.StreamEventType. If the backend
// adds a new event, add it here AND a guard, AND extend the test file.

export type Citation = {
  id: number;
  title: string;
  score: number;
  chunk_id: string;
  text_preview: string;
};

export type StreamEvent =
  | { type: "chat.token"; payload: { token: string }; sequence: number }
  | { type: "chat.tool_use"; payload: { name: string; arguments: Record<string, unknown>; id?: string }; sequence: number }
  | { type: "chat.tool_result"; payload: { id?: string; result: unknown }; sequence: number }
  | { type: "rag.context"; payload: { citations: Citation[] }; sequence: number }
  | { type: "chat.done"; payload: Record<string, unknown>; sequence: number }
  | { type: "chat.error"; payload: { code: string; message: string }; sequence: number };

export type ChatMessageOut = {
  type: "chat.message";
  payload: {
    text: string;
    project_id: string;
    model_override?: string;
    rag_enabled?: boolean;
    top_k_context?: number;
    temperature?: number;
  };
};

export function parseStreamEvent(raw: string): StreamEvent | null {
  let data: unknown;
  try { data = JSON.parse(raw); } catch { return null; }
  if (typeof data !== "object" || data === null) return null;
  const obj = data as Record<string, unknown>;
  if (typeof obj.type !== "string" || typeof obj.sequence !== "number") return null;
  if (typeof obj.payload !== "object" || obj.payload === null) return null;
  switch (obj.type) {
    case "chat.token":
    case "chat.tool_use":
    case "chat.tool_result":
    case "rag.context":
    case "chat.done":
    case "chat.error":
      return obj as unknown as StreamEvent;
    default:
      return null;
  }
}

export function isToken(e: StreamEvent): e is Extract<StreamEvent, { type: "chat.token" }> {
  return e.type === "chat.token";
}
export function isRagContext(e: StreamEvent): e is Extract<StreamEvent, { type: "rag.context" }> {
  return e.type === "rag.context";
}
export function isToolUse(e: StreamEvent): e is Extract<StreamEvent, { type: "chat.tool_use" }> {
  return e.type === "chat.tool_use";
}
export function isToolResult(e: StreamEvent): e is Extract<StreamEvent, { type: "chat.tool_result" }> {
  return e.type === "chat.tool_result";
}
export function isDone(e: StreamEvent): e is Extract<StreamEvent, { type: "chat.done" }> {
  return e.type === "chat.done";
}
export function isError(e: StreamEvent): e is Extract<StreamEvent, { type: "chat.error" }> {
  return e.type === "chat.error";
}
