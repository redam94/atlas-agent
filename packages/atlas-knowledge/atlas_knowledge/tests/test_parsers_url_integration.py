"""Integration tests for the URL parser — requires Chromium installed.

Skipped unless ATLAS_RUN_PLAYWRIGHT_TESTS=1. Spins up a tiny aiohttp server on
127.0.0.1 and points the parser at it (after locally bypassing validate_url's
loopback rejection — that branch is covered by the unit tests already).
"""
from __future__ import annotations

import os

import pytest

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("ATLAS_RUN_PLAYWRIGHT_TESTS") != "1",
        reason="set ATLAS_RUN_PLAYWRIGHT_TESTS=1 to enable",
    ),
]


_HTML = """
<!doctype html>
<html><head><title>Hello Integration</title></head>
<body><main><article>
<h1>Hello Integration</h1>
<p>This is an integration test served from a local aiohttp app. It contains
enough text — well over a hundred characters — to satisfy the parser's minimum
extracted content threshold without raising ValueError.</p>
<p>Second paragraph for additional body weight.</p>
</article></main></body></html>
"""


@pytest.mark.asyncio
async def test_fetch_and_parse_local_url():
    from aiohttp import web

    from atlas_knowledge.parsers import url as url_module

    async def handler(_request):
        return web.Response(text=_HTML, content_type="text/html")

    app = web.Application()
    app.router.add_get("/article", handler)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    try:
        # Look up the bound port. aiohttp's site doesn't expose it directly;
        # use the runner's server connections.
        port = site._server.sockets[0].getsockname()[1]  # type: ignore[attr-defined]
        target = f"http://127.0.0.1:{port}/article"

        # parse_url calls fetch_html only — there's no validate_url in the
        # call chain. (Router-level validation rejects loopback in production.)
        doc = await url_module.parse_url(target)
        assert "Hello Integration" in doc.title
        assert "integration test served from a local aiohttp app" in doc.text
    finally:
        await runner.cleanup()
