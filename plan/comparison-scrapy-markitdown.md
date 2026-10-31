# Crawler vs Scrapy, PDF extraction vs MarkItDown (issue #116)

Stage 1 · area: parser. Question: would an open-source library do the job of `app/ingest/`
better? Result and decision: [DEC-51](DECISIONS.md). Scripts: `scripts/compare_crawlers.py`
(`crawl`, `pdf`) and `scripts/make_test_pdfs.py`; both are throw-away tools with extra
dependencies (`uv run --with scrapy --with 'markitdown[pdf]' ...`), not part of the app.

Run on 2026-10-28 from the cloud box (Scrapy 2.19.0, MarkItDown 0.1.9, pdfminer.six 20260107).

## Recommendation

| Part | Verdict |
|---|---|
| Crawler | **Keep ours.** Scrapy found the same pages and nothing it adds is needed at 5–200 pages per site. One real gap found (robots.txt `Sitemap:` lines) is a small fix in our code |
| PDF text | **Back with the library.** MarkItDown (pdfminer.six underneath, MIT) turns tables into Markdown rows; ours puts each cell on its own line. Switch `extract_pdf_text` to it, keep our size limit, Content-Type check and title logic |
| Scanned PDFs | No difference: neither reads them. OCR stays deferred |

## Crawler vs Scrapy

Same cap, 0.5 s delay, robots.txt obeyed, one request at a time; Scrapy used `SitemapSpider`
(follows sitemap indexes) and the same `clean_page` for the text, so character counts
compare discovery, not extraction.

| Site | Sitemap | Ours | Scrapy | Notes |
|---|---|---|---|---|
| indoorpickleballx.com, cap 100 | index, 4 children, 61 URLs | 61 pages | 61 pages | identical URL sets |
| bhbarbershop.com, cap 100 | index, 6 children, 70 URLs | 70 pages | 69 pages | Scrapy skipped one (start URL with/without trailing slash) |
| indoorpickleballx.com, cap 40 | same | 40 pages | 41 pages | different 40: ours is priority-ordered (depth-1 and location pages first, blog capped, DEC K1); Scrapy takes sitemap order, so author/category archive pages come early |
| bhbarbershop.com, cap 40 | same | 40 pages | 41 pages | same effect: Scrapy's set is mostly `/blog/…`, `/author/…`, `/category/…`; ours is services, hours, contact |
| abathhouse.com (pilot sauna) | — | not reachable | not reachable | 403/blocked from the cloud box for both; goes to "Test on Mac" |
| quotes.toscrape.com (no-sitemap case) | — | not reachable | not reachable | 403 from the cloud box; link-BFS path not compared live |

Findings:

- **Pages found and sitemap index (#104):** equal. Scrapy follows an index by default; ours
  follows one level (the 20-child limit is not a problem at these sizes).
- **Main content (#103):** Scrapy gives raw HTML only, no extraction, so it would still call
  `clean_page`. Nothing to gain. (MarkItDown's HTML converter is markdownify without
  main-content extraction, the same as ours; not worth swapping.)
- **robots.txt / crawl delay:** both obey. Ours reads fractional `Crawl-delay` (the stdlib
  drops it) and raises our delay to it; Scrapy's `ROBOTSTXT_OBEY` checks rules but its delay
  comes from settings, not robots.txt, unless `DOWNLOAD_DELAY` is set by us.
- **Priority and caps:** Scrapy has no equivalent of `prioritize()`; we would rewrite it
  around the spider. This is the main reason to keep ours.
- **JS-rendered pages:** neither does it alone. Scrapy needs `scrapy-playwright`; ours plans
  Crawl4AI (fetch.py docstring). Same cost. Not tested live.
- **Code and weight:** ours is `discover.py` + `fetch.py` + `run.py`'s BFS, plain httpx and
  sync. Scrapy is a Twisted reactor that can start once per process and has its own
  settings/pipeline model; the comparison script had to run it in a subprocess, and a
  Scrapy callback must be a real file (it reads callback source). `CLOSESPIDER_PAGECOUNT`
  also counts robots.txt and sitemap responses, so a page cap needs `ITEMCOUNT`.
- **One gap in ours:** Scrapy reads `Sitemap:` lines from robots.txt; `fetch_sitemap` only
  tries `/sitemap.xml`. bhbarbershop.com declares `Sitemap: …/sitemap_index.xml` and also
  serves it at `/sitemap.xml`, so it worked, but a site that declares only a non-default
  path would fall back to link BFS. Follow-up issue.
- **Speed:** ours was slower (35–65 s vs 25–40 s for 60 pages) only because it also sleeps
  after the robots/sitemap requests; not a reason to switch.

## PDF vs MarkItDown

Real PDFs: none of the three sites links one, and the cloud box cannot download sample PDFs
(hosts not allowed), so these are three generated PDFs (`scripts/make_test_pdfs.py`). A real
menu may behave differently; re-run `scripts/compare_crawlers.py pdf` on one on the Mac.

| PDF | Ours (`pypdf`) | MarkItDown |
|---|---|---|
| Price list with a ruled table (service / duration / price) | one cell per line: `Classic haircut`, `30 min`, `$35`, …; order correct but the row is lost | Markdown table, one row per service, price in the row |
| Two-column bar menu (names left, prices right) | names and prices on separate lines, each column read top to bottom | one table, each row `Espresso \| $4 \| Old Fashioned \| $14`: price stays next to its name |
| Scanned (image only) | empty | empty (no OCR by default) |

Both run in ~10 ms on these files. Text order was correct in all three; the difference is
table structure, which matters for prices (the assistant must not pair a price with the wrong
item). Not tested: long prose PDFs, multi-column articles, rotated pages, encrypted files.

## Licenses (from the repos' LICENSE files, 2026-10-28)

| Package | License | Source |
|---|---|---|
| Scrapy | BSD-3-Clause | github.com/scrapy/scrapy `LICENSE`; package metadata agrees |
| MarkItDown | MIT (Microsoft) | github.com/microsoft/markitdown `LICENSE`; package metadata agrees |
| pdfminer.six (MarkItDown's PDF backend) | MIT | package license file |
| pdfplumber (used for MarkItDown's tables) | MIT | package license file |

All allow commercial use. MarkItDown's optional extras (Azure, audio, etc.) are not needed
for PDFs; install only `markitdown[pdf]`.

## Not done / needs the Mac or the user

- [ ] Pilot sauna site (abathhouse.com) and a link-BFS site: unreachable from the cloud box.
- [ ] A real menu PDF with tables (ask the pilot business for theirs).
- [ ] JS-rendered page comparison (needs a headless browser setup).
