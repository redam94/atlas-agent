"""Tests for the EmbeddingService ABC + FakeEmbedder used downstream."""

import pytest

from atlas_knowledge.embeddings import EmbeddingService, FakeEmbedder


def test_embedding_service_is_abstract():
    with pytest.raises(TypeError):
        EmbeddingService()  # type: ignore[abstract]


async def test_fake_embedder_embeds_documents():
    e = FakeEmbedder(dim=16)
    vectors = await e.embed_documents(["hello", "world", "hello"])
    assert len(vectors) == 3
    assert all(len(v) == 16 for v in vectors)
    # Same input → same output (deterministic)
    assert vectors[0] == vectors[2]


async def test_fake_embedder_embeds_query_consistent_with_documents():
    e = FakeEmbedder(dim=16)
    [doc_vec] = await e.embed_documents(["hello"])
    query_vec = await e.embed_query("hello")
    assert doc_vec == query_vec


async def test_fake_embedder_dim_default():
    e = FakeEmbedder()
    [v] = await e.embed_documents(["x"])
    assert len(v) == 16  # default dim


async def test_fake_embedder_model_id():
    e = FakeEmbedder()
    assert e.model_id == "fake-embedder"
