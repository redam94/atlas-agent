import { Link, useParams } from "react-router-dom";
import { useProjects } from "@/hooks/use-projects";
import { cn } from "@/lib/cn";

export function ProjectList() {
  const { id: activeId } = useParams<{ id: string }>();
  const { data, isLoading, error } = useProjects();

  if (isLoading) return <div className="text-sm text-muted-foreground">Loading…</div>;
  if (error) return <div className="text-sm text-destructive">Failed to load projects.</div>;
  if (!data || data.length === 0) {
    return <div className="text-sm text-muted-foreground">No projects yet.</div>;
  }

  return (
    <ul className="space-y-1">
      {data
        .filter((p) => p.status !== "archived")
        .map((p) => (
          <li key={p.id}>
            <Link
              to={`/projects/${p.id}`}
              className={cn(
                "block rounded-md px-2 py-1.5 text-sm hover:bg-accent",
                activeId === p.id && "bg-accent font-medium",
              )}
            >
              {p.name}
            </Link>
          </li>
        ))}
    </ul>
  );
}
