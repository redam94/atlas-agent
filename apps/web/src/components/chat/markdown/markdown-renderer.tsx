import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { CodeBlock } from "./code-block";

export function MarkdownRenderer({ source }: { source: string }) {
  return (
    <div className="prose prose-sm max-w-none [&_p]:my-1 [&_pre]:my-2">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        skipHtml
        components={{
          code(props) {
            const { children, className, ...rest } = props;
            const match = /language-(\w+)/.exec(className ?? "");
            const text = String(children ?? "").replace(/\n$/, "");
            if (match) return <CodeBlock code={text} lang={match[1]} />;
            return (
              <code className="rounded bg-muted px-1 py-0.5 text-xs" {...rest}>
                {children}
              </code>
            );
          },
        }}
      >
        {source}
      </ReactMarkdown>
    </div>
  );
}
