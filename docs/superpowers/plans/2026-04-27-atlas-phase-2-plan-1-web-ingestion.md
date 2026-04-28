# ATLAS Phase 2 — Plan 1: Web/URL Ingestion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add URL ingestion: paste a URL into the IngestModal, the api fetches it via headless Chromium, extracts the article via Trafilatura, and pipes the result through the existing chunk → embed → vector-store pipeline. No graph touch.

**Architecture:** New parser module in `atlas_knowledge.parsers.url` with a split fetch/parse API (`fetch_html` async + `parse_html` sync, behind an `async parse_url` facade). New router `POST /api/v1/knowledge/ingest/url` validates URL + runs an SSRF guard, then awaits `parse_url`, then calls the existing `IngestionService.ingest(...)`. New "URL" tab in `IngestModal` plus a `useStartUrlIngest()` hook. Playwright Chromium baked into the api Docker image (~200 MB tax accepted in the spec).

**Tech Stack:**
- Backend: Python 3.13, `playwright>=1.45,<2`, `trafilatura>=1.12,<2`, FastAPI, pydantic v2 `HttpUrl`, pytest (existing markers + fixtures), `socket.getaddrinfo` + `ipaddress` for the SSRF guard.
- Frontend: React 19 + TypeScript strict, TanStack Query v5, existing shadcn `Tabs`/`Input`/`Button`, Vitest + React Testing Library + jsdom.
- Infra: Docker, single-stage api image; `uv run playwright install --with-deps chromium`.

**Authoritative spec:** `docs/superpowers/specs/2026-04-27-atlas-phase-2-knowledge-graph-design.md` §5.1.
**Per-plan design:** `docs/superpowers/plans/2026-04-27-atlas-phase-2-plan-1-web-ingestion-design.md`.
**Branch:** `feat/phase-2-plan-1-web-ingestion` (already checked out; design committed).

**Important contract details discovered during planning** (verify with `grep` if needed):
- `KnowledgeNodeORM.source_type` and `IngestionJobORM.source_type` are SQLAlchemy `Text`, not Postgres enums (`apps/api/.../alembic/versions/0003_add_knowledge_nodes_and_ingestion_jobs.py:81`). Adding `SourceType.URL = "url"` is a Pydantic-only change — **no alembic migration**.
- The existing `IngestRequest` validator only requires `text` when `source_type == MARKDOWN`. Leaving it untouched is fine; URL gets its own `UrlIngestRequest` model.
- `IngestionService.ingest(...)` takes `source_type: str` (a comment says `"markdown" | "pdf"`). Passing `"url"` works; only the docstring/comment needs widening.
- Test fixtures: `db_session` and `app_client` come from `/Users/redam94/Coding/Projects/atlas-agent/conftest.py`. Knowledge router tests use `fake_knowledge_stack` + `app_with_knowledge_overrides` (see `apps/api/atlas_api/tests/test_knowledge_router.py`); reuse these.
- The existing `IngestModal` test file at `apps/web/src/tests/ingest-modal.test.tsx` mocks `globalThis.fetch`. Add the `/ingest/url` mock to that block.
- `apps/api/Dockerfile` is a single-stage image based on `ghcr.io/astral-sh/uv:python3.13-bookworm-slim`. The `playwright install --with-deps chromium` step runs after `uv sync` succeeds because Playwright is installed via `uv sync` first.
- `bare_extraction` from Trafilatura returns a `Document` dataclass with `.text`, `.title`, `.author`, `.date`, `.sitename`, `.language`. Use `with_metadata=True` and ask for `output_format="markdown"` so the body is markdown-shaped (matching how chunker treats markdown).

---

## File Map

**Backend additions:**
- Create: `packages/atlas-knowledge/atlas_knowledge/parsers/url.py`
- Create: `packages/atlas-knowledge/atlas_knowledge/tests/test_parsers_url.py`
- Create: `packages/atlas-knowledge/atlas_knowledge/tests/test_parsers_url_integration.py`
- Create: `packages/atlas-knowledge/atlas_knowledge/tests/fixtures/article_basic.html`
- Create: `packages/atlas-knowledge/atlas_knowledge/tests/fixtures/article_with_table.html`
- Create: `packages/atlas-knowledge/atlas_knowledge/tests/fixtures/spa_shell.html`

**Backend modifications:**
- Modify: `packages/atlas-knowledge/pyproject.toml` (add `playwright`, `trafilatura` deps)
- Modify: `packages/atlas-knowledge/atlas_knowledge/parsers/__init__.py` (re-export `parse_url`, `parse_html`, `fetch_html`, `validate_url`)
- Modify: `packages/atlas-knowledge/atlas_knowledge/models/ingestion.py` (add `SourceType.URL`, new `UrlIngestRequest`)
- Modify: `packages/atlas-knowledge/atlas_knowledge/tests/test_models_ingestion.py` *(create if absent — see Task 2 for which file to use)*
- Modify: `packages/atlas-knowledge/atlas_knowledge/ingestion/service.py` (one-line docstring/comment widen: `"markdown" | "pdf"` → `"markdown" | "pdf" | "url"`)
- Modify: `apps/api/atlas_api/routers/knowledge.py` (new `ingest_url_endpoint` handler)
- Modify: `apps/api/atlas_api/tests/test_knowledge_router.py` (router tests for the new endpoint)
- Modify: `apps/api/Dockerfile` (add `RUN uv run playwright install --with-deps chromium`)
- Modify: `pyproject.toml` (root) — pytest marker registration for `integration`

