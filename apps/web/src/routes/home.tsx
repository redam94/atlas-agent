import { Navigate } from "react-router-dom";
import { useProjects } from "@/hooks/use-projects";

export function IndexRoute() {
  const { data, isLoading } = useProjects();
  if (isLoading) return <div className="p-6 text-sm text-muted-foreground">Loading…</div>;
  const active = data?.find((p) => p.status !== "archived");
  if (active) return <Navigate to={`/projects/${active.id}`} replace />;
  return (
    <div className="p-6">
      <p className="text-sm text-muted-foreground">
        No projects yet. Click "+ New Project" in the sidebar to create one.
      </p>
    </div>
  );
}
