# Crawler layer

Status: describes the code on `main` at `75d3bfd` (2026-11-02), not the plans. The plan
for this part is [plan/01-crawler.md](../plan/01-crawler.md); where the code and the plan
differ, this file follows the code and lists the difference under [Known gaps](#known-gaps).

## Purpose and boundaries

The crawler turns a business's public website into stored, cleaned pages: one row in
`pages` per HTML page or linked PDF, as Markdown, keyed by `(business_id, url)`. It is
the only part of the app that talks to a business's website.

**In the layer**

| Module | Role |
|---|---|
| `app/ingest/discover.py` | Pure parsing, no network: sitemaps, sitemap indexes, links, robots.txt (Protego), `noindex` meta, URL normalisation, priority order |
| `app/ingest/fetch.py` | All HTTP to the site: one `httpx.Client` per crawl, retries, robots.txt / sitemap / page / PDF fetches, PDF text extraction |
| `app/ingest/clean.py` | HTML to Markdown: main content and site chrome (nav/header/footer) kept apart, title, `og:site_name`, `<html lang>`, content hash |
| `app/ingest/identity.py` | Business identity from a URL: `domain_of` (www-stripped host), `site_root` |
| `app/ingest/run.py` — crawl half | CLI entry, `resolve_business`, `_expand_by_links` (BFS), the fetch loop of `crawl_business`, the delete-safety threshold |
| `app/ingest/store.py` — `upsert_page`, `delete_pages_not_in` | Writes and deletes `pages` rows |

**Not in the layer** (but in the same package or file)

- `run.py` after the fetch loop is an orchestrator for the data layer: organize,
  rechunk, profile, timezone, facts, summary, and a voice greeting. Those steps are
  described in [data.md](data.md); this file only records the order and the
  calls.
- `store.replace_chunks` writes `chunks` and belongs to [data.md](data.md).
- `app/ingest/places.py` is a Google Places client and belongs to
  [integrations.md](integrations.md).
- Embedding is not run by the crawler: it is a separate CLI, `python -m app.rag.index`
  ([data.md](data.md)).

**Boundary rule as built.** The crawler's output contract is the `pages` table (`url`,
`title`, `markdown`, `chrome_markdown`, `content_hash`, `language`, `scraped_at`) plus a
set of `noindex` URLs and an optional brand name handed to the next step in memory. The
organize-step columns on `pages` (`page_type`, `location_id`, `retrievable`,
`duplicate_of`, ...) are never written by the crawler.

## How it runs

- Only as a CLI: `python -m app.ingest.run --url <site> [--max-pages N] [--no-profile]
  [--facts-model haiku|qwen|gemma]`. `--only summary|facts` and `--report conflicts`
  skip the crawl and run a data-layer step on stored pages.
- No API endpoint, scheduler or web button starts a crawl (checked: no reference to
  `crawl_business` or `app.ingest.run` under `app/api/`, `app/main.py` or `web/src/`).
- Natively on the Mac (DEC-28), or from the API's Docker image:
  `docker compose run --rm crawler --url <site>` (`docker-compose.yml`, service
  `crawler`, entrypoint `python -m app.ingest.run`). There is no separate crawler image:
  `Dockerfile` says one is added only with Crawl4AI (DEC-36).
- Synchronous and single-threaded for HTTP. One crawl at a time is assumed; there is no
  lock.

## Data flow

```
--url ──► resolve_business (identity.domain_of → businesses.domain; create row if new)
          │
          ▼
robots.txt ──► RobotsChecker (Protego, our token "prototypeRAG-crawler")
          │      └─ Crawl-delay / Request-rate raises the client delay (never lowers it)
          ▼
sitemap: robots.txt Sitemap: lines, then /sitemap.xml ──► index? one level of children (≤20)
          │
          ▼
discover_pages: same domain, not skippable, robots-allowed, prioritize(), cap max_pages
          │   └─ sitemap gave ≤1 URL → _expand_by_links: BFS from start_url, max_depth 3
          ▼
for each URL (list grows with linked PDFs, ≤ pdf_cap = 10, on top of max_pages):
   fetch_document (HTML or PDF by Content-Type) ── sleep crawl delay
   HTML → clean_page → main_markdown + chrome_markdown + language; title; og:site_name
   PDF  → extract_pdf_text (MarkItDown if a table, else pypdf); pdf_title
   noindex (meta or X-Robots-Tag) → noindex_urls
   content_hash(main_markdown) → upsert_page (one tenant_session per page)
          │
          ▼
businesses.name ← first og:site_name seen
          │
          ▼  ── hand-off to the data layer ──
organize_business(business_id, start_url, root, noindex_urls)
_rechunk_page for each organized page
delete_pages_not_in (only if pages_crawled ≥ 50 % of stored pages)
profile → timezones → facts → summary → greeting audio
```