**Frontend modifications:**
- Modify: `apps/web/src/hooks/use-ingest-job.ts` (add `useStartUrlIngest`; widen `IngestionJob.source_type`)
- Modify: `apps/web/src/components/ingest/ingest-modal.tsx` (URL tab + state + submit branch)
- Modify: `apps/web/src/tests/ingest-modal.test.tsx` (URL tab tests)

**No migrations.** **No graph touch.**

---

## Verification baseline

Before starting Task 1, confirm the suite passes on `feat/phase-2-plan-1-web-ingestion`:

```bash
uv run pytest -q
cd apps/web && pnpm test --run && cd -
```

Expected: all green. If something is already red on this branch, stop and surface it before proceeding — Plan 1 should not be committed onto a broken baseline.

---

## Task 1: Add Playwright + Trafilatura deps and write `validate_url` (SSRF guard)

**Files:**
- Modify: `packages/atlas-knowledge/pyproject.toml`
- Create: `packages/atlas-knowledge/atlas_knowledge/parsers/url.py`
- Create: `packages/atlas-knowledge/atlas_knowledge/tests/test_parsers_url.py`

The SSRF guard is the most-isolated piece — pure function, network-free unit tests via DNS monkeypatch. Land it first so we have something tested before touching async code.

- [ ] **Step 1: Add deps to `packages/atlas-knowledge/pyproject.toml`**

Replace the `dependencies = [...]` block with:

```toml
dependencies = [
    "atlas-core",
    "pydantic>=2.10",
    "sentence-transformers>=3.3",
    "chromadb>=0.5",
    "pymupdf>=1.25",
    "anyio>=4.6",
    "playwright>=1.45,<2",
    "trafilatura>=1.12,<2",
]
```

Then sync:

```bash
uv sync --all-packages
```

Expected: lock updates; the `playwright` and `trafilatura` packages appear in `uv.lock`. Do **not** run `playwright install` locally yet — that comes later (Task 8). The Python deps are enough for the unit tests in this task.

- [ ] **Step 2: Write the failing tests for `validate_url`**

Create `packages/atlas-knowledge/atlas_knowledge/tests/test_parsers_url.py` with:

```python
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
        "example.com": "203.0.113.1",
        "news.example.com": "203.0.113.2",
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
```

- [ ] **Step 3: Run the tests, confirm they fail with ImportError**

```bash
uv run pytest packages/atlas-knowledge/atlas_knowledge/tests/test_parsers_url.py -v
```

Expected: collection error or all-red — `from atlas_knowledge.parsers.url import validate_url` raises `ModuleNotFoundError`.

- [ ] **Step 4: Implement `validate_url`**

Create `packages/atlas-knowledge/atlas_knowledge/parsers/url.py` with:

```python
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
```

- [ ] **Step 5: Re-export from `parsers/__init__.py`**

Read `packages/atlas-knowledge/atlas_knowledge/parsers/__init__.py` first to see the current pattern. Add:

```python
from atlas_knowledge.parsers.url import validate_url  # noqa: F401
```

(Add additional re-exports — `fetch_html`, `parse_html`, `parse_url` — in Tasks 2-3 as those land.)

- [ ] **Step 6: Run the tests, confirm they pass**

```bash
uv run pytest packages/atlas-knowledge/atlas_knowledge/tests/test_parsers_url.py -v
```

Expected: all green.

- [ ] **Step 7: Run the full atlas-knowledge suite to confirm no regressions**

```bash
uv run pytest packages/atlas-knowledge -q
```

Expected: all green.

- [ ] **Step 8: Commit**

```bash
git add packages/atlas-knowledge/pyproject.toml \
        packages/atlas-knowledge/atlas_knowledge/parsers/__init__.py \
        packages/atlas-knowledge/atlas_knowledge/parsers/url.py \
        packages/atlas-knowledge/atlas_knowledge/tests/test_parsers_url.py \
        uv.lock
git commit -m "feat(knowledge): add validate_url with SSRF guard and Playwright/Trafilatura deps"
```

---

## Task 2: `parse_html` (Trafilatura, sync, fixture-driven)

**Files:**
- Create: `packages/atlas-knowledge/atlas_knowledge/tests/fixtures/article_basic.html`
- Create: `packages/atlas-knowledge/atlas_knowledge/tests/fixtures/article_with_table.html`
- Create: `packages/atlas-knowledge/atlas_knowledge/tests/fixtures/spa_shell.html`
- Modify: `packages/atlas-knowledge/atlas_knowledge/parsers/url.py`
- Modify: `packages/atlas-knowledge/atlas_knowledge/tests/test_parsers_url.py`
- Modify: `packages/atlas-knowledge/atlas_knowledge/parsers/__init__.py`

`parse_html` is sync, deterministic, and unit-testable on raw HTML. Land it before Playwright.

- [ ] **Step 1: Create the HTML fixtures**

Create `packages/atlas-knowledge/atlas_knowledge/tests/fixtures/article_basic.html`:

```html
<!doctype html>
<html lang="en">
<head>
  <title>Geo-Lift Methodology — Example Blog</title>
  <meta name="author" content="Jane Doe">
  <meta name="date" content="2025-09-15">
  <meta property="og:site_name" content="Example Blog">
</head>
<body>
  <header><nav>home about</nav></header>
  <main>
    <article>
      <h1>Geo-Lift Methodology</h1>
      <p>Geo-lift studies measure incrementality by withholding marketing in test geographies and comparing them against synthetic controls. The technique avoids cookie-based attribution problems entirely.</p>
      <p>The two pieces that matter most are control selection and the post-period measurement window. We typically use the GeoLift R package for both.</p>
      <p>This is a long enough article body that the parser should not flag it as empty content. Lorem ipsum dolor sit amet, consectetur adipiscing elit, sed do eiusmod tempor incididunt ut labore et dolore magna aliqua.</p>
    </article>
  </main>
  <footer><p>(c) Example Blog</p></footer>
</body>
</html>
```

