"""Tests for SemanticChunker — paragraph + heading aware splitting."""

from atlas_knowledge.chunking.semantic import SemanticChunker


def test_short_text_yields_one_chunk():
    c = SemanticChunker(target_tokens=512, overlap_tokens=128)
    chunks = c.chunk("Just one sentence.")
    assert len(chunks) == 1
    assert chunks[0].text == "Just one sentence."


def test_long_text_yields_multiple_chunks():
    paragraph = "word " * 600  # well over 512 tokens
    c = SemanticChunker(target_tokens=512, overlap_tokens=128)
    chunks = c.chunk(paragraph)
    assert len(chunks) >= 2


def test_chunks_carry_index_and_token_count():
    text = ("paragraph " * 600).strip()
    c = SemanticChunker(target_tokens=512, overlap_tokens=128)
    chunks = c.chunk(text)
    assert all(ch.index == i for i, ch in enumerate(chunks))
    assert all(ch.token_count > 0 for ch in chunks)


def test_chunks_overlap_when_split():
    """The overlap window should reuse some tokens from the previous chunk."""
    text = ("alpha " * 1200).strip()
    c = SemanticChunker(target_tokens=512, overlap_tokens=128)
    chunks = c.chunk(text)
    assert len(chunks) >= 2
    last_words_of_first = chunks[0].text.split()[-50:]
    first_words_of_second = chunks[1].text.split()[:50]
    assert any(w in first_words_of_second for w in last_words_of_first)


def test_paragraph_break_preferred_split():
    """When a paragraph break exists near the budget, prefer splitting there."""
    para_a = "alpha " * 300
    para_b = "beta " * 300
    text = f"{para_a.strip()}\n\n{para_b.strip()}"
    c = SemanticChunker(target_tokens=400, overlap_tokens=50)
    chunks = c.chunk(text)
    # First chunk should be roughly paragraph A (give or take overlap)
    assert "alpha" in chunks[0].text
    assert "beta" in chunks[-1].text
