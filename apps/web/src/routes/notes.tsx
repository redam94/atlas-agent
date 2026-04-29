import { Outlet } from "react-router-dom";
import { NoteListRail } from "@/components/notes/note-list-rail";
import { NoteEmpty } from "@/components/notes/note-empty";

export function NotesRoute() {
  return (
    <div className="flex h-full">
      <NoteListRail />
      <div className="flex-1 overflow-hidden">
        <Outlet />
      </div>
    </div>
  );
}

export function NotesIndex() {
  return <NoteEmpty />;
}
