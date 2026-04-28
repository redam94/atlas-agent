import { ProjectList } from "./project-list";
import { NewProjectModal } from "./new-project-modal";

export function Sidebar() {
  return (
    <aside className="flex w-64 flex-col border-r bg-muted/30">
      <div className="flex h-12 items-center px-4 font-semibold">ATLAS</div>
      <div className="flex-1 overflow-y-auto px-2 py-2">
        <div className="mb-1 px-2 text-xs uppercase tracking-wider text-muted-foreground">
          Projects
        </div>
        <ProjectList />
        <div className="mt-2">
          <NewProjectModal />
        </div>
      </div>
      <div className="border-t p-3 text-xs text-muted-foreground">⚙ Settings</div>
    </aside>
  );
}
