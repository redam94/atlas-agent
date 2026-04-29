"""NerExtractor — extracts typed entities from chunk text via an LM Studio HTTP call.

LM Studio speaks the OpenAI chat-completions API and supports
``response_format: json_schema`` for structured output. We send one request per
chunk in parallel via asyncio.gather, retry once on 5xx / malformed-JSON, and
raise NerFailure if either retry also fails.
"""
from __future__ import annotations

import asyncio
import json
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final
from uuid import UUID

import httpx
import structlog

log = structlog.get_logger("atlas.graph.ner")

ENTITY_TYPES: Final[tuple[str, ...]] = (
    "CLIENT",
    "METHOD",
    "METRIC",
    "TOOL",
    "PERSON",
    "ORG",
    "LOCATION",
    "TIME_PERIOD",
    "INDUSTRY",
    "CONTACT_INFO",
    "DATA_SOURCE",
)


@dataclass(frozen=True)
class Entity:
    name: str
    type: str


class NerFailure(RuntimeError):
    """Raised when NER fails after the single allowed retry."""


_SYSTEM_PROMPT = """\
You extract typed entities from consulting documents. Return strict JSON matching the schema.

Types:
- CLIENT: companies the author works with or about (e.g. "CircleK", "Wendy's").
- METHOD: methodologies, frameworks, techniques (e.g. "geo lift", "MMM", "incrementality testing").
- METRIC: KPIs, financial measures (e.g. "CAC", "ROAS", "LTV").
- TOOL: software, platforms, vendors (e.g. "Snowflake", "GA4"). If the same name is referenced as a *data source*, prefer DATA_SOURCE.
- PERSON: individuals named in the text.
- ORG: non-client organizations (vendors, agencies, regulators).
- LOCATION: geographic context (e.g. "EMEA", "California").
- TIME_PERIOD: named time windows (e.g. "Q3 2025", "2024 holiday season").
- INDUSTRY: sectors (e.g. "QSR", "DTC retail").
- CONTACT_INFO: emails, phone numbers, addresses.
- DATA_SOURCE: datasets, public data sources, third-party panels (e.g. "Nielsen panel", "Census 2020").

Rules:
- Skip generic words. Only entities that would be useful as graph nodes for retrieval.
- Each entity ONE type only.
- Return at most 20 entities.
- Order by importance (most central concept first).
"""


_RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "entities": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "type": {"type": "string", "enum": list(ENTITY_TYPES)},
                },
                "required": ["name", "type"],
            },
        }
    },
    "required": ["entities"],
}


class NerExtractor:
    def __init__(
        self,
        *,
        client: httpx.AsyncClient,
        base_url: str,
        max_entities: int,
        request_timeout: float = 30.0,
    ) -> None:
        self._client = client
        self._base_url = base_url.rstrip("/")
        self._max_entities = max_entities
        self._timeout = request_timeout

    async def extract_batch(
        self, chunks: Sequence[tuple[UUID, str]]
    ) -> dict[UUID, list[Entity]]:
        results = await asyncio.gather(
            *(self._extract_one(text) for _, text in chunks),
            return_exceptions=False,
        )
        return {chunk_id: ents for (chunk_id, _), ents in zip(chunks, results, strict=True)}

    async def _extract_one(self, text: str) -> list[Entity]:
        payload = {
            "model": "ner",  # LM Studio ignores model name when one is loaded
            "messages": [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": text},
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": "entities", "schema": _RESPONSE_SCHEMA},
            },
        }
        last_err: Exception | None = None
        for attempt in range(2):
            try:
                resp = await self._client.post(
                    f"{self._base_url}/chat/completions",
                    json=payload,
                    timeout=self._timeout,
                )
                if resp.status_code >= 500:
                    raise NerFailure(f"LM Studio HTTP {resp.status_code}")
                if resp.status_code >= 400:
                    raise httpx.HTTPStatusError(
                        f"HTTP {resp.status_code}",
                        request=None,  # type: ignore
                        response=resp,
                    )
                content = resp.json()["choices"][0]["message"]["content"]
                parsed = json.loads(content)
                return self._validate(parsed.get("entities", []))
            except (NerFailure, httpx.HTTPError, json.JSONDecodeError, KeyError) as e:
                log.warning("ner.attempt_failed", attempt=attempt, error=str(e))
                last_err = e
        raise NerFailure(f"NER failed after retry: {last_err}")

    def _validate(self, raw: list[dict]) -> list[Entity]:
        out: list[Entity] = []
        valid_types = set(ENTITY_TYPES)
        for item in raw[: self._max_entities]:
            name = (item.get("name") or "").strip()
            etype = item.get("type")
            if not name or etype not in valid_types:
                continue
            out.append(Entity(name=name, type=etype))
        return out
