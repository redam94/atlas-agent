import { useRef, useState, type KeyboardEvent } from "react";
import { Send, Plus } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { ModelPicker } from "./model-picker";

export function Composer(props: {
  session_id: string;
  default_model: string;
  is_streaming: boolean;
  onSend: (text: string) => void;
  onOpenIngest: () => void;
}) {
  const [text, setText] = useState("");
  const ref = useRef<HTMLTextAreaElement>(null);

  const submit = () => {
    if (!text.trim() || props.is_streaming) return;
    props.onSend(text);
    setText("");
  };
  const onKey = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
      e.preventDefault();
      submit();
    }
  };

  return (
    <div className="border-t bg-background">
      <div className="flex items-center gap-2 px-4 pt-2">
        <ModelPicker session_id={props.session_id} default_model={props.default_model} />
        <Button variant="ghost" size="sm" onClick={props.onOpenIngest}>
          <Plus className="h-4 w-4" />
          Add
        </Button>
      </div>
      <div className="flex items-end gap-2 p-4">
        <Textarea
          ref={ref}
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={onKey}
          placeholder="Ask anything (Cmd/Ctrl+Enter to send)…"
          disabled={props.is_streaming}
          rows={3}
        />
        <Button size="icon" onClick={submit} disabled={!text.trim() || props.is_streaming}>
          <Send className="h-4 w-4" />
        </Button>
      </div>
    </div>
  );
}
