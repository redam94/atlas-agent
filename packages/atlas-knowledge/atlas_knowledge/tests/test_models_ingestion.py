"""Tests for the ingestion request/job models."""
from __future__ import annotations

from uuid import uuid4

import pytest
from pydantic import ValidationError

from atlas_knowledge.models.ingestion import (
    IngestRequest,
    SourceType,
    UrlIngestRequest,
)


def test_source_type_has_url_value():
    assert SourceType.URL == "url"
    assert SourceType("url") is SourceType.URL


def test_url_ingest_request_accepts_https_url():
    req = UrlIngestRequest(project_id=uuid4(), url="https://example.com/article")
    assert str(req.url) == "https://example.com/article"


def test_url_ingest_request_accepts_http_url():
    req = UrlIngestRequest(project_id=uuid4(), url="http://example.com/x")
    assert str(req.url).startswith("http://")


def test_url_ingest_request_rejects_non_http_scheme():
    with pytest.raises(ValidationError):
        UrlIngestRequest(project_id=uuid4(), url="ftp://example.com/x")


def test_url_ingest_request_rejects_missing_url():
    with pytest.raises(ValidationError):
        UrlIngestRequest(project_id=uuid4())  # type: ignore[call-arg]


def test_legacy_ingest_request_still_validates_markdown_text():
    # IngestRequest is unchanged; URL is a separate model.
    with pytest.raises(ValueError):
        IngestRequest(project_id=uuid4(), source_type=SourceType.MARKDOWN, text="")
