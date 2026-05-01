"""Tests for the text chunker."""

from atlas_discord_bot.chunker import MAX_CHUNK, chunk_text


def test_short_text_single_chunk():
    result = chunk_text("hello world")
    assert result == ["hello world"]


def test_empty_text_returns_empty_list():
    assert chunk_text("") == []


def test_exactly_max_len_is_single_chunk():
    text = "a" * MAX_CHUNK
    assert chunk_text(text) == [text]


def test_over_max_splits_at_paragraph():
    para = "word " * 400  # well over 1900 chars
    text = para.strip() + "\n\n" + "second paragraph"
    chunks = chunk_text(text)
    assert len(chunks) >= 2
    assert all(len(c) <= MAX_CHUNK for c in chunks)
    assert "second paragraph" in chunks[-1]


def test_over_max_splits_at_newline():
    line = "x" * 950
    text = line + "\n" + line + "\n" + line
    chunks = chunk_text(text)
    assert all(len(c) <= MAX_CHUNK for c in chunks)


def test_over_max_splits_at_sentence():
    sentence = "This is a sentence. " * 100
    chunks = chunk_text(sentence)
    assert all(len(c) <= MAX_CHUNK for c in chunks)


def test_over_max_hard_split_fallback():
    text = "x" * (MAX_CHUNK * 3)
    chunks = chunk_text(text)
    assert all(len(c) <= MAX_CHUNK for c in chunks)
    assert "".join(chunks) == text


def test_chunks_reassemble_to_original_content():
    import random
    import string
    random.seed(42)
    text = " ".join("".join(random.choices(string.ascii_lowercase, k=8)) for _ in range(500))
    chunks = chunk_text(text)
    assert all(len(c) <= MAX_CHUNK for c in chunks)
    assert len(chunks) > 1
