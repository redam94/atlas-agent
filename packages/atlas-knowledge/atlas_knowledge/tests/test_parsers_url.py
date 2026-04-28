"""Unit tests for the URL parser — no browser, no network."""
from __future__ import annotations

import socket

import pytest

from atlas_knowledge.parsers.url import validate_url


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