Create `packages/atlas-knowledge/atlas_knowledge/tests/fixtures/article_with_table.html`:

```html
<!doctype html>
<html lang="en"><head><title>Q3 Results</title></head>
<body><main><article>
  <h1>Q3 Results</h1>
  <p>Summary of the quarter's performance across three regions. The table below captures CAC by region.</p>
  <table>
    <thead><tr><th>Region</th><th>CAC</th></tr></thead>
    <tbody>
      <tr><td>NA</td><td>$42</td></tr>
      <tr><td>EU</td><td>$51</td></tr>
      <tr><td>APAC</td><td>$38</td></tr>
    </tbody>
  </table>
  <p>Conclusion: NA and APAC remained efficient; EU saw a step-change driven by competitive entry. We will revisit allocation in Q4 planning. Lorem ipsum dolor sit amet consectetur adipiscing elit.</p>
</article></main></body></html>
```

Create `packages/atlas-knowledge/atlas_knowledge/tests/fixtures/spa_shell.html` — a JS-shell page Trafilatura should reject:

```html
<!doctype html>
<html><head><title>Loading…</title></head>
<body>
  <div id="root"></div>
  <script>console.log("hi")</script>
</body></html>
```

- [ ] **Step 2: Add failing tests for `parse_html`**

Append to `packages/atlas-knowledge/atlas_knowledge/tests/test_parsers_url.py`:

```python
from pathlib import Path

from atlas_knowledge.parsers.url import parse_html

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


def test_parse_html_falls_back_to_url_when_no_title(monkeypatch):
    # A page with no <title> and no metadata title should fall back to the URL.
    html = "<html><body><article><p>" + ("alpha beta " * 80) + "</p></article></body></html>"
    doc = parse_html(html, "https://no-title.example.com/path")
    assert doc.title == "https://no-title.example.com/path"
    assert "alpha beta" in doc.text
```

- [ ] **Step 3: Run the tests, confirm they fail**

```bash
uv run pytest packages/atlas-knowledge/atlas_knowledge/tests/test_parsers_url.py -v
```

Expected: the new tests fail with `ImportError` on `parse_html`.

- [ ] **Step 4: Implement `parse_html`**

Append to `packages/atlas-knowledge/atlas_knowledge/parsers/url.py`:

```python
from datetime import UTC, datetime

import trafilatura

from atlas_knowledge.parsers.markdown import ParsedDocument

_MIN_EXTRACT_CHARS = 100


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
```

- [ ] **Step 5: Re-export `parse_html`**

Add to `packages/atlas-knowledge/atlas_knowledge/parsers/__init__.py`:

```python
from atlas_knowledge.parsers.url import parse_html  # noqa: F401
```

- [ ] **Step 6: Run the tests, confirm they pass**

```bash
uv run pytest packages/atlas-knowledge/atlas_knowledge/tests/test_parsers_url.py -v
```

Expected: all green. If a `published_date` assertion is loose (Trafilatura sometimes returns dates in unexpected formats), the test only asserts `in (..., None)` so this is tolerant.

- [ ] **Step 7: Run the full atlas-knowledge suite**

```bash
uv run pytest packages/atlas-knowledge -q
```

Expected: all green.

- [ ] **Step 8: Commit**

```bash
git add packages/atlas-knowledge/atlas_knowledge/parsers/url.py \
        packages/atlas-knowledge/atlas_knowledge/parsers/__init__.py \
        packages/atlas-knowledge/atlas_knowledge/tests/test_parsers_url.py \
        packages/atlas-knowledge/atlas_knowledge/tests/fixtures/article_basic.html \
        packages/atlas-knowledge/atlas_knowledge/tests/fixtures/article_with_table.html \
        packages/atlas-knowledge/atlas_knowledge/tests/fixtures/spa_shell.html
git commit -m "feat(knowledge): add parse_html with Trafilatura extraction and metadata"
```

---

## Task 3: `fetch_html` + `parse_url` facade with opt-in integration test

**Files:**
- Modify: `packages/atlas-knowledge/atlas_knowledge/parsers/url.py`
- Modify: `packages/atlas-knowledge/atlas_knowledge/parsers/__init__.py`
- Create: `packages/atlas-knowledge/atlas_knowledge/tests/test_parsers_url_integration.py`
- Modify: `pyproject.toml` (root, register pytest marker)

The async fetch wraps Playwright with a 30s wall-clock timeout and a 5s soft `networkidle` cap. The integration test is opt-in (skipped by default) so the regular suite runs without Chromium installed.

- [ ] **Step 1: Register the `integration` pytest marker**

Read `/Users/redam94/Coding/Projects/atlas-agent/pyproject.toml` and find the `[tool.pytest.ini_options]` section. Add `markers` if absent (or extend the existing list):

```toml
[tool.pytest.ini_options]
markers = [
    "integration: opt-in integration test (requires external services / browsers)",
]
```

If a `markers = [...]` already exists, append the entry rather than overwriting. If `[tool.pytest.ini_options]` does not exist, create it.

- [ ] **Step 2: Implement `fetch_html` and `parse_url`**

Append to `packages/atlas-knowledge/atlas_knowledge/parsers/url.py`:

