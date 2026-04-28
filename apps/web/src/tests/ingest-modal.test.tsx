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
});
