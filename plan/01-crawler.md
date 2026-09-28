# Part 1 — Crawler (collect public business data)

**Goal:** give the system a business website URL (plus its Google listing) and get back
clean, chunked knowledge ready to load into the vector database.

**Done when:** `python -m app.ingest.run --url https://example-restaurant.com` produces
Markdown pages, a structured business profile, and chunks for one real business.

---

## Sources

| Source | What we get | How |
|---|---|---|
| Company website | Menu, prices, services, hours, FAQ, contacts, about, policies | Crawl internal pages |
| Google business listing | Name, address, phone, opening hours, rating, reviews, categories | **Google Places API** (not scraping Google Maps — against ToS and breaks often) |
| PDFs linked on the site | Menus, price lists, brochures | Download + extract text (`pypdf` / `pymupdf`) |
| Owner uploads (later) | Anything missing from the site | Admin upload endpoint |

## Pipeline

```
URL ─► discover pages ─► fetch/render ─► clean to Markdown ─► extract profile ─► chunk ─► save
                                                                                        │
Google Places API ─► business profile (hours, address, phone, reviews) ─────────────────┘
```

1. **Discover pages**
   - Start from `sitemap.xml` if present, otherwise follow internal links from the home page.
   - Stay on the same domain; skip login, cart, media files; dedupe by normalized URL.
   - Limits: max depth (3) and max pages (e.g. 200) per business.
   - Respect `robots.txt` and add a delay between requests.

2. **Fetch / render**
   - **Crawl4AI** as the default (renders JavaScript, outputs Markdown).
   - Fallback: `httpx` + BeautifulSoup for simple static sites.

3. **Clean**
   - Remove navigation, footer, cookie banners, repeated boilerplate across pages.
   - Keep headings, lists, tables → Markdown.
   - One Markdown document per page with its source URL (stored in `pages`, step 6).

4. **Extract business profile** (structured facts)
   - Merge Places API data with facts found on the site into one record:
     `name, address, phone, email, opening_hours, booking_policy, price_range, languages`.
   - Use the LLM with a JSON schema to extract fields from the site text; Places API wins
     on conflicts for hours/address/phone.
   - **Open (OPEN-04, R9):** Places terms limit what may be stored permanently (only
     `place_id` for sure). Until checked, store `place_id` and treat Places fields as a
     short-lived cache / live fetch. Reviews are not stored in the knowledge base.
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

Runs in its **own Docker image** (Crawl4AI + headless browser), separate from the API
image so the API stays small and starts fast (R8, DEC-10). In cloud: Cloud Run Job.

## Tasks

- [ ] CLI skeleton + crawler Docker image (uses tables from the foundation step)
- [ ] Page discovery (sitemap, links, limits, robots.txt)
- [ ] Fetch with Crawl4AI, fallback fetcher
- [ ] Cleaning + Markdown output, check on 3 different real sites
- [ ] Google Places API client (needs API key; free monthly credit covers testing)
- [ ] Profile extraction with JSON schema
- [ ] Chunker + metadata
- [ ] Change detection for re-crawls
- [ ] Unit tests on saved HTML fixtures (no network in tests)

## Risks / notes

- JS-heavy sites and sites behind Cloudflare may block crawling → fallback: owner uploads.
- Menus often exist only as images/PDF → PDF text extraction; image OCR later if needed.
- Reviews: not stored in the knowledge base (Places terms, R9); if used at all, fetched
  live and clearly separated from official business facts.
