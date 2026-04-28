import { useCallback, useEffect, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import {
  parseStreamEvent,
  isToken,
  isRagContext,
  isToolUse,
  isToolResult,
  isDone,
  isError,
  type Citation,
  type ChatMessageOut,
} from "@/lib/ws-protocol";
import { useSessionMessages, type SessionMessage } from "@/hooks/use-session-messages";

export type ToolCard = {
  id: string;
  name?: string;
  arguments?: Record<string, unknown>;
  result?: unknown;
};

export type ChatMessage = {
  client_id: string;
  role: "user" | "assistant" | "system";
  content: string;
  tool_cards?: ToolCard[];
  finalized: boolean;
};

const BACKOFF_MS = [1000, 2000, 4000, 8000, 16000, 30000];

export function useAtlasChat(opts: {
  session_id: string;
  project_id: string;
  model_id: string | undefined;
}) {
  const { session_id, project_id, model_id } = opts;
  const queryClient = useQueryClient();
  const { data: persisted } = useSessionMessages(session_id);

  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [rag_context, setRagContext] = useState<Citation[] | null>(null);
  const [is_streaming, setStreaming] = useState(false);
  const [error, setError] = useState<{ code: string; message: string } | null>(null);

  const wsRef = useRef<WebSocket | null>(null);
  const retryRef = useRef(0);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const aliveRef = useRef(true);

  // Hydrate messages from persisted on first load. We replace, not merge,
  // because persisted is the authoritative server state for this session.
  useEffect(() => {
    if (!persisted) return;
    setMessages(
      persisted.map(
        (m: SessionMessage): ChatMessage => ({
          client_id: m.id,
          role: m.role,
          content: m.content,
          finalized: true,
        }),
      ),
    );
  }, [persisted]);

  const connect = useCallback(() => {
    if (!aliveRef.current) return;
    const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
    const url = `${proto}//${window.location.host}/api/v1/ws/${session_id}`;
    const ws = new WebSocket(url);
    wsRef.current = ws;

    ws.onopen = () => {
      retryRef.current = 0;
    };

    ws.onmessage = (ev) => {
      const event = parseStreamEvent(typeof ev.data === "string" ? ev.data : "");
      if (!event) return;

      if (isToken(event)) {
        setMessages((prev) => {
          const last = prev[prev.length - 1];
          if (!last || last.role !== "assistant" || last.finalized) return prev;
          const updated = { ...last, content: last.content + event.payload.token };
          return [...prev.slice(0, -1), updated];
        });
        return;
      }
      if (isRagContext(event)) {
        setRagContext(event.payload.citations);
        return;
      }
      if (isToolUse(event)) {
        const id = event.payload.id ?? crypto.randomUUID();
        setMessages((prev) => {
          const last = prev[prev.length - 1];
          if (!last || last.role !== "assistant") return prev;
          const cards = last.tool_cards ?? [];
          return [
            ...prev.slice(0, -1),
            { ...last, tool_cards: [...cards, { id, name: event.payload.name, arguments: event.payload.arguments }] },
          ];
        });
        return;
      }
      if (isToolResult(event)) {
        const id = event.payload.id;
        setMessages((prev) => {
          const last = prev[prev.length - 1];
          if (!last || !last.tool_cards) return prev;
          const updated = last.tool_cards.map((c) =>
            c.id === id ? { ...c, result: event.payload.result } : c,
          );
          return [...prev.slice(0, -1), { ...last, tool_cards: updated }];
        });
        return;
      }
      if (isDone(event)) {
        setStreaming(false);
        setMessages((prev) => {
          const last = prev[prev.length - 1];
          if (!last || last.finalized) return prev;
          return [...prev.slice(0, -1), { ...last, finalized: true }];
        });
        // Refetch canonical persisted messages so future hydrations include this turn.
        queryClient.invalidateQueries({ queryKey: ["sessions", session_id, "messages"] });
        return;
      }
      if (isError(event)) {
        setError(event.payload);
        setStreaming(false);
        setMessages((prev) => {
          const last = prev[prev.length - 1];
          if (!last || last.finalized) return prev;
          return [...prev.slice(0, -1), { ...last, finalized: true }];
        });
        return;
      }
    };

    ws.onclose = (ev) => {
      if (!aliveRef.current) return;
      if (ev.code === 1000) return;
      // Finalize a partial message with a "(disconnected)" trailer.
      setMessages((prev) => {
        const last = prev[prev.length - 1];
        if (!last || last.finalized || last.role !== "assistant") return prev;
        return [
          ...prev.slice(0, -1),
          { ...last, content: last.content + "\n\n_(disconnected)_", finalized: true },
        ];
      });
      setStreaming(false);
      const idx = Math.min(retryRef.current, BACKOFF_MS.length - 1);
      const delay = BACKOFF_MS[idx];
      retryRef.current += 1;
      timerRef.current = setTimeout(connect, delay);
    };

    ws.onerror = () => {
      // ignore — onclose handles reconnect
    };
  }, [session_id, queryClient]);

  useEffect(() => {
    aliveRef.current = true;
    connect();
    return () => {
      aliveRef.current = false;
      if (timerRef.current) clearTimeout(timerRef.current);
      wsRef.current?.close(1000);
    };
  }, [connect]);

  const send = useCallback(
    (text: string) => {
      const trimmed = text.trim();
      if (!trimmed) return;
      setError(null);
      setRagContext(null);
      const userMsg: ChatMessage = {
        client_id: crypto.randomUUID(),
        role: "user",
        content: trimmed,
        finalized: true,
      };
      const assistantStub: ChatMessage = {
        client_id: crypto.randomUUID(),
        role: "assistant",
        content: "",
        finalized: false,
      };
      setMessages((prev) => [...prev, userMsg, assistantStub]);
      setStreaming(true);

      const out: ChatMessageOut = {
        type: "chat.message",
        payload: {
          text: trimmed,
          project_id,
          ...(model_id ? { model_override: model_id } : {}),
        },
      };
      wsRef.current?.send(JSON.stringify(out));
    },
    [project_id, model_id],
  );

  const cancel = useCallback(() => {
    wsRef.current?.close(4000, "client_cancel");
    setStreaming(false);
  }, []);

  return { messages, rag_context, is_streaming, error, send, cancel };
}
