# ATLAS Phase 2 — Plan 1 — Web/URL Ingestion Design

**Status:** Draft · 2026-04-27
**Implements:** Phase 2 spec §5.1 (`docs/superpowers/specs/2026-04-27-atlas-phase-2-knowledge-graph-design.md`)
**Predecessor:** Phase 1 closed; markdown + PDF ingestion shipping; React `IngestModal` live.
**Successor:** Plan 2 (Neo4j + graph schema + write path) — independent of this plan.

---

## 1. Purpose

Pasting a URL into the IngestModal should produce the same "Ingested N chunks" experience as a markdown paste or PDF upload. The new endpoint feeds the existing `IngestionService` unchanged; no graph, no retrieval changes. This is the smallest Phase 2 increment — if Phase 2 stalls after this plan, ATLAS still gains URL ingestion.

A URL ingest fetches the rendered HTML via Playwright (so JS-rendered articles work), extracts the main article body via Trafilatura, and routes the resulting `ParsedDocument` through chunker → embedder → vector store + DB. The user sees a third tab in the existing modal.

---

## 2. Scope

### In scope
- New parser module `atlas_knowledge/parsers/url.py` with split fetch/parse functions.
- New endpoint `POST /api/v1/knowledge/ingest/url`.
- `SourceType.URL` enum value + `UrlIngestRequest` Pydantic model.
- SSRF guard: scheme allowlist + DNS-resolved private/loopback rejection.
- Playwright Chromium baked into the api Docker image.
- New "URL" tab in `IngestModal`; new `useStartUrlIngest()` hook.
- Unit, integration (opt-in), and router tests; extended frontend test.

### Out of scope
- Browser-extension capture (deferred per spec §2).
- Batch URL importer, scheduled re-fetch, RSS feeds.
- URL deduplication / upsert — re-ingesting the same URL creates a new document (Phase 1 dedupe parity; revisit when scheduled re-fetch lands).
- Persistent browser process — Plan 1 launches Chromium per request.
- Any Neo4j / graph write — that lands in Plan 2.

---

## 3. Architecture

```
URL paste
  │
  ▼
POST /api/v1/knowledge/ingest/url            (new router handler)
  │
  ├── ProjectORM.get(project_id) or 404
  ├── validate_url(url) or 400
  ├── parse_url(url) async or 502 on fetch/parse failure
  │     │
  │     ├── fetch_html(url)        async → str        (Playwright, ephemeral browser)
  │     └── parse_html(html, url)  sync  → ParsedDocument   (Trafilatura)
  │
  └── IngestionService.ingest(parsed=…, source_type="url",
                              source_filename=url) → IngestionJob
              │
              └── unchanged: chunk → embed → vector store + DB
```

### 3.1 Why a split parser API
The existing `parse_markdown` / `parse_pdf` are sync and pure. Playwright is async. Splitting into:

- `async def fetch_html(url) -> str` — the only async, side-effecting half;
- `def parse_html(html, url) -> ParsedDocument` — pure, deterministic, sync;
- `async def parse_url(url) -> ParsedDocument` — public facade composing both;

keeps unit tests for extraction logic browser-free (HTML fixtures into `parse_html`) and confines Playwright concerns to one mockable async function. The router calls `parse_url` and stays symmetric with the markdown / PDF call sites.

### 3.2 No schema migration
`KnowledgeNodeORM.source_type` and `IngestionJobORM.source_type` are `Text`, not Postgres enums (verified in `apps/api/.../alembic/versions/0003_*.py`). Adding `SourceType.URL = "url"` is a Pydantic-only change. No alembic migration needed for Plan 1.

---

## 4. Components

### 4.1 `packages/atlas-knowledge/atlas_knowledge/parsers/url.py` (new)

```python
def validate_url(url: str) -> str: ...
async def fetch_html(url: str, *, timeout_s: float = 30.0) -> str: ...
def parse_html(html: str, url: str) -> ParsedDocument: ...
async def parse_url(url: str) -> ParsedDocument: ...
```

