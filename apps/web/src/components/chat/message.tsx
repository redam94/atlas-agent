import type { ChatMessage } from "@/hooks/use-atlas-chat";
import { cn } from "@/lib/cn";
import { MarkdownRenderer } from "./markdown/markdown-renderer";

export function Message({ msg }: { msg: ChatMessage }) {
  const isUser = msg.role === "user";
  const empty = !msg.content && msg.role === "assistant" && !msg.finalized;
  return (
    <div className={cn("flex w-full", isUser ? "justify-end" : "justify-start")}>
      <div
        className={cn(
          "max-w-[80%] rounded-lg px-4 py-2 text-sm",
          isUser ? "bg-primary text-primary-foreground whitespace-pre-wrap" : "bg-muted",
        )}
      >
        {isUser ? msg.content : empty ? <span className="opacity-50">…</span> : <MarkdownRenderer source={msg.content} />}
      </div>
    </div>
  );
}
