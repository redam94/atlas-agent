import { useParams } from "react-router-dom";
import { ChatPanel } from "@/components/chat/chat-panel";

export function ProjectRoute() {
  const { id } = useParams<{ id: string }>();
  if (!id) return null;
  return <ChatPanel project_id={id} />;
}