**`validate_url(url)`** — raises `ValueError` if any of:
- scheme not in `{"http", "https"}`;
- host empty or fails to parse;
- DNS-resolved IP is in any blocked range:
  - IPv4: RFC1918 (`10/8`, `172.16/12`, `192.168/16`), loopback (`127/8`), link-local (`169.254/16`), `0.0.0.0`;
  - IPv6: loopback (`::1`), unique local (`fc00::/7`), link-local (`fe80::/10`).

Uses `socket.getaddrinfo(host, None)` and `ipaddress.ip_address(...).is_private | is_loopback | is_link_local | is_unspecified`. Returns the original URL string on success.

**`fetch_html(url, *, timeout_s=30.0)`** — opens `async_playwright()`, launches `chromium` headless, new context with a desktop user-agent, new page, navigates with `wait_until="domcontentloaded"`, then awaits `page.wait_for_load_state("networkidle", timeout=5000)` swallowing `TimeoutError` (SPAs that never go idle still return). Hard 30s wall-clock timeout via `asyncio.wait_for` over the whole call. Returns `await page.content()`. Closes browser in `finally`.

**`parse_html(html, url)`** — calls `trafilatura.extract(html, output_format="markdown", include_tables=True, include_comments=False, include_links=False, with_metadata=True)`. Parses the returned metadata block (Trafilatura prefixes it as JSON in metadata mode, or use `trafilatura.bare_extraction(...)` which returns a structured dict — design picks `bare_extraction` for cleaner access). Title resolution: `metadata.title` → BeautifulSoup-extracted `<title>` → URL itself. Raises `ValueError("no extractable content")` if extracted text is `None` or under 100 chars (the threshold is a constant; tunable). Returns:

```python
ParsedDocument(
    text=extracted_markdown,
    title=resolved_title,
    source_type="url",
    metadata={
        "source_url": url,
        "fetched_at": datetime.now(UTC).isoformat(),
        "author": metadata.author,            # Optional
        "published_date": metadata.date,      # Optional
        "site_name": metadata.sitename,       # Optional
        "language": metadata.language,        # Optional
    },
)
```

**`parse_url(url)`** — `return parse_html(await fetch_html(url), url)`.

### 4.2 `packages/atlas-knowledge/pyproject.toml`

Add to `[project].dependencies`:
```
"playwright>=1.45,<2",
"trafilatura>=1.12,<2",
```

### 4.3 `packages/atlas-knowledge/atlas_knowledge/models/ingestion.py`

- `SourceType.URL = "url"` added to the enum.
- New model `UrlIngestRequest(AtlasRequestModel)`:
  ```python
  class UrlIngestRequest(AtlasRequestModel):
      project_id: UUID
      url: HttpUrl  # pydantic v2 HttpUrl — server-side scheme + structure check
  ```
  This is a separate model from `IngestRequest` because the validators are different and `IngestRequest` would otherwise grow conditional logic. `IngestRequest` is left untouched.
- The `source_filename` field on `IngestionJob` widens semantically to "URL itself" for URL ingests (the stored value is the URL string). No schema change — it's already nullable text.

### 4.4 `apps/api/atlas_api/routers/knowledge.py`

New handler:
```python
@router.post("/knowledge/ingest/url", response_model=IngestionJob, status_code=202)
async def ingest_url_endpoint(
    payload: UrlIngestRequest,
    db: AsyncSession = Depends(get_session),
    service: IngestionService = Depends(get_ingestion_service),
    settings: AtlasConfig = Depends(get_settings),
) -> IngestionJob:
    if await db.get(ProjectORM, payload.project_id) is None:
        raise HTTPException(404, "project not found")
    url = str(payload.url)
    try:
        validate_url(url)
    except ValueError as e:
        raise HTTPException(400, str(e))
    try:
        parsed = await parse_url(url)
    except ValueError as e:                       # extraction empty
        raise HTTPException(502, f"could not extract content: {e}")
    except Exception as e:                        # Playwright timeout, nav fail, etc.
        raise HTTPException(502, f"fetch failed: {e}")
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
        raise HTTPException(500, "ingest created no job row")
    return ingestion_job_from_orm(job_row)
```

