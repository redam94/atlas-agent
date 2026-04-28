import { Outlet } from "react-router-dom";
import { Sidebar } from "@/components/sidebar/sidebar";

export function App() {
  return (
    <div className="flex h-screen">
      <Sidebar />
      <main className="flex flex-1 flex-col overflow-hidden">
        <Outlet />
      </main>
    </div>
  );
}