```python
import asyncio


_NETWORKIDLE_TIMEOUT_MS = 5000


async def fetch_html(url: str, *, timeout_s: float = 30.0) -> str:
    """Render `url` with headless Chromium and return the rendered HTML.

    Per-request browser: launches and closes one Chromium instance per call.
    Hard wall-clock timeout via asyncio.wait_for; soft networkidle wait so
    SPAs that never go idle still return after the DOM is ready.
    """
    # Late import so the module imports cheaply when the API isn't ingesting.
    from playwright.async_api import (
        TimeoutError as PlaywrightTimeoutError,
        async_playwright,
    )

    async def _do() -> str:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            try:
                ctx = await browser.new_context(
                    user_agent=(
                        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/124.0.0.0 Safari/537.36"
                    )
                )
                page = await ctx.new_page()
                await page.goto(url, wait_until="domcontentloaded")
                try:
                    await page.wait_for_load_state(
                        "networkidle", timeout=_NETWORKIDLE_TIMEOUT_MS
                    )
                except PlaywrightTimeoutError:
                    pass  # SPA never goes idle; current DOM is good enough.
                return await page.content()
            finally:
                await browser.close()

    return await asyncio.wait_for(_do(), timeout=timeout_s)


async def parse_url(url: str) -> ParsedDocument:
    """Public facade: fetch_html then parse_html."""
    html = await fetch_html(url)
    return parse_html(html, url)
```

- [ ] **Step 3: Re-export `fetch_html` and `parse_url`**

Update `packages/atlas-knowledge/atlas_knowledge/parsers/__init__.py`:

```python
from atlas_knowledge.parsers.url import (  # noqa: F401
    fetch_html,
    parse_html,
    parse_url,
    validate_url,
)
```

- [ ] **Step 4: Add the opt-in integration test**

Create `packages/atlas-knowledge/atlas_knowledge/tests/test_parsers_url_integration.py`:

```python
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
```

This test imports `aiohttp`. Confirm it is already in the dev dep tree by running:

```bash
uv run python -c "import aiohttp; print(aiohttp.__version__)"
```

Expected: prints a version. If `ModuleNotFoundError`, add `aiohttp` to the root `[dependency-groups].dev` (or whichever dev group exists — check `pyproject.toml`) and re-`uv sync`.

- [ ] **Step 5: Confirm the integration test is skipped by default**

```bash
uv run pytest packages/atlas-knowledge/atlas_knowledge/tests/test_parsers_url_integration.py -v
```

Expected: 1 skipped (because `ATLAS_RUN_PLAYWRIGHT_TESTS` is unset).

- [ ] **Step 6: (Optional, local-only) Run the integration test if Chromium is installed**

```bash
uv run playwright install chromium  # one-time
ATLAS_RUN_PLAYWRIGHT_TESTS=1 uv run pytest \
    packages/atlas-knowledge/atlas_knowledge/tests/test_parsers_url_integration.py -v
```

Expected: 1 passed. **This step is optional** — Step 5 establishing the skip behavior is the gate. Failing here on a machine without Chromium does not block the task.

- [ ] **Step 7: Run the full atlas-knowledge suite**

```bash
uv run pytest packages/atlas-knowledge -q
```

Expected: all green; the integration test reports as skipped.

- [ ] **Step 8: Commit**

```bash
git add packages/atlas-knowledge/atlas_knowledge/parsers/url.py \
        packages/atlas-knowledge/atlas_knowledge/parsers/__init__.py \
        packages/atlas-knowledge/atlas_knowledge/tests/test_parsers_url_integration.py \
        pyproject.toml
# Also stage uv.lock if `uv sync` updated it for aiohttp.
git add uv.lock 2>/dev/null || true
git commit -m "feat(knowledge): add fetch_html + parse_url facade with opt-in integration test"
```

---

## Task 4: `SourceType.URL` + `UrlIngestRequest` model + service docstring

**Files:**
- Modify: `packages/atlas-knowledge/atlas_knowledge/models/ingestion.py`
- Modify: `packages/atlas-knowledge/atlas_knowledge/ingestion/service.py`
- Create: `packages/atlas-knowledge/atlas_knowledge/tests/test_models_ingestion.py` *(or extend existing — check first)*

- [ ] **Step 1: Check whether `test_models_ingestion.py` already exists**

```bash
ls packages/atlas-knowledge/atlas_knowledge/tests/test_models_ingestion.py 2>/dev/null
```

If the file exists, append to it. If not, create it.

- [ ] **Step 2: Write failing tests for `SourceType.URL` and `UrlIngestRequest`**

Add (or create) `packages/atlas-knowledge/atlas_knowledge/tests/test_models_ingestion.py`:

```python
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
```

- [ ] **Step 3: Run, confirm fail**

```bash
uv run pytest packages/atlas-knowledge/atlas_knowledge/tests/test_models_ingestion.py -v
```

Expected: import error on `UrlIngestRequest` and/or `SourceType.URL`.

- [ ] **Step 4: Add `SourceType.URL` and `UrlIngestRequest`**

Modify `packages/atlas-knowledge/atlas_knowledge/models/ingestion.py`:

Add `URL = "url"` to `SourceType`:

```python
class SourceType(StrEnum):
    MARKDOWN = "markdown"
    PDF = "pdf"
    URL = "url"
```

Add the new model below `IngestRequest`:

