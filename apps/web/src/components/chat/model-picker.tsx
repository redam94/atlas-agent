import { useEffect } from "react";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { useModels } from "@/hooks/use-models";
import { useAtlasStore } from "@/stores/atlas-store";

export function ModelPicker({ session_id, default_model }: { session_id: string; default_model: string }) {
  const { data, isLoading } = useModels();
  const selected = useAtlasStore((s) => s.models.selected_id_per_session[session_id]);
  const setSelected = useAtlasStore((s) => s.setSelectedModel);

  useEffect(() => {
    if (!selected && default_model) setSelected(session_id, default_model);
  }, [selected, default_model, session_id, setSelected]);

  if (isLoading) return <div className="text-xs text-muted-foreground">Loading models…</div>;

  return (
    <Select value={selected} onValueChange={(v) => setSelected(session_id, v)}>
      <SelectTrigger className="h-8 w-[260px] text-xs">
        <SelectValue placeholder="Select model" />
      </SelectTrigger>
      <SelectContent>
        {data?.map((m) => (
          <SelectItem key={`${m.provider}:${m.model_id}`} value={m.model_id}>
            {m.provider} — {m.model_id}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  );
}
