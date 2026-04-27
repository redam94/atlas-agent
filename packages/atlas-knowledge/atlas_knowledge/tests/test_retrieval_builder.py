"""Tests for build_rag_context — pure renderer, no I/O."""
from datetime import UTC, datetime
from uuid import uuid4

from atlas_knowledge.models.nodes import KnowledgeNode, KnowledgeNodeType
from atlas_knowledge.models.retrieval import ScoredChunk
from atlas_knowledge.retrieval.builder import build_rag_context


def _scored(text: str, *, title: str | None = "Doc", parent_title: str | None = None, score: float = 0.8) -> ScoredChunk:
    chunk = KnowledgeNode(
        id=uuid4(),
        user_id="matt",
        project_id=uuid4(),
        type=KnowledgeNodeType.CHUNK,
        parent_id=uuid4(),
        title=title,
        text=text,
        created_at=datetime.now(UTC),
    )
    return ScoredChunk(chunk=chunk, score=score, parent_title=parent_title)


def test_build_rag_context_empty_input():
    ctx = build_rag_context([])
    assert ctx.rendered == ""
    assert ctx.citations == []


def test_build_rag_context_single_chunk():
    sc = _scored("hello world", title="Notes", score=0.91)
    ctx = build_rag_context([sc])
    assert "<source id=\"1\" title=\"Notes\">hello world</source>" in ctx.rendered
    assert ctx.rendered.startswith("<context>")
    assert "</context>" in ctx.rendered
    assert "Cite as [1]" in ctx.rendered
    assert len(ctx.citations) == 1
    cite = ctx.citations[0]
    assert cite["id"] == 1
    assert cite["title"] == "Notes"
    assert cite["score"] == 0.91
    assert cite["chunk_id"] == str(sc.chunk.id)


def test_build_rag_context_prefers_parent_title_over_chunk_title():
    sc = _scored("body", title="chunk-only", parent_title="Parent Doc")
    ctx = build_rag_context([sc])
    assert "title=\"Parent Doc\"" in ctx.rendered
    assert ctx.citations[0]["title"] == "Parent Doc"


def test_build_rag_context_falls_back_to_untitled():
    sc = _scored("body", title=None, parent_title=None)
    ctx = build_rag_context([sc])
    assert "title=\"Untitled\"" in ctx.rendered
    assert ctx.citations[0]["title"] == "Untitled"


def test_build_rag_context_xml_escapes_title_and_text():
    sc = _scored("a < b & c > d", title="Title <evil> & \"quoted\"")
    ctx = build_rag_context([sc])
    # rendered side: escaped
    assert "Title &lt;evil&gt; &amp; &quot;quoted&quot;" in ctx.rendered
    assert "a &lt; b &amp; c &gt; d" in ctx.rendered
    assert "<evil>" not in ctx.rendered
    # citations side: raw, since JSON does its own escaping
    assert ctx.citations[0]["title"] == "Title <evil> & \"quoted\""


def test_build_rag_context_assigns_contiguous_one_indexed_ids():
    chunks = [_scored(f"chunk {i}", title=f"T{i}", score=0.5 - i * 0.01) for i in range(3)]
    ctx = build_rag_context(chunks)
    ids = [c["id"] for c in ctx.citations]
    assert ids == [1, 2, 3]
    # rendered side has matching id attrs
    for i in (1, 2, 3):
        assert f"<source id=\"{i}\"" in ctx.rendered
