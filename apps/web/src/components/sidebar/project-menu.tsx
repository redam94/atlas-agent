import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { MoreHorizontal } from "lucide-react";
import {
  DropdownMenu,
  DropdownMenuTrigger,
  DropdownMenuContent,
  DropdownMenuItem,
} from "@/components/ui/dropdown-menu";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogFooter,
  DialogTitle,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { useUpdateProject, useDeleteProject, type Project } from "@/hooks/use-projects";

export function ProjectMenu({ project }: { project: Project }) {
  const [renameOpen, setRenameOpen] = useState(false);
  const [deleteOpen, setDeleteOpen] = useState(false);
  const [name, setName] = useState(project.name);
  const update = useUpdateProject(project.id);
  const remove = useDeleteProject();
  const navigate = useNavigate();

  const submitRename = async (e: React.FormEvent) => {
    e.preventDefault();
    await update.mutateAsync({ name: name.trim() });
    setRenameOpen(false);
  };
  const confirmDelete = async () => {
    await remove.mutateAsync(project.id);
    setDeleteOpen(false);
    navigate("/");
  };

  return (
    <>
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <button
            className="opacity-0 group-hover:opacity-100 hover:bg-accent rounded p-1"
            onClick={(e) => e.stopPropagation()}
          >
            <MoreHorizontal className="h-3.5 w-3.5" />
          </button>
        </DropdownMenuTrigger>
        <DropdownMenuContent>
          <DropdownMenuItem onSelect={() => setRenameOpen(true)}>Rename</DropdownMenuItem>
          <DropdownMenuItem
            className="text-destructive focus:text-destructive"
            onSelect={() => setDeleteOpen(true)}
          >
            Delete
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>

      <Dialog open={renameOpen} onOpenChange={setRenameOpen}>
        <DialogContent>
          <DialogHeader><DialogTitle>Rename project</DialogTitle></DialogHeader>
          <form onSubmit={submitRename} className="space-y-4">
            <Input value={name} onChange={(e) => setName(e.target.value)} autoFocus />
            <DialogFooter>
              <Button variant="ghost" type="button" onClick={() => setRenameOpen(false)}>Cancel</Button>
              <Button type="submit" disabled={update.isPending}>Save</Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>

      <Dialog open={deleteOpen} onOpenChange={setDeleteOpen}>
        <DialogContent>
          <DialogHeader><DialogTitle>Archive "{project.name}"?</DialogTitle></DialogHeader>
          <p className="text-sm text-muted-foreground">
            This is a soft delete — the project is hidden from the list but the data is preserved.
          </p>
          <DialogFooter>
            <Button variant="ghost" onClick={() => setDeleteOpen(false)}>Cancel</Button>
            <Button variant="destructive" onClick={confirmDelete} disabled={remove.isPending}>
              {remove.isPending ? "Archiving…" : "Archive"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}