```python
from pydantic import HttpUrl  # add to imports if not already present


class UrlIngestRequest(AtlasRequestModel):
    """Payload for POST /api/v1/knowledge/ingest/url.

    Pydantic v2 HttpUrl handles scheme + structural validation; the router
    additionally runs validate_url() for the SSRF / private-IP guard.
    """

    project_id: UUID
    url: HttpUrl
```

- [ ] **Step 5: Widen the service docstring/comment**

In `packages/atlas-knowledge/atlas_knowledge/ingestion/service.py`, replace:

```python
        source_type: str,  # "markdown" | "pdf"
```

with:

```python
        source_type: str,  # "markdown" | "pdf" | "url"
```

(There may be a second occurrence in a docstring — leave the docstring alone unless it contradicts the new value.)

- [ ] **Step 6: Run, confirm pass**

```bash
uv run pytest packages/atlas-knowledge/atlas_knowledge/tests/test_models_ingestion.py -v
uv run pytest packages/atlas-knowledge -q
```

Expected: all green.

- [ ] **Step 7: Commit**

```bash
git add packages/atlas-knowledge/atlas_knowledge/models/ingestion.py \
        packages/atlas-knowledge/atlas_knowledge/ingestion/service.py \
        packages/atlas-knowledge/atlas_knowledge/tests/test_models_ingestion.py
git commit -m "feat(knowledge): add SourceType.URL and UrlIngestRequest model"
```

---

## Task 5: Router endpoint `POST /api/v1/knowledge/ingest/url`

**Files:**
- Modify: `apps/api/atlas_api/routers/knowledge.py`
- Modify: `apps/api/atlas_api/tests/test_knowledge_router.py`

The router test mocks `parse_url` so the test is fast and deterministic; real Playwright runs only via the integration test (Task 3) and manual smoke (Task 9).

- [ ] **Step 1: Write failing router tests**

Append to `apps/api/atlas_api/tests/test_knowledge_router.py`:

```python
from unittest.mock import patch
from uuid import uuid4

from atlas_knowledge.parsers.markdown import ParsedDocument


@pytest.mark.asyncio
async def test_ingest_url_happy_path(app_with_knowledge_overrides, db_session):
    project = ProjectORM(user_id="matt", name="P", default_model="claude-sonnet-4-6")
    db_session.add(project)
    await db_session.flush()

    parsed = ParsedDocument(
        text="alpha beta " * 200,
        title="Geo-Lift Methodology",
        source_type="url",
        metadata={"source_url": "https://blog.example.com/geo-lift"},
    )

    async def fake_parse_url(_url):
        return parsed

    with patch(
        "atlas_api.routers.knowledge.parse_url",
        side_effect=fake_parse_url,
    ), patch(
        "atlas_api.routers.knowledge.validate_url",
        side_effect=lambda u: u,
    ):
        resp = await app_with_knowledge_overrides.post(
            "/api/v1/knowledge/ingest/url",
            json={
                "project_id": str(project.id),
                "url": "https://blog.example.com/geo-lift",
            },
        )

    assert resp.status_code == 202, resp.text
    body = resp.json()
    assert body["status"] == "completed"
    assert body["source_type"] == "url"
    assert body["source_filename"] == "https://blog.example.com/geo-lift"


@pytest.mark.asyncio
async def test_ingest_url_unknown_project_returns_404(app_with_knowledge_overrides):
    resp = await app_with_knowledge_overrides.post(
        "/api/v1/knowledge/ingest/url",
        json={"project_id": str(uuid4()), "url": "https://example.com/x"},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_ingest_url_invalid_scheme_returns_422(app_with_knowledge_overrides, db_session):
    project = ProjectORM(user_id="matt", name="P", default_model="claude-sonnet-4-6")
    db_session.add(project)
    await db_session.flush()

    resp = await app_with_knowledge_overrides.post(
        "/api/v1/knowledge/ingest/url",
        json={"project_id": str(project.id), "url": "ftp://example.com/x"},
    )
    # pydantic v2 HttpUrl rejects non-http schemes at the request boundary → 422.
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_ingest_url_ssrf_block_returns_400(app_with_knowledge_overrides, db_session):
    project = ProjectORM(user_id="matt", name="P", default_model="claude-sonnet-4-6")
    db_session.add(project)
    await db_session.flush()

    def boom(_url):
        raise ValueError("url resolves to disallowed address '10.0.0.1'")

    with patch("atlas_api.routers.knowledge.validate_url", side_effect=boom):
        resp = await app_with_knowledge_overrides.post(
            "/api/v1/knowledge/ingest/url",
            json={"project_id": str(project.id), "url": "https://internal.example/x"},
        )
    assert resp.status_code == 400
    assert "disallowed" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_ingest_url_fetch_failure_returns_502(app_with_knowledge_overrides, db_session):
    project = ProjectORM(user_id="matt", name="P", default_model="claude-sonnet-4-6")
    db_session.add(project)
    await db_session.flush()

    async def fake_parse_url(_url):
        raise RuntimeError("playwright nav timeout")

    with patch(
        "atlas_api.routers.knowledge.parse_url", side_effect=fake_parse_url
    ), patch(
        "atlas_api.routers.knowledge.validate_url", side_effect=lambda u: u
    ):
        resp = await app_with_knowledge_overrides.post(
            "/api/v1/knowledge/ingest/url",
            json={"project_id": str(project.id), "url": "https://example.com/x"},
        )
    assert resp.status_code == 502
    assert "fetch failed" in resp.json()["detail"].lower()


@pytest.mark.asyncio
async def test_ingest_url_extraction_empty_returns_502(app_with_knowledge_overrides, db_session):
    project = ProjectORM(user_id="matt", name="P", default_model="claude-sonnet-4-6")
    db_session.add(project)
    await db_session.flush()

    async def fake_parse_url(_url):
        raise ValueError("no extractable content from URL")

    with patch(
        "atlas_api.routers.knowledge.parse_url", side_effect=fake_parse_url
    ), patch(
        "atlas_api.routers.knowledge.validate_url", side_effect=lambda u: u
    ):
        resp = await app_with_knowledge_overrides.post(
            "/api/v1/knowledge/ingest/url",
            json={"project_id": str(project.id), "url": "https://example.com/x"},
        )
    assert resp.status_code == 502
    assert "extract" in resp.json()["detail"].lower()
```

