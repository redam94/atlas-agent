"""Unit tests for the URL parser — no browser, no network."""
from __future__ import annotations

import socket
from pathlib import Path

import pytest

from atlas_knowledge.parsers.url import (
    _MAX_HTML_BYTES,
    _check_html_size,
    parse_html,
    validate_url,
)


def _fake_getaddrinfo(host_to_ip: dict[str, str]):
    """Build a getaddrinfo replacement that returns one IPv4 result per host."""

    def fake(host, port, *args, **kwargs):
        ip = host_to_ip[host]
        # mimic socket.getaddrinfo return shape
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port or 0))]

    return fake


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com/article",
        "http://example.com/path?q=1",
        "https://news.example.com:8443/x",
    ],
)
def test_validate_url_accepts_public_http_urls(monkeypatch, url):
    monkeypatch.setattr(socket, "getaddrinfo", _fake_getaddrinfo({
        "example.com": "93.184.216.34",
        "news.example.com": "1.1.1.1",
    }))
    assert validate_url(url) == url


@pytest.mark.parametrize(
    "url",
    [
        "ftp://example.com/x",
        "file:///etc/passwd",
        "javascript:alert(1)",
        "://no-scheme",
        "",
        "https:///path",   # valid scheme, empty host → "url has no host"
    ],
)
def test_validate_url_rejects_non_http_or_malformed(url):
    with pytest.raises(ValueError):
        validate_url(url)


@pytest.mark.parametrize(
    "ip",
    [
        "10.0.0.1",        # RFC1918
        "172.16.0.1",      # RFC1918
        "192.168.1.1",     # RFC1918
        "127.0.0.1",       # loopback
        "169.254.1.1",     # link-local
        "0.0.0.0",         # unspecified
    ],
)
def test_validate_url_rejects_private_ipv4(monkeypatch, ip):
    monkeypatch.setattr(
        socket, "getaddrinfo", _fake_getaddrinfo({"target.example": ip})
    )
    with pytest.raises(ValueError):
        validate_url("https://target.example/x")


@pytest.mark.parametrize(
    "ip",
    [
        "::1",              # loopback
        "fc00::1",          # unique local
        "fe80::1",          # link-local
    ],
)
def test_validate_url_rejects_private_ipv6(monkeypatch, ip):
    def fake(host, port, *args, **kwargs):
        return [(socket.AF_INET6, socket.SOCK_STREAM, 6, "", (ip, port or 0, 0, 0))]

    monkeypatch.setattr(socket, "getaddrinfo", fake)
    with pytest.raises(ValueError):
        validate_url("https://target.example/x")


def test_validate_url_rejects_unresolvable_host(monkeypatch):
    def fake(host, port, *args, **kwargs):
        raise socket.gaierror("name or service not known")

    monkeypatch.setattr(socket, "getaddrinfo", fake)
    with pytest.raises(ValueError):
        validate_url("https://does-not-resolve.example/")


_FIXTURES = Path(__file__).parent / "fixtures"


def _read_fixture(name: str) -> str:
    return (_FIXTURES / name).read_text(encoding="utf-8")


def test_parse_html_extracts_basic_article():
    html = _read_fixture("article_basic.html")
    doc = parse_html(html, "https://blog.example.com/geo-lift")
    assert doc.source_type == "url"
    assert "Geo-Lift" in doc.title
    assert "Geo-lift studies measure incrementality" in doc.text
    assert doc.metadata["source_url"] == "https://blog.example.com/geo-lift"
    assert "fetched_at" in doc.metadata
    # Author/date/site_name are best-effort; assert they round-trip when present.
    assert doc.metadata.get("author") in ("Jane Doe", None)


def test_parse_html_includes_table_text():
    html = _read_fixture("article_with_table.html")
    doc = parse_html(html, "https://blog.example.com/q3")
    # Trafilatura output_format=markdown emits table cells as text — both region
    # names and CAC values should appear.
    assert "NA" in doc.text and "$42" in doc.text
    assert "EU" in doc.text and "$51" in doc.text


def test_parse_html_rejects_empty_or_tiny_content():
    html = _read_fixture("spa_shell.html")
    with pytest.raises(ValueError) as excinfo:
        parse_html(html, "https://app.example.com/")
    assert "no extractable content" in str(excinfo.value).lower()


def test_parse_html_falls_back_to_url_when_no_title():
    # A page with no <title> and no metadata title should fall back to the URL.
    html = "<html><body><article><p>" + ("alpha beta " * 80) + "</p></article></body></html>"
    doc = parse_html(html, "https://no-title.example.com/path")
    assert doc.title == "https://no-title.example.com/path"
    assert "alpha beta" in doc.text


def test_parse_html_rejects_garbage_input():
    # Trafilatura returns None for non-HTML garbage; parse_html should surface
    # it as ValueError (the "extracted is None" branch), not AttributeError.
    with pytest.raises(ValueError, match="no extractable content"):
        parse_html("not html at all just random words", "https://example.com/")


def test_check_html_size_accepts_under_cap():
    # Anything well under the cap passes silently.
    _check_html_size("<html>" + ("x" * 1024) + "</html>")


def test_check_html_size_rejects_over_cap():
    oversized = "x" * (_MAX_HTML_BYTES + 1)
    with pytest.raises(ValueError, match="exceeds size cap"):
        _check_html_size(oversized)


def test_check_html_size_counts_utf8_bytes_not_characters():
    # A 4-byte-per-char emoji string under the char cap but over the byte cap
    # should be rejected. _MAX_HTML_BYTES // 4 + 1 emojis = byte_count > cap.
    char_count = _MAX_HTML_BYTES // 4 + 1
    payload = "🎉" * char_count  # 4 UTF-8 bytes each
    assert len(payload) < _MAX_HTML_BYTES  # fewer characters than the byte cap
    with pytest.raises(ValueError, match="exceeds size cap"):
        _check_html_size(payload)
