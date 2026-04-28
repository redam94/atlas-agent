import { useEffect, useState } from "react";
import { codeToHtml } from "shiki";

export function CodeBlock({ code, lang }: { code: string; lang?: string }) {
  const [html, setHtml] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    codeToHtml(code, { lang: lang ?? "text", theme: "github-light" })
      .then((out) => { if (!cancelled) setHtml(out); })
      .catch(() => { if (!cancelled) setHtml(null); });
    return () => { cancelled = true; };
  }, [code, lang]);

  if (html === null) {
    return (
      <pre role="region" aria-label="code" className="rounded-md bg-muted p-3 text-xs overflow-x-auto">
        <code>{code}</code>
      </pre>
    );
  }
  return (
    <div
      role="region"
      aria-label="code"
      className="rounded-md overflow-x-auto text-xs [&>pre]:p-3"
      dangerouslySetInnerHTML={{ __html: html }}
    />
  );
}
