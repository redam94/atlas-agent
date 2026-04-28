import { useState } from "react";
import { useProject } from "@/hooks/use-projects";
import { useAtlasChat } from "@/hooks/use-atlas-chat";
import { useAtlasStore } from "@/stores/atlas-store";
import { getOrCreateSessionId } from "@/lib/session-storage";
import { MessageList } from "./message-list";
import { Composer } from "./composer";

export function ChatPanel({ project_id }: { project_id: string }) {
  const { data: project, isLoading } = useProject(project_id);
  const session_id = getOrCreateSessionId(project_id);
  const selected_model = useAtlasStore((s) => s.models.selected_id_per_session[session_id]);
  const [ingestOpen, setIngestOpen] = useState(false);

  const chat = useAtlasChat({
    session_id,
    project_id,
    model_id: selected_model,
  });

  if (isLoading || !project) {
    return <div className="p-6 text-sm text-muted-foreground">Loading…</div>;
  }

  return (
    <div className="flex h-full flex-col">
      <div className="flex h-12 items-center justify-between border-b px-4">
        <div className="font-medium">{project.name}</div>
        {/* RAG drawer toggle button mounts here in Task F1. */}
      </div>
      <div className="flex-1 overflow-y-auto">
        <MessageList messages={chat.messages} />
        {chat.error && (
          <div className="mx-4 mb-4 rounded-md border border-destructive/50 bg-destructive/10 p-3 text-sm text-destructive">
            {chat.error.message}
          </div>
        )}
      </div>
      <Composer
        session_id={session_id}
        default_model={project.default_model}
        is_streaming={chat.is_streaming}
        onSend={chat.send}
        onOpenIngest={() => setIngestOpen(true)}
      />
      {/* IngestModal mounts here in Task G2; for now, the Add button is a no-op. */}
      {ingestOpen && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/50"
          onClick={() => setIngestOpen(false)}
        >
          <div className="rounded-md bg-background p-6 text-sm">Ingest UI lands in Task G2.</div>
        </div>
      )}
    </div>
  );
}
