import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";

export type IngestionStatus = "pending" | "running" | "completed" | "failed";

export type IngestionJob = {
  id: string;
  user_id: string;
  project_id: string;
  source_type: "markdown" | "pdf";
  source_filename: string | null;
  status: IngestionStatus;
  node_ids: string[];
  error: string | null;
  created_at: string;
  completed_at: string | null;
};

export function useStartMarkdownIngest() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: { project_id: string; text: string; source_filename?: string }) =>
      api.post<IngestionJob>("/api/v1/knowledge/ingest", {
        project_id: body.project_id,
        source_type: "markdown",
        text: body.text,
        source_filename: body.source_filename,
      }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["ingestion-jobs"] }),
  });
}

export function useStartPdfIngest() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (body: { project_id: string; file: File }) => {
      const form = new FormData();
      form.append("project_id", body.project_id);
      form.append("file", body.file);
      return api.postForm<IngestionJob>("/api/v1/knowledge/ingest/pdf", form);
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ["ingestion-jobs"] }),
  });
}

export function useIngestJob(job_id: string | undefined) {
  return useQuery({
    queryKey: ["ingestion-jobs", job_id],
    queryFn: () => api.get<IngestionJob>(`/api/v1/knowledge/jobs/${job_id}`),
    enabled: Boolean(job_id),
    refetchInterval: (query) => {
      const status = query.state.data?.status;
      if (status === "completed" || status === "failed") return false;
      return 1000;
    },
  });
}
