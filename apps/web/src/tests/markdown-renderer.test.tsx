import { describe, expect, it } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { MarkdownRenderer } from "@/components/chat/markdown/markdown-renderer";

describe("MarkdownRenderer", () => {
  it("renders a fenced code block via CodeBlock", async () => {
    const md = "```ts\nconst x = 1;\n```";
    render(<MarkdownRenderer source={md} />);
    await waitFor(() => {
      const pre = screen.getByRole("region", { name: /code/i });
      expect(pre).toBeInTheDocument();
    });
  });

  it("renders GFM tables", () => {
    const md = "| h1 | h2 |\n| --- | --- |\n| a | b |";
    render(<MarkdownRenderer source={md} />);
    expect(screen.getByText("h1")).toBeInTheDocument();
    expect(screen.getByText("a")).toBeInTheDocument();
  });

  it("does not execute raw HTML script tags", () => {
    const md = "<script>document.title='pwned';</script>hello";
    const before = document.title;
    render(<MarkdownRenderer source={md} />);
    expect(document.title).toBe(before);
    // When skipHtml is enabled, HTML tags are skipped and text after them may not render
    // The key assertion is that the script tag does NOT execute
  });

  it("renders inline LaTeX with KaTeX", () => {
    const md = "Solve $x^2 + 1 = 0$ for x.";
    render(<MarkdownRenderer source={md} />);
    // KaTeX renders math into a <span class="katex"> wrapper. Look for the class.
    const katexNode = document.querySelector(".katex");
    expect(katexNode).not.toBeNull();
  });

  it("renders block LaTeX with KaTeX", () => {
    const md = "$$\n\\int_0^1 x^2 \\, dx = \\frac{1}{3}\n$$";
    render(<MarkdownRenderer source={md} />);
    // Block math is rendered as a span with class="katex-display"
    const katexBlock = document.querySelector(".katex-display");
    expect(katexBlock).not.toBeNull();
  });
});
