import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { IngestModal } from "@/components/ingest/ingest-modal";

beforeEach(() => {
  globalThis.fetch = vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    if (url.includes("/api/v1/knowledge/ingest") && !url.includes("/pdf")) {
      return new Response(
        JSON.stringify({
          id: "job-1", user_id: "matt", project_id: "p", source_type: "markdown",
          source_filename: null, status: "pending", node_ids: [], error: null,
          created_at: new Date().toISOString(), completed_at: null,
        }),
        { status: 202, headers: { "content-type": "application/json" } },
      );
    }
    if (url.includes("/api/v1/knowledge/jobs/")) {
      return new Response(
        JSON.stringify({
          id: "job-1", user_id: "matt", project_id: "p", source_type: "markdown",
          source_filename: null, status: "completed", node_ids: ["n1", "n2", "n3"], error: null,
          created_at: new Date().toISOString(), completed_at: new Date().toISOString(),
        }),
        { status: 200, headers: { "content-type": "application/json" } },
      );
    }
    return new Response("not mocked", { status: 500 });
  }) as unknown as typeof fetch;
});

afterEach(() => { vi.restoreAllMocks(); });

const wrapper = ({ children }: { children: ReactNode }) => {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return <QueryClientProvider client={qc}>{children}</QueryClientProvider>;
};

describe("IngestModal", () => {
  it("submits markdown and shows completion", async () => {
    const user = userEvent.setup();
    render(<IngestModal open onOpenChange={() => {}} project_id="p" />, { wrapper });

    const textarea = screen.getByRole("textbox", { name: /^markdown$/i });
    await user.type(textarea, "# hello");
    await user.click(screen.getByRole("button", { name: /ingest/i }));

    await waitFor(() => expect(screen.getByText(/ingested 3 chunks/i)).toBeInTheDocument());
  });

  it("preserves form state across tabs", async () => {
    const user = userEvent.setup();
    render(<IngestModal open onOpenChange={() => {}} project_id="p" />, { wrapper });

    const textarea = screen.getByRole("textbox", { name: /^markdown$/i });
    await user.type(textarea, "# hello");

    fireEvent.click(screen.getByRole("tab", { name: /pdf/i }));
    fireEvent.click(screen.getByRole("tab", { name: /markdown/i }));

    expect(screen.getByRole("textbox", { name: /^markdown$/i })).toHaveValue("# hello");
  });

  it("submits a URL and shows completion", async () => {
    // Extend the fetch mock to handle the URL endpoint, returning a completed job.
    const originalFetch = globalThis.fetch;
    globalThis.fetch = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const u = String(input);
      if (u.includes("/api/v1/knowledge/ingest/url")) {
        return new Response(
          JSON.stringify({
            id: "job-url", user_id: "matt", project_id: "p", source_type: "url",
            source_filename: "https://example.com/x", status: "pending",
            node_ids: [], error: null,
            created_at: new Date().toISOString(), completed_at: null,
          }),
          { status: 202, headers: { "content-type": "application/json" } },
        );
      }
      if (u.includes("/api/v1/knowledge/jobs/job-url")) {
        // Return completed on first poll (immediate)
        return new Response(
          JSON.stringify({
            id: "job-url", user_id: "matt", project_id: "p", source_type: "url",
            source_filename: "https://example.com/x", status: "completed",
            node_ids: ["n1", "n2"], error: null,
            created_at: new Date().toISOString(), completed_at: new Date().toISOString(),
          }),
          { status: 200, headers: { "content-type": "application/json" } },
        );
      }
      return originalFetch(input, init);
    }) as unknown as typeof fetch;

    const user = userEvent.setup();
    render(<IngestModal open onOpenChange={() => {}} project_id="p" />, { wrapper });

    await user.click(screen.getByRole("tab", { name: /url/i }));
    const input = screen.getByDisplayValue("") as HTMLInputElement;
    await user.type(input, "https://example.com/x");
    await user.click(screen.getByRole("button", { name: /ingest/i }));

    await waitFor(() => expect(screen.getByText(/ingested 2 chunks/i)).toBeInTheDocument());
  });

  it("disables ingest when the URL is empty or malformed", async () => {
    const user = userEvent.setup();
    const { unmount } = render(<IngestModal open onOpenChange={() => {}} project_id="p" />, { wrapper });

    // First test: empty URL should disable button
    await user.click(screen.getByRole("tab", { name: /url/i }));
    let ingestBtn = screen.getByRole("button", { name: /ingest/i });
    expect(ingestBtn).toBeDisabled();

    // Test malformed URL
    let input = screen.getByDisplayValue("") as HTMLInputElement;
    await user.type(input, "not a url");
    expect(ingestBtn).toBeDisabled();

    // Unmount and re-render for clean state
    unmount();
    render(<IngestModal open onOpenChange={() => {}} project_id="p" />, { wrapper });

    // Test valid URL should enable button
    await user.click(screen.getByRole("tab", { name: /url/i }));
    input = screen.getByDisplayValue("") as HTMLInputElement;
    await user.type(input, "https://example.com/article");
    ingestBtn = screen.getByRole("button", { name: /ingest/i });
    expect(ingestBtn).not.toBeDisabled();
  });
});
