"""Markdown parser — passthrough with simple title extraction."""

import re
from dataclasses import dataclass

_FRONT_MATTER_RE = re.compile(r"\A---\n.*?\n---\n", re.DOTALL)
_FIRST_H1_RE = re.compile(r"^# (.+?)$", re.MULTILINE)


@dataclass(frozen=True)
class ParsedDocument:
    """A parsed source document, ready to feed into the chunker."""

    text: str
    title: str
    source_type: str  # "markdown" | "pdf"
    metadata: dict[str, object]


def parse_markdown(text: str, *, title: str | None = None) -> ParsedDocument:
    """Strip optional YAML front matter, then return the body as-is.

    Title resolution: explicit ``title`` arg → first H1 in the body → "Untitled".
    """
    body = _FRONT_MATTER_RE.sub("", text, count=1)

    resolved_title = title or _extract_first_h1(body) or "Untitled"
    return ParsedDocument(
        text=body,
        title=resolved_title,
        source_type="markdown",
        metadata={},
    )


def _extract_first_h1(body: str) -> str | None:
    m = _FIRST_H1_RE.search(body)
    return m.group(1).strip() if m else None
