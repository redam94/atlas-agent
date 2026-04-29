"""NerExtractor unit tests — LM Studio client mocked at the httpx layer."""
from __future__ import annotations

import json
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest

from atlas_graph.ingestion.ner import (
    ENTITY_TYPES,
    Entity,
    NerExtractor,
    NerFailure,
)


def _ok_response(entities: list[dict]) -> httpx.Response:
    body = {
        "choices": [
            {
                "message": {
                    "content": json.dumps({"entities": entities}),
                }
            }
        ]
    }
    request = httpx.Request("POST", "http://lms.local/v1/chat/completions")
    return httpx.Response(200, json=body, request=request)


@pytest.mark.asyncio
async def test_extract_batch_returns_entities_per_chunk():
    client = AsyncMock(spec=httpx.AsyncClient)
    client.post.side_effect = [
        _ok_response([{"name": "CircleK", "type": "CLIENT"}]),
        _ok_response([{"name": "MMM", "type": "METHOD"}]),
    ]
    extractor = NerExtractor(client=client, base_url="http://lms.local/v1", max_entities=20)

    cid_a, cid_b = uuid4(), uuid4()
    out = await extractor.extract_batch([(cid_a, "we worked with CircleK"), (cid_b, "MMM applied")])
    assert out == {
        cid_a: [Entity(name="CircleK", type="CLIENT")],
        cid_b: [Entity(name="MMM", type="METHOD")],
    }


@pytest.mark.asyncio
async def test_extract_batch_enforces_20_cap():
    """If LLM returns more than max_entities, only the first N are kept."""
    client = AsyncMock(spec=httpx.AsyncClient)
    too_many = [{"name": f"E{i}", "type": "METHOD"} for i in range(50)]
    client.post.return_value = _ok_response(too_many)
    extractor = NerExtractor(client=client, base_url="http://lms.local/v1", max_entities=20)

    cid = uuid4()
    out = await extractor.extract_batch([(cid, "blah")])
    assert len(out[cid]) == 20
    assert out[cid][0].name == "E0"
    assert out[cid][-1].name == "E19"


@pytest.mark.asyncio
async def test_extract_batch_filters_unknown_types_and_empty_names():
    client = AsyncMock(spec=httpx.AsyncClient)
    client.post.return_value = _ok_response([
        {"name": "CircleK", "type": "CLIENT"},
        {"name": "X", "type": "BOGUS"},
        {"name": "", "type": "METHOD"},
    ])
    extractor = NerExtractor(client=client, base_url="http://lms.local/v1", max_entities=20)

    cid = uuid4()
    out = await extractor.extract_batch([(cid, "blah")])
    assert out[cid] == [Entity(name="CircleK", type="CLIENT")]


@pytest.mark.asyncio
async def test_extract_batch_retries_on_5xx_then_succeeds():
    client = AsyncMock(spec=httpx.AsyncClient)
    request = httpx.Request("POST", "http://lms.local/v1/chat/completions")
    client.post.side_effect = [
        httpx.Response(503, request=request),
        _ok_response([{"name": "CircleK", "type": "CLIENT"}]),
    ]
    extractor = NerExtractor(client=client, base_url="http://lms.local/v1", max_entities=20)

    cid = uuid4()
    out = await extractor.extract_batch([(cid, "blah")])
    assert out[cid] == [Entity(name="CircleK", type="CLIENT")]
    assert client.post.call_count == 2


@pytest.mark.asyncio
async def test_extract_batch_raises_after_second_failure():
    client = AsyncMock(spec=httpx.AsyncClient)
    request = httpx.Request("POST", "http://lms.local/v1/chat/completions")
    client.post.side_effect = [httpx.Response(500, request=request), httpx.Response(500, request=request)]
    extractor = NerExtractor(client=client, base_url="http://lms.local/v1", max_entities=20)

    with pytest.raises(NerFailure):
        await extractor.extract_batch([(uuid4(), "blah")])


@pytest.mark.asyncio
async def test_extract_batch_raises_on_persistent_malformed_json():
    client = AsyncMock(spec=httpx.AsyncClient)
    request = httpx.Request("POST", "http://lms.local/v1/chat/completions")
    bad = httpx.Response(200, json={"choices": [{"message": {"content": "not-json"}}]}, request=request)
    client.post.side_effect = [bad, bad]
    extractor = NerExtractor(client=client, base_url="http://lms.local/v1", max_entities=20)

    with pytest.raises(NerFailure):
        await extractor.extract_batch([(uuid4(), "blah")])


def test_entity_types_contains_all_eleven():
    """Drift protection: design lists exactly these eleven types."""
    expected = {
        "CLIENT", "METHOD", "METRIC", "TOOL", "PERSON", "ORG",
        "LOCATION", "TIME_PERIOD", "INDUSTRY", "CONTACT_INFO", "DATA_SOURCE",
    }
    assert set(ENTITY_TYPES) == expected
