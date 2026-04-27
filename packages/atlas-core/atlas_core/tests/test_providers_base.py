"""Tests for atlas_core.providers.base + the FakeProvider used in WS tests."""
import pytest

from atlas_core.models.llm import ModelEventType
from atlas_core.providers import BaseModel, FakeProvider, ProviderError


def test_provider_error_carries_code_and_message():
    err = ProviderError(code="rate_limit", message="too many")
    assert err.code == "rate_limit"
    assert "too many" in str(err)


def test_base_model_is_abstract():
    with pytest.raises(TypeError):
        BaseModel()  # type: ignore[abstract]


async def test_fake_provider_streams_tokens_then_done():
    fp = FakeProvider(model_id="fake-1", token_chunks=["hello", " ", "world"])
    events = []
    async for ev in fp.stream(messages=[{"role": "user", "content": "hi"}]):
        events.append(ev)
    types = [e.type for e in events]
    assert types == [
        ModelEventType.TOKEN,
        ModelEventType.TOKEN,
        ModelEventType.TOKEN,
        ModelEventType.DONE,
    ]
    final = events[-1]
    assert final.data["usage"]["input_tokens"] >= 0


async def test_fake_provider_can_be_configured_to_error():
    fp = FakeProvider(model_id="fake-1", token_chunks=[], error_on_call=True)
    events = []
    async for ev in fp.stream(messages=[]):
        events.append(ev)
    assert any(e.type == ModelEventType.ERROR for e in events)
