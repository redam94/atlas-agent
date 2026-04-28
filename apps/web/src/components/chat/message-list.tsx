import { useEffect, useRef } from "react";
import { Message } from "./message";
import type { ChatMessage } from "@/hooks/use-atlas-chat";

export function MessageList({ messages }: { messages: ChatMessage[] }) {
  const bottomRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages]);

  if (messages.length === 0) {
    return (
      <div className="flex h-full items-center justify-center text-sm text-muted-foreground">
        Start a conversation by typing below.
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-3 p-4">
      {messages.map((m) => (
        <Message key={m.client_id} msg={m} />
      ))}
      <div ref={bottomRef} />
    </div>
  );
}
