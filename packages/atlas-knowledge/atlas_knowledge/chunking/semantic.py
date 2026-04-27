"""Semantic chunker — whitespace-tokenized sliding window with paragraph snapping.

This is intentionally simple for Phase 1. It uses whitespace word counts as a
cheap proxy for tokens (BGE-small's true tokenizer would be more accurate but
adds 100ms+ overhead per document and a hard dependency on the tokenizer at
chunking time). For ATLAS at single-user scale the approximation is fine; Phase
2 can swap in a real tokenizer if retrieval quality regresses.

Strategy:
1. Split on blank lines into paragraphs (paragraph = atomic unit).
2. Greedily pack paragraphs into windows up to ``target_tokens``.
3. If a single paragraph exceeds the target, split it on word boundaries.
4. Generate ``overlap_tokens`` worth of trailing words from each chunk and
   prepend them to the next.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class Chunk:
    text: str
    index: int
    token_count: int


class SemanticChunker:
    def __init__(self, *, target_tokens: int = 512, overlap_tokens: int = 128) -> None:
        if overlap_tokens >= target_tokens:
            raise ValueError("overlap_tokens must be smaller than target_tokens")
        self.target_tokens = target_tokens
        self.overlap_tokens = overlap_tokens

    def chunk(self, text: str) -> list[Chunk]:
        if not text.strip():
            return []

        words = text.split()
        if len(words) <= self.target_tokens:
            return [Chunk(text=text.strip(), index=0, token_count=len(words))]

        # Paragraph boundaries: index of word AFTER each blank line.
        paragraph_starts = self._paragraph_start_indices(text)

        out: list[Chunk] = []
        start = 0
        idx = 0
        n = len(words)
        while start < n:
            end = min(start + self.target_tokens, n)

            # Snap end to nearest paragraph boundary within [start + 50%, end].
            snap_lo = start + self.target_tokens // 2
            candidates = [b for b in paragraph_starts if snap_lo <= b <= end]
            if candidates:
                end = candidates[-1]

            chunk_words = words[start:end]
            chunk_text = " ".join(chunk_words)
            out.append(Chunk(text=chunk_text, index=idx, token_count=len(chunk_words)))
            idx += 1

            if end >= n:
                break
            start = max(end - self.overlap_tokens, start + 1)

        return out

    @staticmethod
    def _paragraph_start_indices(text: str) -> list[int]:
        """Return word indices that begin a new paragraph (post-blank-line)."""
        starts: list[int] = []
        word_index = 0
        in_blank = False
        for token in text.split("\n"):
            if token.strip() == "":
                in_blank = True
                continue
            if in_blank:
                starts.append(word_index)
                in_blank = False
            word_index += len(token.split())
        return starts
