import { describe, expect, it } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { RagDrawer } from "@/components/rag/rag-drawer";
import type { Citation } from "@/lib/ws-protocol";

const cites: Citation[] = [
  { id: 1, title: "Source A", score: 0.91, chunk_id: "a1", text_preview: "alpha preview text" },
  { id: 2, title: "Source B", score: 0.72, chunk_id: "b2", text_preview: "beta preview text" },
];

describe("RagDrawer", () => {
  it("renders empty state when no citations", () => {
    render(<RagDrawer open citations={null} onOpenChange={() => {}} />);
    expect(screen.getByText(/no sources/i)).toBeInTheDocument();
  });

  it("renders one card per citation with title, score, and preview", () => {
    render(<RagDrawer open citations={cites} onOpenChange={() => {}} />);
    expect(screen.getByText("Source A")).toBeInTheDocument();
    expect(screen.getByText("Source B")).toBeInTheDocument();
    expect(screen.getByText(/0\.91/)).toBeInTheDocument();
    expect(screen.getByText(/alpha preview/)).toBeInTheDocument();
  });

  it("expands chunk preview on click", () => {
    render(<RagDrawer open citations={cites} onOpenChange={() => {}} />);
    const card = screen.getByText("Source A").closest("[data-citation-card]")!;
    fireEvent.click(card);
    expect(card).toHaveAttribute("data-expanded", "true");
  });
});
