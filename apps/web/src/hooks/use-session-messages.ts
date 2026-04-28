import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";

export type SessionMessage = {
  id: string;
  user_id: string;
  session_id: string;
  role: "user" | "assistant" | "system";
  content: string;
  tool_calls: Array<Record<string, unknown>> | null;
  rag_context: Array<Record<string, unknown>> | null;
  model: string | null;
  token_count: number | null;
  created_at: string;
};

export function useSessionMessages(session_id: string | undefined) {
  return useQuery({
    queryKey: ["sessions", session_id, "messages"],
    queryFn: () => api.get<SessionMessage[]>(`/api/v1/sessions/${session_id}/messages`),
    enabled: Boolean(session_id),
    staleTime: Infinity, // we manage the cache by appending live tokens through useAtlasChat
  });
}
