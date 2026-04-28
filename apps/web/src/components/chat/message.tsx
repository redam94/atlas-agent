import type { ChatMessage } from "@/hooks/use-atlas-chat";
import { cn } from "@/lib/cn";

export function Message({ msg }: { msg: ChatMessage }) {
  const isUser = msg.role === "user";
  return (
    <div className={cn("flex w-full", isUser ? "justify-end" : "justify-start")}>
      <div
        className={cn(
          "max-w-[80%] whitespace-pre-wrap rounded-lg px-4 py-2 text-sm",
          isUser ? "bg-primary text-primary-foreground" : "bg-muted",
        )}
      >
        {msg.content || (msg.role === "assistant" && !msg.finalized ? "…" : "")}
      </div>
    </div>
  );
}
