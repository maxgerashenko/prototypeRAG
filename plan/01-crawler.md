# Part 1 — Crawler (collect public business data)

**Goal:** give the system a business website URL (plus its Google listing) and get back
clean, chunked knowledge ready to load into the vector database.

**Done when:** `python -m app.ingest.run --url https://www.abathhouse.com/williamsburg` produces
Markdown pages, a structured business profile, and chunks for one real business.

Stage-1 test business: Bathhouse Williamsburg (DEC-31) — spa/sauna, Squarespace site,
server-rendered HTML, `sitemap.xml` present, `robots.txt` permits crawling.

---

## Sources

| Source | What we get | How |
|---|---|---|
| Company website | Menu, prices, services, hours, FAQ, contacts, about, policies | Crawl internal pages |
| Google business listing | Stored: `place_id` only. Live lookup (display, never stored): name, address, phone, opening hours. No ratings/reviews (R9) | **Google Places API** (not scraping Google Maps — against ToS and breaks often) |
| PDFs linked on the site | Menus, price lists, brochures | Download + extract text (`pypdf` / `pymupdf`) |
| Owner uploads (later) | Anything missing from the site | Admin upload endpoint |

## Pipeline

```
URL ─► discover pages ─► fetch/render ─► clean to Markdown ─► extract profile ─► chunk ─► save
                                                                                        │
Google Places API ─► place_id only (live lookup for display, R9) ───────────────────────┘
```

1. **Discover pages**
   - Start from `sitemap.xml` if present, otherwise follow internal links from the home page.
   - Stay on the same domain; skip login, cart, media files; dedupe by normalized URL.
   - Limits: max depth (3) and max pages (e.g. 200) per business.
   - Respect `robots.txt` and add a delay between requests.

2. **Fetch / render**
   - `httpx` + BeautifulSoup first — enough for server-rendered sites like the pilot (DEC-33).
   - **Crawl4AI** (renders JavaScript) added when a JS-rendered site is hit.

3. **Clean**
   - Remove navigation, footer, cookie banners, repeated boilerplate across pages.
   - Keep headings, lists, tables → Markdown.
   - One Markdown document per page with its source URL (stored in `pages`, step 6).

4. **Extract business profile** (structured facts)
   - Extract facts found on the site into one record:
     `name, address, phone, email, opening_hours, booking_policy, price_range, languages`
     (+ `place_id`, the only Places field we store).
   - Use the LLM with a JSON schema to extract fields from the site text; conflicts
     with a live Places lookup are shown to the owner to resolve.
   - Once the owner has confirmed the profile (`confirmed_at`), a re-crawl no longer
     overwrites it; a re-crawl also keeps the stored `place_id`.
   - **Sources of truth (DEC-11, R9):** stored facts come from the **website** and are
     **confirmed/edited by the owner** in the dashboard. From Places we store only
     `place_id`; Places fields are a live lookup, not stored or cached. Reviews are not
     stored. Open (OPEN-04): whether Places may pre-fill the owner's profile form.
   - This record goes into the prompt directly — critical facts should not depend on
     vector search.

5. **Chunk**
   - Split by Markdown headers first (`#`, `##`, `###`), then into ~500–800 token chunks
     with ~10–15% overlap.
   - Each chunk carries metadata:
     `business_id, source_url, page_title, section_heading, chunk_index, scraped_at, content_hash`.

6. **Save**
   - Everything goes into **Postgres** (same database as Part 2, locally in Docker,
     managed Postgres in cloud) — no file storage to switch between local and cloud:
     - cleaned Markdown → `pages` table (url, title, markdown, content_hash, scraped_at)
     - profile → `business_profile` table
     - chunks → `chunks` table with empty `embedding`; Part 2's indexer fills it.
   - Keeping chunk text in the database means we can **re-embed** any time (needed when
     moving to cloud embeddings) without re-crawling.
   - Raw HTML is not kept by default; add a Cloud Storage bucket later only if needed.

7. **Refresh**
   - Re-crawl on a schedule; compare `content_hash` per page and only re-chunk/re-embed
     changed pages; delete chunks of pages that disappeared.

## Code layout

```
app/ingest/
  run.py            CLI entry: --url, --business-id, --max-pages
  discover.py       sitemap + link discovery, robots.txt
  fetch.py          Crawl4AI / httpx fetchers
  clean.py          boilerplate removal, HTML → Markdown
  places.py         Google Places API client
  profile.py        LLM-based structured extraction + merge
  chunk.py          header-aware chunking + metadata
  store.py          write pages/profile/chunks to Postgres
```

Stage 1: runs natively (`uv run python -m app.ingest.run ...`, DEC-28). At stage-1 exit it
gets its **own Docker image** (Crawl4AI + headless browser), separate from the API image
so the API stays small and starts fast (R8, DEC-33) — needed only once Crawl4AI is added;
while the crawler is httpx-only it can run from the API image. Stage 2: Cloud Run Job + Scheduler.

## Tasks

- [x] CLI skeleton (`app/ingest/run.py`)
- [x] Page discovery (sitemap, links, limits, robots.txt) — `discover.py`
- [x] Fetch — `fetch.py`, httpx only; Crawl4AI deferred until a JS-heavy site is hit (DEC-27)
- [x] Cleaning + Markdown output — `clean.py`, checked against the pilot business (DEC-31)
- [x] Google Places API client — `places.py`, code only; **not live-tested, no API key yet**
- [x] Profile extraction with JSON schema — `profile.py`, verified against the live local LLM
- [x] Chunker + metadata — `chunk.py`
- [x] Change detection for re-crawls — `store.py` (`content_hash` on upsert; full chunk replace per page)
- [x] Unit tests on saved HTML fixtures (no network in tests) — `tests/test_crawler.py`
- [ ] Unit tests on 3 different real sites (only the pilot business so far)
- [ ] PDF menus: `fetch.extract_pdf_text` exists but isn't wired in — `discover.is_skippable` drops `.pdf` links
- [ ] Honour `Crawl-delay` from robots.txt (fixed 0.5 s delay today)
- [ ] Stage-1 exit: `Dockerfile.crawler` — only if Crawl4AI was added by then (DEC-33)

## Risks / notes

- JS-heavy sites and sites behind Cloudflare may block crawling → fallback: owner uploads.
- Menus often exist only as images/PDF → PDF text extraction; image OCR later if needed.
- Reviews: not stored in the knowledge base (Places terms, R9); if used at all, fetched
  live and clearly separated from official business facts.
