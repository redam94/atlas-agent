import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, beforeEach, vi } from "vitest";
import { useExplorerStore } from "@/stores/explorer-store";
import { ExplorerSearchBar } from "./explorer-search-bar";

describe("ExplorerSearchBar", () => {
  beforeEach(() => useExplorerStore.getState().reset());

  it("typing updates store query and pressing Enter calls onSubmit with the value", async () => {
    const onSubmit = vi.fn();
    render(<ExplorerSearchBar onSubmit={onSubmit} />);
    const input = screen.getByRole("textbox");
    await userEvent.type(input, "hello");
    expect(useExplorerStore.getState().query).toBe("hello");
    await userEvent.type(input, "{Enter}");
    expect(onSubmit).toHaveBeenCalledWith("hello");
  });

  it("clear button resets query and fires onClear", async () => {
    const onClear = vi.fn();
    render(<ExplorerSearchBar onSubmit={() => {}} onClear={onClear} />);
    const input = screen.getByRole("textbox");
    await userEvent.type(input, "abc");
    await userEvent.click(screen.getByRole("button", { name: /clear/i }));
    expect(useExplorerStore.getState().query).toBe("");
    expect(onClear).toHaveBeenCalled();
  });
});
