import { NavLink } from "react-router-dom";
import { MessageSquare, Network } from "lucide-react";
import { cn } from "@/lib/cn";

interface Props {
  projectId: string;
}

export function ProjectTabs({ projectId }: Props) {
  return (
    <nav className="flex gap-1 border-b px-3 py-2">
      <NavLink
        to={`/projects/${projectId}`}
        end
        className={({ isActive }) =>
          cn(
            "flex items-center gap-1.5 rounded-md px-3 py-1 text-sm transition",
            isActive ? "bg-accent font-medium" : "text-muted-foreground hover:bg-accent/50",
          )
        }
      >
        <MessageSquare className="h-4 w-4" />
        Chat
      </NavLink>
      <NavLink
        to={`/projects/${projectId}/explorer`}
        className={({ isActive }) =>
          cn(
            "flex items-center gap-1.5 rounded-md px-3 py-1 text-sm transition",
            isActive ? "bg-accent font-medium" : "text-muted-foreground hover:bg-accent/50",
          )
        }
      >
        <Network className="h-4 w-4" />
        Explorer
      </NavLink>
    </nav>
  );
}
