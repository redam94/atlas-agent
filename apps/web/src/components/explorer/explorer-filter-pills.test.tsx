import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, beforeEach } from "vitest";
import { useExplorerStore } from "@/stores/explorer-store";
import { ExplorerFilterPills } from "./explorer-filter-pills";

describe("ExplorerFilterPills", () => {
  beforeEach(() => useExplorerStore.getState().reset());

  it("renders three pills, all selected by default", () => {
    render(<ExplorerFilterPills />);
    for (const label of ["Document", "Chunk", "Entity"]) {
      const pill = screen.getByRole("button", { name: label });
      expect(pill).toHaveAttribute("aria-pressed", "true");
    }
  });

  it("clicking a pill toggles its visibility in the store", async () => {
    render(<ExplorerFilterPills />);
    await userEvent.click(screen.getByRole("button", { name: "Chunk" }));
    expect(useExplorerStore.getState().visibleTypes.has("Chunk")).toBe(false);
    await userEvent.click(screen.getByRole("button", { name: "Chunk" }));
    expect(useExplorerStore.getState().visibleTypes.has("Chunk")).toBe(true);
  });
});
