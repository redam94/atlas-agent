"""URL parser — Playwright fetch + Trafilatura extraction.

Public API:
    validate_url(url)        — pure, raises ValueError on reject (scheme + SSRF)
    fetch_html(url, ...)     — async, Playwright Chromium, returns rendered HTML
    parse_html(html, url)    — sync, Trafilatura → ParsedDocument
    parse_url(url)           — async facade: fetch_html + parse_html
"""
from __future__ import annotations

import ipaddress
import socket
from datetime import UTC, datetime
from urllib.parse import urlparse

import trafilatura

from atlas_knowledge.parsers.markdown import ParsedDocument

_ALLOWED_SCHEMES = {"http", "https"}
_MIN_EXTRACT_CHARS = 100


def validate_url(url: str) -> str:
    """Validate scheme + SSRF block; return the URL on success.

    Raises ValueError if the URL has a non-http(s) scheme, a malformed host,
    or resolves to a private/loopback/link-local/unspecified IP.
    """
    if not url or not isinstance(url, str):
        raise ValueError("url must be a non-empty string")

    parsed = urlparse(url)
    if parsed.scheme not in _ALLOWED_SCHEMES:
        raise ValueError(f"unsupported url scheme: {parsed.scheme!r}")
    host = parsed.hostname
    if not host:
        raise ValueError("url has no host")

    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as e:
        raise ValueError(f"could not resolve host {host!r}: {e}") from e

    for info in infos:
        sockaddr = info[4]
        ip_str = sockaddr[0]
        try:
            ip = ipaddress.ip_address(ip_str)
        except ValueError:
            continue
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_unspecified:
            raise ValueError(
                f"url {url!r} resolves to disallowed address {ip_str!r}"
            )
    return url


def parse_html(html: str, url: str) -> ParsedDocument:
    """Extract the main article body + metadata from rendered HTML.

    Raises ValueError if Trafilatura returns no usable content.
    """
    extracted = trafilatura.bare_extraction(
        html,
        output_format="markdown",
        include_tables=True,
        include_comments=False,
        include_links=False,
        with_metadata=True,
    )
    if extracted is None:
        raise ValueError("no extractable content from URL")

    # bare_extraction returns either a Document dataclass (newer trafilatura)
    # or a dict (older). Normalize to attribute access.
    def _get(name: str) -> str | None:
        if hasattr(extracted, name):
            v = getattr(extracted, name)
        elif isinstance(extracted, dict):
            v = extracted.get(name)
        else:
            v = None
        return v if isinstance(v, str) and v.strip() else None

    text = _get("text") or _get("raw_text") or ""
    if len(text) < _MIN_EXTRACT_CHARS:
        raise ValueError("no extractable content (text too short)")

    title = _get("title") or url
    metadata: dict[str, object] = {
        "source_url": url,
        "fetched_at": datetime.now(UTC).isoformat(),
    }
    for key, src in (
        ("author", "author"),
        ("published_date", "date"),
        ("site_name", "sitename"),
        ("language", "language"),
    ):
        val = _get(src)
        if val is not None:
            metadata[key] = val

    return ParsedDocument(
        text=text,
        title=title,
        source_type="url",
        metadata=metadata,
    )
