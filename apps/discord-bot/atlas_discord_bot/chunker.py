"""Split text into Discord-safe chunks (≤1900 chars) at paragraph/sentence boundaries."""

from __future__ import annotations

MAX_CHUNK = 1900


def chunk_text(text: str, max_len: int = MAX_CHUNK) -> list[str]:
    """Split text into chunks of at most max_len chars.

    Prefers splitting at double-newline (paragraph), then single newline,
    then period+space, then falls back to hard truncation.
    """
    if not text:
        return []
    if len(text) <= max_len:
        return [text]

    chunks: list[str] = []
    remaining = text
    while remaining:
        if len(remaining) <= max_len:
            chunks.append(remaining)
            break
        # Try paragraph boundary
        idx = remaining.rfind("\n\n", 0, max_len)
        if idx > 0:
            chunks.append(remaining[: idx + 2].rstrip())
            remaining = remaining[idx + 2 :].lstrip()
            continue
        # Try line boundary
        idx = remaining.rfind("\n", 0, max_len)
        if idx > 0:
            chunks.append(remaining[:idx].rstrip())
            remaining = remaining[idx + 1 :].lstrip()
            continue
        # Try sentence boundary
        idx = remaining.rfind(". ", 0, max_len)
        if idx > 0:
            chunks.append(remaining[: idx + 1].rstrip())
            remaining = remaining[idx + 2 :].lstrip()
            continue
        # Hard split
        chunks.append(remaining[:max_len])
        remaining = remaining[max_len:]
    return [c for c in chunks if c]