- [ ] **Step 2: Run, confirm fail**

```bash
uv run pytest apps/api/atlas_api/tests/test_knowledge_router.py -v -k url
```

Expected: red (handler does not exist or imports fail).

- [ ] **Step 3: Implement the handler**

Modify `apps/api/atlas_api/routers/knowledge.py`. Update imports (add to the existing import block):

```python
from atlas_knowledge.models.ingestion import (
    IngestionJob,
    IngestRequest,
    SourceType,
    UrlIngestRequest,
)
from atlas_knowledge.parsers.url import parse_url, validate_url
```

Add the new endpoint between `ingest_pdf_endpoint` and `get_job`:

```python
@router.post("/knowledge/ingest/url", response_model=IngestionJob, status_code=202)
async def ingest_url_endpoint(
    payload: UrlIngestRequest,
    db: AsyncSession = Depends(get_session),
    service: IngestionService = Depends(get_ingestion_service),
    settings: AtlasConfig = Depends(get_settings),
) -> IngestionJob:
    if await db.get(ProjectORM, payload.project_id) is None:
        raise HTTPException(status_code=404, detail="project not found")
    url = str(payload.url)
    try:
        validate_url(url)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    try:
        parsed = await parse_url(url)
    except ValueError as e:
        raise HTTPException(status_code=502, detail=f"could not extract content: {e}")
    except Exception as e:  # noqa: BLE001 — Playwright errors are varied
        raise HTTPException(status_code=502, detail=f"fetch failed: {e}")
    job_id = await service.ingest(
        db=db,
        user_id=settings.user_id,
        project_id=payload.project_id,
        parsed=parsed,
        source_type="url",
        source_filename=url,
    )
    job_row = await db.get(IngestionJobORM, job_id)
    if job_row is None:
        raise HTTPException(status_code=500, detail="ingest created no job row")
    return ingestion_job_from_orm(job_row)
```

Also update the file's module docstring to add the new line:

```
POST   /api/v1/knowledge/ingest/url      Ingest a URL (Playwright + Trafilatura)
```

- [ ] **Step 4: Run, confirm pass**

```bash
uv run pytest apps/api/atlas_api/tests/test_knowledge_router.py -v
```

Expected: all router tests green (existing + 6 new).

- [ ] **Step 5: Run the full backend suite**

```bash
uv run pytest -q
```

Expected: all green.

- [ ] **Step 6: Commit**

```bash
git add apps/api/atlas_api/routers/knowledge.py \
        apps/api/atlas_api/tests/test_knowledge_router.py
git commit -m "feat(api): POST /api/v1/knowledge/ingest/url endpoint"
```

---

## Task 6: Frontend hook `useStartUrlIngest` + widen `IngestionJob.source_type`

**Files:**
- Modify: `apps/web/src/hooks/use-ingest-job.ts`

- [ ] **Step 1: Open `apps/web/src/hooks/use-ingest-job.ts` and apply the changes**

Widen the `source_type` union in the `IngestionJob` type:

```ts
source_type: "markdown" | "pdf" | "url";
```

Add a new mutation hook below `useStartPdfIngest`:

```ts
export function useStartUrlIngest() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: { project_id: string; url: string }) =>
      api.post<IngestionJob>("/api/v1/knowledge/ingest/url", body),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["ingestion-jobs"] }),
  });
}
```

- [ ] **Step 2: Type-check**

```bash
cd apps/web && pnpm exec tsc --noEmit
```

Expected: no errors.

