import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { createBrowserRouter, RouterProvider } from "react-router-dom";
import { App } from "./App";
import { IndexRoute } from "./routes/home";
import { ChatRoute, ProjectShell } from "./routes/project";
import { ExplorerRoute } from "./routes/explorer";
import { NoteEditor } from "./components/notes/note-editor";
import { NotesIndex, NotesRoute } from "./routes/notes";
import "./index.css";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: { staleTime: 30_000, refetchOnWindowFocus: false },
  },
});

const router = createBrowserRouter([
  {
    path: "/",
    element: <App />,
    children: [
      { index: true, element: <IndexRoute /> },
      {
        path: "projects/:id",
        element: <ProjectShell />,
        children: [
          { index: true, element: <ChatRoute /> },
          { path: "explorer", element: <ExplorerRoute /> },
          {
            path: "notes",
            element: <NotesRoute />,
            children: [
              { index: true, element: <NotesIndex /> },
              { path: ":noteId", element: <NoteEditor /> },
            ],
          },
        ],
      },
    ],
  },
]);

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>
  </StrictMode>,
);
