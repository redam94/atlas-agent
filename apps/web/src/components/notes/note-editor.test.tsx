import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi, afterEach } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { NoteEditor } from "./note-editor";

function makeNote(overrides = {}) {
  return {
    id: "n1",
    user_id: "matt",
    project_id: "p1",
    knowledge_node_id: null,
    title: "Test",
    body_markdown: "hello",
    mention_entity_ids: [],
    indexed_at: null,
    created_at: "2026-04-29T10:00:00Z",
    updated_at: "2026-04-29T10:00:00Z",
    ...overrides,
  };
}

function renderEditor() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={["/projects/p1/notes/n1"]}>
        <Routes>
          <Route path="/projects/:id/notes/:noteId" element={<NoteEditor />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

afterEach(() => vi.unstubAllGlobals());

describe("NoteEditor", () => {
  it("shows the title in the input", async () => {
    vi.stubGlobal("fetch", vi.fn(() =>
      Promise.resolve({
        ok: true, status: 200,
        json: () => Promise.resolve(makeNote({ title: "Hello" })),
      } as unknown as Response)));
    renderEditor();
    const input = await screen.findByDisplayValue("Hello");
    expect(input).toBeInTheDocument();
  });

  it("shows 'Saved' badge when note is loaded and not dirty", async () => {
    vi.stubGlobal("fetch", vi.fn(() =>
      Promise.resolve({
        ok: true, status: 200,
        json: () => Promise.resolve(makeNote()),
      } as unknown as Response)));
    renderEditor();
    expect(await screen.findByText(/saved/i)).toBeInTheDocument();
  });

  it("shows 'Indexed' when indexed_at >= updated_at", async () => {
    const note = makeNote({
      updated_at: "2026-04-29T10:00:00Z",
      indexed_at: "2026-04-29T10:00:01Z",
    });
    vi.stubGlobal("fetch", vi.fn(() =>
      Promise.resolve({
        ok: true, status: 200,
        json: () => Promise.resolve(note),
      } as unknown as Response)));
    renderEditor();
    expect(await screen.findByText(/^indexed$/i)).toBeInTheDocument();
  });

  it("shows 'Indexed (stale)' when updated_at > indexed_at", async () => {
    const note = makeNote({
      updated_at: "2026-04-29T11:00:00Z",
      indexed_at: "2026-04-29T10:00:00Z",
    });
    vi.stubGlobal("fetch", vi.fn(() =>
      Promise.resolve({
        ok: true, status: 200,
        json: () => Promise.resolve(note),
      } as unknown as Response)));
    renderEditor();
    expect(await screen.findByText(/indexed \(stale\)/i)).toBeInTheDocument();
  });

  it("Save & Index button appears", async () => {
    vi.stubGlobal("fetch", vi.fn(() =>
      Promise.resolve({
        ok: true, status: 200,
        json: () => Promise.resolve(makeNote()),
      } as unknown as Response)));
    renderEditor();
    expect(await screen.findByRole("button", { name: /save & index/i })).toBeInTheDocument();
  });

  it("delete button confirms before firing", async () => {
    const calls: string[] = [];
    vi.stubGlobal("fetch", vi.fn((url: string, init?: RequestInit) => {
      calls.push(`${init?.method ?? "GET"} ${url}`);
      return Promise.resolve({
        ok: true, status: init?.method === "DELETE" ? 204 : 200,
        text: () => Promise.resolve(""),
        json: () => Promise.resolve(makeNote()),
      } as unknown as Response);
    }));
    vi.stubGlobal("confirm", vi.fn(() => false));
    renderEditor();
    const del = await screen.findByRole("button", { name: /delete/i });
    fireEvent.click(del);
    await waitFor(() => expect(calls.filter((c) => c.startsWith("DELETE")).length).toBe(0));
  });
});
