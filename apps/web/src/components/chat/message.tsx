import { useState } from "react";
import { Copy, Check } from "lucide-react";
import type { ChatMessage } from "@/hooks/use-atlas-chat";
import { cn } from "@/lib/cn";
import { MarkdownRenderer } from "./markdown/markdown-renderer";
import { ToolUseCard } from "./tool-use/tool-use-card";
import { ToolCallChip } from "./tool-call-chip";

export function Message({ msg }: { msg: ChatMessage }) {
  const isUser = msg.role === "user";
  const empty = !msg.content && msg.role === "assistant" && !msg.finalized;
  const [copied, setCopied] = useState(false);

  const onCopy = async () => {
    try {
      await navigator.clipboard.writeText(msg.content);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      // older browsers w/o clipboard API
    }
  };

  return (
    <div className={cn("group flex w-full", isUser ? "justify-end" : "justify-start")}>
      <div
        className={cn(
          "relative max-w-[80%] rounded-lg px-4 py-2 text-sm",
          isUser ? "bg-primary text-primary-foreground whitespace-pre-wrap" : "bg-muted",
        )}
      >
        {isUser ? (
          msg.content
        ) : empty ? (
          <span className="opacity-50">…</span>
        ) : (
          <>
            {msg.toolCalls && msg.toolCalls.length > 0 && (
              <div className="mb-2 flex flex-wrap gap-1">
                {msg.toolCalls.map((tc) => (
                  <ToolCallChip
                    key={tc.callId}
                    toolName={tc.toolName}
                    status={tc.status}
                    durationMs={tc.durationMs}
                  />
                ))}
              </div>
            )}
            <MarkdownRenderer source={msg.content} />
            {msg.tool_cards?.map((c) => <ToolUseCard key={c.id} card={c} />)}
          </>
        )}
        {msg.content && (
          <button
            onClick={onCopy}
            className={cn(
              "absolute -top-2 right-2 rounded-md border bg-background p-1 opacity-0 group-hover:opacity-100 transition",
            )}
            aria-label="Copy message"
          >
            {copied ? <Check className="h-3 w-3" /> : <Copy className="h-3 w-3" />}
          </button>
        )}
      </div>
    </div>
  );
}
