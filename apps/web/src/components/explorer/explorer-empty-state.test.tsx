import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { ExplorerEmptyState } from "./explorer-empty-state";

describe("ExplorerEmptyState", () => {
  it("renders loading variant", () => {
    render(<ExplorerEmptyState variant="loading" />);
    expect(screen.getByText(/loading/i)).toBeInTheDocument();
  });

  it("renders error variant with the message", () => {
    render(<ExplorerEmptyState variant="error" message="boom" />);
    expect(screen.getByText(/boom/)).toBeInTheDocument();
  });

  it("renders empty variant", () => {
    render(<ExplorerEmptyState variant="empty" />);
    expect(screen.getByText(/no entities/i)).toBeInTheDocument();
  });

  it("renders degraded variant with the explanation", () => {
    render(<ExplorerEmptyState variant="degraded" />);
    expect(screen.getByText(/graph data unavailable/i)).toBeInTheDocument();
  });
});
