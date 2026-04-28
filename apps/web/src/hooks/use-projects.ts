import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";

export type Project = {
  id: string;
  user_id: string;
  name: string;
  description: string | null;
  status: "active" | "paused" | "archived";
  privacy_level: "cloud_ok" | "local_only";
  default_model: string;
  enabled_plugins: string[];
  created_at: string;
  updated_at: string;
};

export type ProjectCreateBody = {
  name: string;
  description?: string;
  privacy_level?: "cloud_ok" | "local_only";
  default_model: string;
  enabled_plugins?: string[];
};

export type ProjectUpdateBody = Partial<ProjectCreateBody> & { status?: Project["status"] };

const KEY = ["projects"] as const;

export function useProjects() {
  return useQuery({
    queryKey: KEY,
    queryFn: () => api.get<Project[]>("/api/v1/projects"),
  });
}

export function useProject(id: string | undefined) {
  return useQuery({
    queryKey: [...KEY, id],
    queryFn: () => api.get<Project>(`/api/v1/projects/${id}`),
    enabled: Boolean(id),
  });
}

export function useCreateProject() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: ProjectCreateBody) => api.post<Project>("/api/v1/projects", body),
    onSuccess: () => qc.invalidateQueries({ queryKey: KEY }),
  });
}

export function useUpdateProject(id: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: ProjectUpdateBody) => api.patch<Project>(`/api/v1/projects/${id}`, body),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: KEY });
      qc.invalidateQueries({ queryKey: [...KEY, id] });
    },
  });
}

export function useDeleteProject() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.delete(`/api/v1/projects/${id}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: KEY }),
  });
}