### 4.5 `apps/api/atlas_api/...` — `IngestionService` docstring update only

The `source_type: str # "markdown" | "pdf"` comment becomes `# "markdown" | "pdf" | "url"`. No code change.

### 4.6 Docker — `apps/api/Dockerfile`

After the `uv sync` step that installs Python deps, add:

```dockerfile
RUN uv run playwright install --with-deps chromium
```

This adds ~200 MB to the image (Chromium binary + system libs). Spec §5.1 explicitly accepts this. Single-stage for now; multi-stage optimization is a future concern.

### 4.7 Frontend — `apps/web/src/components/ingest/ingest-modal.tsx`

Tab state widens: `useState<"markdown" | "pdf" | "url">("markdown")`. New `<TabsTrigger value="url">URL</TabsTrigger>`. New `<TabsContent value="url">`:

```tsx
<div className="space-y-3">
  <Label htmlFor="url-input">URL</Label>
  <Input
    id="url-input"
    type="url"
    placeholder="https://example.com/article"
    value={url}
    onChange={(e) => setUrl(e.target.value)}
  />
  <p className="text-muted-foreground text-xs">
    We render JavaScript and extract the article body.
  </p>
</div>
```

Submit branch: `if (tab === "url") { if (!isValidHttpUrl(url)) return; await startUrl.mutateAsync({project_id, url}); }`. `isValidHttpUrl` is a 5-line helper using browser `URL` constructor + scheme check. The Ingest button stays disabled when the active tab's input is empty/invalid.

### 4.8 Frontend — `apps/web/src/hooks/use-ingest-job.ts`

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

`IngestionJob.source_type` widens: `"markdown" | "pdf" | "url"`.

---

## 5. Data flow & error handling

