import { useState, useEffect } from "react";
import { Library } from "lucide-react";
import { useProject } from "@/hooks/use-projects";
import { useAtlasChat } from "@/hooks/use-atlas-chat";
import { useAtlasStore } from "@/stores/atlas-store";
import { getOrCreateSessionId } from "@/lib/session-storage";
import { MessageList } from "./message-list";
import { Composer } from "./composer";
import { Button } from "@/components/ui/button";
import { RagDrawer } from "@/components/rag/rag-drawer";
import { IngestModal } from "@/components/ingest/ingest-modal";

export function ChatPanel({ project_id }: { project_id: string }) {
  const { data: project, isLoading } = useProject(project_id);
  const session_id = getOrCreateSessionId(project_id);
  const selected_model = useAtlasStore((s) => s.models.selected_id_per_session[session_id]);
  const [ingestOpen, setIngestOpen] = useState(false);

  const ragOpen = useAtlasStore((s) => s.ui.rag_drawer_open);
  const setRagOpen = useAtlasStore((s) => s.setRagDrawerOpen);
  const autoOpened = useAtlasStore((s) => s.ui.rag_drawer_auto_opened_for_session[session_id]);
  const markAutoOpened = useAtlasStore((s) => s.markRagDrawerAutoOpened);

  const chat = useAtlasChat({
    session_id,
    project_id,
    model_id: selected_model,
  });

  useEffect(() => {
    if (chat.rag_context && chat.rag_context.length > 0 && !autoOpened) {
      setRagOpen(true);
      markAutoOpened(session_id);
    }
  }, [chat.rag_context, autoOpened, session_id, setRagOpen, markAutoOpened]);

  if (isLoading || !project) {
    return <div className="p-6 text-sm text-muted-foreground">Loading…</div>;
  }

  return (
    <div className="flex h-full flex-col">
      <div className="flex h-12 items-center justify-between border-b px-4">
        <div className="font-medium">{project.name}</div>
        <Button variant="ghost" size="sm" onClick={() => setRagOpen(!ragOpen)}>
          <Library className="h-4 w-4" />
          Sources {chat.rag_context ? `(${chat.rag_context.length})` : ""}
        </Button>
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
      <IngestModal open={ingestOpen} onOpenChange={setIngestOpen} project_id={project_id} />
      <RagDrawer open={ragOpen} citations={chat.rag_context} onOpenChange={setRagOpen} />
    </div>
  );
}
