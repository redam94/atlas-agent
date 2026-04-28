import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { Plus } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { useCreateProject } from "@/hooks/use-projects";

export function NewProjectModal() {
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [defaultModel, setDefaultModel] = useState("claude-sonnet-4-6");
  const create = useCreateProject();
  const navigate = useNavigate();

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!name.trim()) return;
    const project = await create.mutateAsync({
      name: name.trim(),
      description: description.trim() || undefined,
      default_model: defaultModel,
    });
    setOpen(false);
    setName("");
    setDescription("");
    navigate(`/projects/${project.id}`);
  };

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button variant="ghost" size="sm" className="w-full justify-start">
          <Plus className="h-4 w-4" />
          New Project
        </Button>
      </DialogTrigger>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>New Project</DialogTitle>
          <DialogDescription>Projects scope your knowledge and conversations.</DialogDescription>
        </DialogHeader>
        <form onSubmit={submit} className="space-y-4">
          <div className="space-y-2">
            <Label htmlFor="np-name">Name</Label>
            <Input
              id="np-name"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="CircleK MMM"
              required
              autoFocus
            />
          </div>
          <div className="space-y-2">
            <Label htmlFor="np-description">Description</Label>
            <Textarea
              id="np-description"
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              placeholder="Optional notes about scope or stakeholders"
            />
          </div>
          <div className="space-y-2">
            <Label htmlFor="np-model">Default model</Label>
            <Input
              id="np-model"
              value={defaultModel}
              onChange={(e) => setDefaultModel(e.target.value)}
              placeholder="claude-sonnet-4-6"
              required
            />
          </div>
          {create.isError && (
            <div className="text-sm text-destructive">
              {create.error instanceof Error ? create.error.message : "Failed to create project"}
            </div>
          )}
          <DialogFooter>
            <Button type="button" variant="ghost" onClick={() => setOpen(false)}>
              Cancel
            </Button>
            <Button type="submit" disabled={create.isPending || !name.trim()}>
              {create.isPending ? "Creating…" : "Create"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
