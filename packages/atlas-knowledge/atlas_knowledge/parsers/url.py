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
from urllib.parse import urlparse

_ALLOWED_SCHEMES = {"http", "https"}


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