| Failure | Where | Surface |
|---|---|---|
| Bad scheme / malformed URL | `UrlIngestRequest` (`HttpUrl`) → `validate_url` | 422 (Pydantic) or 400 (validate_url) — modal shows mutation error inline; no job row created |
| URL host resolves to private/loopback IP | `validate_url` | 400 — same |
| Project does not exist | router | 404 |
| Playwright nav timeout / connection refused | `fetch_html` raises | 502 — **no job row** (matches markdown router's pre-service failure path) |
| Trafilatura returns empty / <100 chars | `parse_html` `ValueError` | 502 — same |
| Failure inside `IngestionService.ingest` (chunker / embed / vector store) | service exception path | Job row written, `status=failed`, `error=str(e)`; existing modal failure UI handles it |

The pre-service vs in-service split mirrors the existing markdown / PDF endpoints. Both modal failure surfaces (`mutation.error` and `job.status === "failed"`) already render today.

Cancellation / interactive retry are out of scope. The existing `IngestModal` "Retry" button — which resets local state and lets the user resubmit — is the only retry path.

---

## 6. Testing strategy

### 6.1 Unit (no browser)
- `packages/atlas-knowledge/atlas_knowledge/tests/test_parsers_url.py`:
  - `parse_html` fixtures: a typical news-article HTML, an article-with-tables HTML, a JS-shell page with no extractable body. Asserts title/text/metadata; asserts `ValueError` on the empty case.
  - `validate_url` table tests: valid http/https hosts (mocked DNS to `203.0.113.1`); rejected on `ftp://`, malformed, RFC1918 IPv4, `127.0.0.1`, `169.254.x.x`, `::1`, `fc00::/7`. DNS resolution is mocked via `monkeypatch.setattr(socket, "getaddrinfo", ...)` so tests are network-free.
- `test_models_ingestion.py` (extend): `SourceType.URL` round-trips; `UrlIngestRequest` accepts a valid URL and rejects non-HTTP schemes.

### 6.2 Integration (opt-in, real Playwright)
- `test_parsers_url_integration.py` marked `@pytest.mark.integration` and skipped unless `ATLAS_RUN_PLAYWRIGHT_TESTS=1`. Spins up an `aiohttp` server on `127.0.0.1:<random>` serving a fixture HTML page; monkeypatches `validate_url` for this test only (loopback would otherwise be rejected); calls `fetch_html` against the local URL; asserts content round-trips. This is the only test that needs Chromium installed.
- Default `uv run pytest` skips it; `uv run pytest -m integration` runs it.

### 6.3 Router (mocked parser)
- `apps/api/atlas_api/tests/test_knowledge_router.py` (extend): patches `atlas_knowledge.parsers.url.parse_url` to return a fixture `ParsedDocument`. Asserts:
  - 202 + IngestionJob on happy path; chunks land in vector store + DB.
  - 400 on `validate_url` rejection (e.g. `http://10.0.0.1`).
  - 422 on missing fields / non-URL body.
  - 404 on unknown project.
  - 502 when `parse_url` raises a generic exception or `ValueError`.

### 6.4 Frontend (Vitest)
- `apps/web/src/tests/ingest-modal.test.tsx` (extend):
  - URL tab renders; Ingest button is disabled when URL is empty or fails browser `URL` parse;
  - `useStartUrlIngest` is called with the right payload on submit;
  - polled job status drives the same success/failure UI as markdown.

### 6.5 Manual smoke
Before merging the PR: `docker compose up`, paste a real article URL into the new tab, confirm chunks ingest and surface in chat retrieval. Documented in the plan's verification section.

---

## 7. Definition of Done

1. `POST /api/v1/knowledge/ingest/url` exists, validates URL + SSRF, fetches via Playwright, extracts via Trafilatura, returns an `IngestionJob`. Same job-poll lifecycle as markdown / PDF.
2. `IngestModal` shows three tabs; the URL tab ingests an article into the active project.
3. Unit + router tests pass via default `uv run pytest`. Integration test passes via `ATLAS_RUN_PLAYWRIGHT_TESTS=1 uv run pytest -m integration` on a machine with Chromium installed.
4. `docker compose up` from a clean clone produces an api image with Chromium baked in; manual smoke ingests a real URL successfully.
5. No Neo4j, no retrieval changes, no migrations.

---

## 8. Risks

- **Image bloat (~200 MB).** Acknowledged in spec §5.1. Single-stage Docker build for now; if it becomes painful, multi-stage with a slim runtime layer is a one-PR refactor.
- **Sites that block headless browsers.** Failure surfaces as a `502` from the router (since fetch happens before the service). Acceptable; user falls back to copying markdown into the existing tab.
- **SPA pages that never reach `networkidle`.** Mitigation: 5s soft cap on `networkidle`, fall back to current DOM content.
- **JS-shell pages with no real article (paywalled or app-shell-only).** Trafilatura will return short / empty text; `parse_html` raises and the user sees a "could not extract content" message.
- **Future deduplication.** Plan 1 always creates a new doc on re-ingest; the URL is in `metadata.source_url`, so a future plan can add upsert semantics with a single index + a service-level branch. No migration debt.

---

## 9. Open items deferred to per-plan brainstorms (none)

All Plan-1-specific decisions resolved during this brainstorm:
- Parser API split (Q1=C — `parse_url` facade over `fetch_html` + `parse_html`).
- Re-ingest semantics (Q2=A — always new doc).
- URL validation & SSRF guard (Q3=B — scheme allowlist + DNS-resolved private/loopback block).
- Browser lifecycle (Q4=A — per-request Chromium, no shared state).

---

*ATLAS Phase 2 — Plan 1 — Web/URL Ingestion · 2026-04-27*
