import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { NoteListRail } from "./note-list-rail";

function renderRail(notes: unknown[]) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  vi.stubGlobal("fetch", vi.fn(() =>
    Promise.resolve({
      ok: true,
      status: 200,
      json: () => Promise.resolve(notes),
    } as unknown as Response),
  ));
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={["/projects/p1/notes"]}>
        <Routes>
          <Route path="/projects/:id/notes" element={<NoteListRail />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("NoteListRail", () => {
  it("renders empty state when no notes", async () => {
    renderRail([]);
    expect(await screen.findByText(/no notes yet/i)).toBeInTheDocument();
  });

  it("renders note titles when notes exist", async () => {
    renderRail([
      { id: "n1", title: "First", updated_at: "2026-04-29T10:00:00Z", indexed_at: null },
      { id: "n2", title: "Second", updated_at: "2026-04-28T10:00:00Z", indexed_at: "2026-04-28T11:00:00Z" },
    ]);
    expect(await screen.findByText("First")).toBeInTheDocument();
    expect(await screen.findByText("Second")).toBeInTheDocument();
  });

  it("shows stale dot when updated_at > indexed_at", async () => {
    renderRail([
      { id: "n1", title: "Stale", updated_at: "2026-04-29T11:00:00Z", indexed_at: "2026-04-29T10:00:00Z" },
    ]);
    expect(await screen.findByLabelText(/index out of date/i)).toBeInTheDocument();
  });
});
