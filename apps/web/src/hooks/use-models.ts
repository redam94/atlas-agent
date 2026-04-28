import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";

export type ModelSpec = {
  provider: string;
  model_id: string;
  context_window: number;
  supports_tools: boolean;
  supports_streaming: boolean;
};

export function useModels() {
  return useQuery({
    queryKey: ["models"],
    queryFn: () => api.get<ModelSpec[]>("/api/v1/models"),
    staleTime: 5 * 60_000,
  });
}
