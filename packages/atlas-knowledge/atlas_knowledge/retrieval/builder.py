"""Render a list of retrieved ScoredChunks into a RagContext (prompt block + citations).

The rendered block is the text injected as a separate ``system`` message in the
LLM prompt. The citations list mirrors the rendered IDs and is what flows over
the ``rag.context`` WS event and persists in ``MessageORM.rag_context`` on the
assistant row.
"""
from __future__ import annotations

from xml.sax.saxutils import escape as xml_escape

from atlas_knowledge.models.retrieval import RagContext, ScoredChunk

_PROMPT_FOOTER = "\n\nUse the sources above to answer when relevant. Cite as [1], [2]."


def build_rag_context(scored: list[ScoredChunk]) -> RagContext:
    """Render scored chunks into a RagContext.

    Empty input → ``RagContext(rendered="", citations=[])`` so the WS handler
    can use a single truthiness check (``if rag_ctx.citations``) before emitting
    the event or injecting the prompt block.
    """
    if not scored:
        return RagContext(rendered="", citations=[])

    rendered_sources: list[str] = []
    citations: list[dict] = []
    for idx, sc in enumerate(scored, start=1):
        title = sc.parent_title or sc.chunk.title or "Untitled"
        # XML-escape both title (attribute) and text (element body).
        # `quotes=True`-equivalent: pass {chr(34): "&quot;"} so `"` becomes `&quot;` inside the title attribute.
        rendered_sources.append(
            f'<source id="{idx}" title="{xml_escape(title, {chr(34): "&quot;"})}">'
            f"{xml_escape(sc.chunk.text)}"
            f"</source>"
        )
        citations.append(
            {
                "id": idx,
                "title": title,            # raw — JSON serialization handles its own escaping
                "score": sc.score,
                "chunk_id": str(sc.chunk.id),
            }
        )

    rendered = "<context>\n" + "\n".join(rendered_sources) + "\n</context>" + _PROMPT_FOOTER
    return RagContext(rendered=rendered, citations=citations)