**In:** the start URL and CLI flags; the site's robots.txt, sitemaps, HTML pages and
same-domain PDFs; settings from `app/config.py` (only `database_url` directly, the rest
via the data steps).

**Out:**

| Where | What |
|---|---|
| `businesses` | New row on first crawl (`name = PENDING_NAME`, `website = site_root`, `domain = domain_of`); `name` replaced by `og:site_name` on every crawl |
| `pages` | Insert or update per URL; `changed` is true only when `content_hash` changed. `chrome_markdown` and `language` are always overwritten. `scraped_at` moves only on a content change |
| `pages` (delete) | Rows whose URL was not attempted this crawl, except the site-chrome pseudo-page; cascades to chunks and fact sources |
| In memory to organize | `noindex_urls`, `start_url`, site root |
| stdout | One line per skipped URL and a summary dict (`pages_crawled`, `pages_changed`, `deleted_pages`, `deletes_skipped`, ...) |

## Key functions

| Function | File:line | What it does |
|---|---|---|
| `resolve_business` | `run.py:84` | Finds the business by `domain_of(url)` (cross-tenant `SessionLocal`, the one read without a `business_id`), or creates it; `--business-id` must not contradict the domain |
| `crawl_business` | `run.py:286` | The whole crawl and the post-crawl steps; returns the summary dict |
| `_expand_by_links` | `run.py:117` | BFS when no usable sitemap; caches fetched HTML for the main loop |
| `discover_pages` | `discover.py:219` | Sitemap fast path; falls back to `[start_url]` |
| `prioritize` | `discover.py:145` | Start URL, depth-1 pages, deeper pages, then ≤ 20 blog posts (`/blog`, `/journal`); sorted *before* the `max_pages` cut so a blog can't crowd out location pages |
| `RobotsChecker` | `discover.py:195` | Protego (RFC 9309, wildcards, longest match) for our token, falling back to `*`; fractional Crawl-delay rounded up |
| `has_noindex` | `discover.py:135` | `<meta name="robots" content="…noindex|none…">`, any attribute order |
| `normalize_url` | `discover.py:81` | Drops fragment and query, lowercases host, strips trailing slash |
| `fetch_robots_txt` | `fetch.py:149` | Text on 2xx, `None` on 4xx (no rules), raises `RobotsUnavailable` on 429/5xx/network error |
| `fetch_document` | `fetch.py:128` | Streamed GET with retries; HTML or PDF decided by the response, PDFs capped at 20 MB; reads `X-Robots-Tag` |
| `_get` | `fetch.py:62` | Retries 429/5xx/timeouts twice: `Retry-After` seconds (≤ 60 s) or 2 s then 4 s |
| `extract_pdf_text` | `fetch.py:224` | MarkItDown when it finds a Markdown table (rows stay on one line, DEC-51), else pypdf; `""` for scanned PDFs |
| `clean_page` | `clean.py:112` | Pops nav/header/footer as chrome, drops scripts/forms/cookie-banner widgets, takes `<main>` or a single `<article>` or `<body>` |
| `content_hash` | `clean.py:139` | SHA-256 of the main Markdown; also used by organize, fact_chunks and custom replies |
| `domain_of` / `site_root` | `identity.py:12/34` | Identity host (literal `www.` prefix removed, subdomains kept) and the site root URL |
| `upsert_page` | `store.py:18` | Insert or update by `(business_id, url)`, returns `(page, changed)` |
| `delete_pages_not_in` | `store.py:92` | Deletes every page of the business whose URL is not in `keep_urls` |

## Config

The crawler has no env vars of its own. Its knobs are constants and CLI flags:

