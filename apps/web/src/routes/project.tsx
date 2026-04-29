import { Outlet, useParams } from "react-router-dom";
import { ProjectTabs } from "@/components/sidebar/project-tabs";
import { ChatPanel } from "@/components/chat/chat-panel";

export function ProjectShell() {
  const { id } = useParams<{ id: string }>();
  if (!id) return null;
  return (
    <div className="flex h-full flex-col">
      <ProjectTabs projectId={id} />
      <div className="flex-1 overflow-hidden">
        <Outlet />
      </div>
    </div>
  );
}

export function ChatRoute() {
  const { id } = useParams<{ id: string }>();
  if (!id) return null;
  return <ChatPanel project_id={id} />;
}
