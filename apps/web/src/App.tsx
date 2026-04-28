import { Outlet } from "react-router-dom";

export function App() {
  return (
    <div className="flex h-screen">
      <aside className="w-64 border-r bg-muted/30 p-4">
        <div className="font-semibold">ATLAS</div>
        {/* Sidebar will be replaced in Task C2. */}
      </aside>
      <main className="flex-1 overflow-hidden">
        <Outlet />
      </main>
    </div>
  );
}
