import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { ToolCallChip } from "./tool-call-chip";

describe("ToolCallChip", () => {
  it("renders pending state with spinner and tool name", () => {
    render(<ToolCallChip toolName="fake.echo" status="pending" />);
    expect(screen.getByText("fake.echo")).toBeInTheDocument();
    expect(screen.getByLabelText(/calling tool/i)).toBeInTheDocument();
  });

  it("renders ok state with check and duration", () => {
    render(<ToolCallChip toolName="fake.echo" status="ok" durationMs={234} />);
    expect(screen.getByText("fake.echo")).toBeInTheDocument();
    expect(screen.getByText(/234.?ms/i)).toBeInTheDocument();
  });

  it("renders error state with X and duration", () => {
    render(<ToolCallChip toolName="fake.fail" status="error" durationMs={50} />);
    expect(screen.getByText("fake.fail")).toBeInTheDocument();
    expect(screen.getByLabelText(/tool failed/i)).toBeInTheDocument();
  });
});