| Knob | Where | Value |
|---|---|---|
| `--max-pages` | CLI | 200 |
| `max_depth` | `crawl_business` arg | 3 (BFS path only) |
| `delay_seconds` | `crawl_business` arg | 0.5 s, raised to robots.txt's Crawl-delay however long |
| `DEFAULT_PDF_CAP` | `run.py:65` | 10 PDFs on top of `max_pages` |
| `MIN_CRAWLED_FRACTION` | `run.py:68` | 0.5 |
| `DEFAULT_BLOG_CAP` | `discover.py:29` | 20 |
| `MAX_CHILD_SITEMAPS` | `fetch.py:194` | 20 |
| `MAX_PDF_BYTES` | `fetch.py:29` | 20 MB |
| `RETRIES`, `RETRY_BASE_SECONDS`, `MAX_RETRY_AFTER_SECONDS` | `fetch.py:33-35` | 2, 2 s, 60 s |
| Timeout | `make_client`, `fetch_*` | 15 s per request (httpx: per read, not total) |
| `USER_AGENT` | `fetch.py:20` | `prototypeRAG-crawler/0.1 (+https://github.com/placeholder; …)` with browser-like `Accept` headers (#121) |
| Skip lists | `discover.py:22-23` | image/CSS/JS/font extensions; `/cart`, `/checkout`, `/login`, `/account`, `/search` |

`DATABASE_URL` reaches it through `app/db`. The LLM and TTS settings used after the fetch
loop belong to the data and voice layers.

## Error handling

| Failure | Behaviour |
|---|---|
| robots.txt 429, 5xx, timeout, connection error | `RobotsUnavailable`; crawl stops before any write and returns `{"aborted": …}` (RFC 9309, R10) |
| robots.txt 4xx | Treated as no rules |
| Sitemap or child sitemap fails | Skipped silently, never raises; falls back to `/sitemap.xml`, then to BFS |
| Page 429/5xx/timeout | Retried twice, then the URL is skipped with a printed line |
| Any exception for one URL (fetch, parse, DB write) | Caught per URL, printed, next URL; each page has its own transaction, so one bad page rolls back only itself |
| PDF over 20 MB, or with no text | Skipped |
| Crawl fetched 0 pages, or fewer than 50 % of the pages stored | Deletes skipped and reported in `deletes_skipped`; stored knowledge kept |
| Profile, timezone, facts, summary, greeting fail | Each caught on its own and reported; the crawl result still returns |
| `--business-id` disagrees with the domain | `SystemExit` before any crawl |

The crawl delay is applied after every request, including failed ones.

## Tests

`tests/test_crawler.py` (72 tests: discovery, cleaning, PDFs, store, the full crawl
against fixtures), `tests/test_crawler_safety.py` (19: robots, noindex, retries, delete
threshold), `tests/test_crawler_real_sites.py` (14: saved real-site HTML/sitemaps),
`tests/test_fetch_headers.py` (2). `scripts/compare_crawlers.py` compares this crawler
with Scrapy and MarkItDown (#116); it is not part of the app.

## Known gaps

From reading the code at `75d3bfd`. Items marked "review" were first found in the
2026-11-01 crawler review (project file `reviews/crawler-review.md`); its H1, H3, H4, M1
and the meta/`X-Robots-Tag` part of L1 are fixed on `main` and not repeated here.

**Safety and correctness**

- **No SSRF guard (review H2).** `make_client` follows redirects anywhere and only the
  pre-request URL is checked with `same_domain`. A site or sitemap can redirect to
  localhost, a LAN address or a cloud metadata host, and the body is stored as a page.
  `--url` itself is not validated. Low risk while Max types the URL; real once a business
  owner supplies it (stage 2) or the crawler runs in Cloud Run (stage 3).
- **Redirects stored under the requested URL (review M2).** `fetch_document` does not
  return the final URL, so a same-site link that redirects off-site is stored as this
  business's content and that host's robots.txt is never read.
- **BFS path ignores Content-Type (review M3).** `_expand_by_links` uses `fetch_text`,
  and its cached body is reused by the main loop, so a link like `/menu` that serves a PDF
  is stored as decoded bytes. A BFS fetch that failed is requested again in the main loop.
- **URL normalisation is uneven (review M4).** BFS links are normalised; sitemap URLs and
  the start URL are not, so `https://x.com` and `https://x.com/`, or `http`/`https`, or
  `www`/bare host can become two pages. Dropping the query merges pages that differ only
  by query.
- **No size or total-time limit on HTML, robots.txt or sitemap bodies (review M5)**, and
  PDF parsing has no CPU/time limit (review M6).
- **Unbounded Crawl-delay (review M7)** is honoured in full with no budget or message.
- **`businesses.name` is overwritten by `og:site_name` on every crawl (review M8)**, even
  if the owner edited it.
- **Sitemap fetches** (`fetch_sitemap`, `fetch_child_sitemaps`) use plain `client.get`:
  no retries, no size cap, and the first declared sitemap that answers 200 wins even if
  its body is not XML; gzipped sitemaps are not decoded (review L2). Only one level of
  sitemap index is followed.
- **robots.txt is read for the start host only (review L3)**; `www.` and bare host pass
  `same_domain` but share one rule set.
- **The User-Agent's contact URL is a placeholder (review L4).**
- **No lock against two crawls of one business (review L5).**
- **Page text goes to LLM steps unfiltered (review L6)**: prompt injection is handled, if
  at all, by the data layer's quote checks.

**Coverage**

- httpx only, no JavaScript rendering (DEC-36: Crawl4AI when a JS site is hit).
- PDFs on another host (e.g. a Squarespace CDN) are not followed: same domain only.
- Scanned PDFs (images) give no text; no OCR.
- `max_depth` is unused on the sitemap path (`discover_pages` has `noqa: ARG001`), so
  deep sitemap URLs are crawled (review C3).
- Subdomains are separate businesses: `domain_of` keeps them, with no public-suffix list.

**Structure**

- `run.py` mixes the crawl with the data pipeline and imports
  `app.voice.greeting_cache` to pre-generate greeting audio, so the crawler module
  depends on the voice layer. A thin orchestrator outside `app/ingest/` (or a
  hook the data layer owns) would keep the crawler's imports to `db` and its own
  modules.
- `discover.py` duplicates organize's blog prefixes (`journal`, `blog`) by hand.
- `fetch.py` stores the delay on a private attribute of the httpx client
  (`client._crawl_delay`), and `fetch_sitemap` sleeps with `time.sleep` instead of the
  test-patchable `_sleep`.
- Docs mismatch: `CLAUDE.md` says "Crawler in its own Docker image", but it runs from the
  API image (review C1); `plan/01-crawler.md` lists Crawl4AI in `fetch.py` (review C5).

## Depends on / Used by

Layer names follow the 7-layer map: crawler, data (organize, chunks, facts, profile, summary, index/retrieve, `app/db`), voice (call session, VAD, greeting cache, tools, RAG answer), actions (what the app does: bookings, SMS, call transfer, call summary), integrations (thin clients for outside services such as Twilio, Google, the LLM endpoint; called by other layers, never calls back), api, web.

**Depends on**

| Layer | What |
|---|---|
| [data.md](data.md) | `app/db` (`tenant_session`, `SessionLocal`, models `Business`, `Page`, `BusinessProfile`, `Location`), and `organize.chrome_pseudo_url` (the fetch loop and the delete step keep the site-chrome pseudo-page), and from `run.py` after the fetch loop: `organize.organize_business`, `chunk_prefix`, `cut_testimonials`, `filter_junk_chunks`, `prefix_chunk_drafts`, `chunk.chunk_markdown`, `store.replace_chunks`, `profile.*`, `timezone.assign_timezones`, `facts.*`, `merge.*`, `fact_chunks.sync_fact_chunks`, `summary.summarize_business` |
| [voice.md](voice.md) | `app.voice.greeting_cache.pregenerate`, called at the end of a crawl (see Structure gap) |
| [integrations.md](integrations.md) | None directly from the crawl modules. The post-crawl steps reach the LLM (`app/llm.py`) and Google TTS through the data and voice layers |
| Outside | The business's website over HTTP (`httpx`), `protego`, `beautifulsoup4`, `markdownify`, `pypdf`, `markitdown[pdf]`; Postgres via `DATABASE_URL` |

**Used by**

| Layer | What |
|---|---|
| [data.md](data.md) | Reads the `pages` rows the crawler wrote; `organize.py`, `fact_chunks.py` and `store.py` import `clean.content_hash`; `app/db/models.py` and migration `0002` mirror `identity.domain_of` |
| [api.md](api.md) | `app/api/custom_replies.py` imports `clean.content_hash`. No endpoint starts a crawl |
| [web.md](web.md) | None |
| [actions.md](actions.md) | None |
| Tools | `scripts/compare_crawlers.py`; `docker-compose.yml` service `crawler` |