- [ ] **Step 3: Run web tests** (this won't exercise the new hook yet — the IngestModal test does that in Task 7 — but the suite should remain green)

```bash
cd apps/web && pnpm test --run
```

Expected: all green.

- [ ] **Step 4: Commit**

```bash
cd "$(git rev-parse --show-toplevel)"
git add apps/web/src/hooks/use-ingest-job.ts
git commit -m "feat(web): add useStartUrlIngest hook"
```

---

## Task 7: Frontend URL tab in `IngestModal` + tests

**Files:**
- Modify: `apps/web/src/components/ingest/ingest-modal.tsx`
- Modify: `apps/web/src/tests/ingest-modal.test.tsx`

- [ ] **Step 1: Add failing test for the URL tab**

Append (inside the existing `describe("IngestModal", ...)` block) in `apps/web/src/tests/ingest-modal.test.tsx`:

```tsx
  it("submits a URL and shows completion", async () => {
    // Extend the fetch mock to handle the URL endpoint, returning a completed job.
    const originalFetch = globalThis.fetch;
    globalThis.fetch = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const u = String(input);
      if (u.includes("/api/v1/knowledge/ingest/url")) {
        return new Response(
          JSON.stringify({
            id: "job-url", user_id: "matt", project_id: "p", source_type: "url",
            source_filename: "https://example.com/x", status: "pending",
            node_ids: [], error: null,
            created_at: new Date().toISOString(), completed_at: null,
          }),
          { status: 202, headers: { "content-type": "application/json" } },
        );
      }
      if (u.includes("/api/v1/knowledge/jobs/job-url")) {
        return new Response(
          JSON.stringify({
            id: "job-url", user_id: "matt", project_id: "p", source_type: "url",
            source_filename: "https://example.com/x", status: "completed",
            node_ids: ["n1", "n2"], error: null,
            created_at: new Date().toISOString(), completed_at: new Date().toISOString(),
          }),
          { status: 200, headers: { "content-type": "application/json" } },
        );
      }
      return originalFetch(input, init);
    }) as unknown as typeof fetch;

    const user = userEvent.setup();
    render(<IngestModal open onOpenChange={() => {}} project_id="p" />, { wrapper });

    fireEvent.click(screen.getByRole("tab", { name: /url/i }));
    const input = screen.getByLabelText(/^url$/i);
    await user.type(input, "https://example.com/x");
    await user.click(screen.getByRole("button", { name: /ingest/i }));

    await waitFor(() => expect(screen.getByText(/ingested 2 chunks/i)).toBeInTheDocument());
  });

  it("disables ingest when the URL is empty or malformed", async () => {
    const user = userEvent.setup();
    render(<IngestModal open onOpenChange={() => {}} project_id="p" />, { wrapper });

    fireEvent.click(screen.getByRole("tab", { name: /url/i }));
    const ingestBtn = screen.getByRole("button", { name: /ingest/i });
    expect(ingestBtn).toBeDisabled();

    const input = screen.getByLabelText(/^url$/i);
    await user.type(input, "not a url");
    expect(ingestBtn).toBeDisabled();

    await user.clear(input);
    await user.type(input, "https://example.com/article");
    expect(ingestBtn).not.toBeDisabled();
  });
```

- [ ] **Step 2: Run, confirm fail**

```bash
cd apps/web && pnpm test --run -t "ingest-modal"
```

Expected: red — no URL tab, no URL input, button disable rules wrong.

- [ ] **Step 3: Update `IngestModal` to add the URL tab**

Read `apps/web/src/components/ingest/ingest-modal.tsx`. Apply these changes:

1. Widen tab union and add `url` state at the top of the component:

```tsx
const [tab, setTab] = useState<"markdown" | "pdf" | "url">("markdown");
const [markdown, setMarkdown] = useState("");
const [filename, setFilename] = useState("");
const [pdfFile, setPdfFile] = useState<File | null>(null);
const [url, setUrl] = useState("");
const [activeJob, setActiveJob] = useState<IngestionJob | null>(null);
```

2. Add the new mutation hook to the imports and inside the component:

```tsx
import {
  useStartMarkdownIngest,
  useStartPdfIngest,
  useStartUrlIngest,
  useIngestJob,
  type IngestionJob,
} from "@/hooks/use-ingest-job";
```

```tsx
const startUrl = useStartUrlIngest();
```

3. Add a URL helper near the top of the file (above the component):

```tsx
function isValidHttpUrl(value: string): boolean {
  try {
    const u = new URL(value);
    return u.protocol === "http:" || u.protocol === "https:";
  } catch {
    return false;
  }
}
```

4. Extend `submit`:

```tsx
const submit = async () => {
  if (tab === "markdown") {
    if (!markdown.trim()) return;
    const j = await startMd.mutateAsync({
      project_id: props.project_id,
      text: markdown,
      source_filename: filename.trim() || undefined,
    });
    setActiveJob(j);
  } else if (tab === "pdf") {
    if (!pdfFile) return;
    const j = await startPdf.mutateAsync({ project_id: props.project_id, file: pdfFile });
    setActiveJob(j);
  } else {
    if (!isValidHttpUrl(url)) return;
    const j = await startUrl.mutateAsync({ project_id: props.project_id, url });
    setActiveJob(j);
  }
};
```

5. Reset URL on `reset()`:

```tsx
const reset = () => {
  setActiveJob(null);
  setMarkdown("");
  setFilename("");
  setPdfFile(null);
  setUrl("");
  setTab("markdown");
};
```

6. Add the tab trigger and the new `<TabsContent>`:

```tsx
<TabsList>
  <TabsTrigger value="markdown">Markdown</TabsTrigger>
  <TabsTrigger value="pdf">PDF</TabsTrigger>
  <TabsTrigger value="url">URL</TabsTrigger>
</TabsList>
```

```tsx
<TabsContent value="url">
  <div className="space-y-3">
    <div className="space-y-1.5">
      <Label htmlFor="url-input">URL</Label>
      <Input
        id="url-input"
        type="url"
        value={url}
        onChange={(e) => setUrl(e.target.value)}
        placeholder="https://example.com/article"
      />
      <p className="text-muted-foreground text-xs">
        We render JavaScript and extract the article body.
      </p>
    </div>
  </div>
</TabsContent>
```

7. Update the Ingest button's `disabled` so URL tab disables on invalid input:

```tsx
<Button
  onClick={submit}
  disabled={
    startMd.isPending ||
    startPdf.isPending ||
    startUrl.isPending ||
    (tab === "markdown" && !markdown.trim()) ||
    (tab === "pdf" && !pdfFile) ||
    (tab === "url" && !isValidHttpUrl(url))
  }
>
  Ingest
</Button>
```

(Adjust the surrounding markdown/pdf disable behavior only if needed for the empty-input case in the new test — the existing tests already passed on the looser rule. Verify by running both old and new tests.)

- [ ] **Step 4: Run, confirm pass**

```bash
cd apps/web && pnpm test --run
```

Expected: all green (existing markdown + new URL tests).

- [ ] **Step 5: Type-check**

```bash
cd apps/web && pnpm exec tsc --noEmit
```

Expected: no errors.

- [ ] **Step 6: Commit**

```bash
cd "$(git rev-parse --show-toplevel)"
git add apps/web/src/components/ingest/ingest-modal.tsx \
        apps/web/src/tests/ingest-modal.test.tsx
git commit -m "feat(web): add URL tab to IngestModal"
```

---

## Task 8: Bake Chromium into the api Docker image

**Files:**
- Modify: `apps/api/Dockerfile`

- [ ] **Step 1: Update the Dockerfile**

Open `apps/api/Dockerfile`. After the `RUN ... uv venv && uv sync --all-packages` line, add:

```dockerfile
RUN uv run playwright install --with-deps chromium
```

The result should look like:

```dockerfile
RUN --mount=type=cache,target=/root/.cache/uv \
    uv venv && uv sync --all-packages

RUN uv run playwright install --with-deps chromium

ENV PATH="/app/.venv/bin:$PATH"
```

- [ ] **Step 2: Build the image to confirm Chromium installs cleanly**

```bash
docker build -f apps/api/Dockerfile -t atlas-api:phase2-plan1 .
```

Expected: build completes; you should see Playwright downloading Chromium and apt installing system libraries during the new layer. Image size grows ~200 MB. If the build fails because `--with-deps` cannot resolve apt deps in the slim base, fall back to `RUN uv run playwright install chromium` (no `--with-deps`) and add a manual `apt-get install -y libnss3 libxkbcommon0 libatk1.0-0 libatk-bridge2.0-0 libcups2 libdrm2 libxcomposite1 libxdamage1 libxfixes3 libxrandr2 libgbm1 libpango-1.0-0 libcairo2 libasound2` step before it. Document whichever path was used in the commit message.

- [ ] **Step 3: Quick smoke — run the image and hit `/health`**

```bash
docker run --rm -d --name atlas-smoke \
    -e ATLAS_DB__DATABASE_URL=postgresql+asyncpg://atlas:atlas@host.docker.internal:5432/atlas \
    -e ATLAS_ENVIRONMENT=development \
    -p 8001:8000 atlas-api:phase2-plan1

# Wait a couple seconds then:
curl -fsS http://127.0.0.1:8001/health || echo "FAIL"
docker stop atlas-smoke
```

Expected: `200 OK`. (If your local stack is not running Postgres on port 5432, this smoke step is optional — the goal is just to confirm the image starts.)

- [ ] **Step 4: Commit**

```bash
git add apps/api/Dockerfile
git commit -m "feat(api/docker): install Chromium for Playwright URL ingestion"
```

---

## Task 9: Manual end-to-end smoke + final verification

**Files:** none (verification only).

- [ ] **Step 1: Bring up the full stack**

```bash
cd infra && docker compose up -d --build
docker compose ps
```

Expected: `postgres`, `redis`, `api`, `web` all `healthy` / `running`.

- [ ] **Step 2: Visit the web UI, paste a real article URL**

Open `http://localhost:5173` (or whatever port `apps/web` exposes per compose). Create or pick a project. Open IngestModal → URL tab → paste a real URL (e.g., a news article or blog post you can reach over the public internet from the api container). Click Ingest.

Expected: status transitions through `pending` / `running` to `completed`; "Ingested N chunks" appears (N typically 3-15 for an average article).

If `failed`, check `docker compose logs api` — common causes: site blocks headless UA (try a different URL), Trafilatura cannot find an article body (extraction empty), network egress blocked from the container.

- [ ] **Step 3: Confirm chunks landed in retrieval**

Use the chat or `/api/v1/knowledge/search`:

```bash
curl -fsS "http://localhost:8000/api/v1/knowledge/search?project_id=<id>&query=<keyword-from-article>&top_k=3" | jq .
```

Expected: at least one chunk with the article's text in the response.

- [ ] **Step 4: Run the full test suite once more from a clean checkout state**

```bash
cd "$(git rev-parse --show-toplevel)"
uv run pytest -q
cd apps/web && pnpm test --run && cd -
```

Expected: all green.

- [ ] **Step 5: Tear down**

```bash
cd infra && docker compose down
```

- [ ] **Step 6: (No commit — verification only)**

If any of the above fails, do not move on. Open follow-up tasks for any deferred items and surface them before claiming the plan complete.

---

## Self-review notes

- **Spec coverage check:** every backend, frontend, Docker, test, and out-of-scope clause from §4-§7 of the design doc has a task here. The four Q-decisions (parser API split, no dedupe, SSRF guard, per-request browser) are all encoded in Tasks 1-3 and the routers.
- **Type consistency:** `parse_url`, `parse_html`, `fetch_html`, `validate_url` signatures match between Tasks 1, 2, 3 and Task 5's router imports. `UrlIngestRequest` is referenced consistently across Tasks 4 and 5. `SourceType.URL = "url"` is the same string used in `IngestionService.ingest(source_type="url", ...)` in Task 5.
- **No placeholders:** every code block is complete; every `git commit` has a real message; every test has assertions.
- **Order matters:** Tasks 1-4 land the backend foundation; Task 5 wires the API; Tasks 6-7 wire the UI; Task 8 fixes the image; Task 9 verifies end-to-end. Each task ends with a green test run + commit, so subagent review can run between tasks against a known-good checkpoint.

---

*ATLAS Phase 2 — Plan 1 — Web/URL Ingestion Implementation Plan · 2026-04-27*
