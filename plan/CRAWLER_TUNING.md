# Crawler, database and retrieval tuning — everything that affects speed and quality of the knowledge

Companion to [VOICE_TUNING.md](VOICE_TUNING.md). The crawler is an offline job, so "speed"
here means how long a crawl takes and how much it loads the target site and the local model;
"quality" means how complete, clean and correct the stored knowledge is — which is what every
later voice/chat answer is built from. A bad page boundary, a missed location or a junk chunk
shows up weeks later as a wrong answer, so quality knobs matter more than speed ones.

Sections 1–9 cover getting the data in (crawl → store → embed); sections 10–11 cover how it is
organized in Postgres and how it is read back (retrieval, shared by voice and `/chat` —
the voice-side knobs are in [VOICE_TUNING.md](VOICE_TUNING.md) §5).

Flow ([01-crawler.md](01-crawler.md), [07-knowledge-quality.md](07-knowledge-quality.md)):
**discover → fetch → clean → store → organize → chunk → (profile, timezone, facts, summary)
→ index (embed)**. Run: `python -m app.ingest.run --url <site>`, then `python -m app.rag.index`.

Values are the current defaults in the code (approximate, stage 1). **Where a knob lives:**
*CLI* = a flag of `app.ingest.run`; *env* = `.env` / [app/config.py](../app/config.py);
*code* = a constant in the named file (needs a PR); *prompt* = LLM instruction text;
*external* = LM Studio / Google. Change one thing, re-crawl, compare, and record the result
in [01-crawler.md](01-crawler.md) / [DECISIONS.md](DECISIONS.md) as usual.

## 1. Scope — which pages get crawled

Affects completeness (a page never fetched can't be answered from) and crawl time.

| Knob | Default | Where | Effect / trade-off |
|---|---|---|---|
| `--max-pages` | 200 | CLI | Hard cap on HTML pages. Applied **after** priority ordering, so a low cap drops blog/deep pages first, not locations |
| `--url` (start page) | — | CLI | Seed: the start URL is crawled first and sets the default location (DEC-37/A1). Pointing at the wrong location's page makes the wrong one the default |
| `DEFAULT_PDF_CAP` | 10 | code: [app/ingest/run.py](../app/ingest/run.py) (`pdf_cap`) | PDFs linked from crawled pages (menus, price lists), on top of `max-pages` |
| `MAX_PDF_BYTES` | 20 MB | code: [app/ingest/fetch.py](../app/ingest/fetch.py) | Bigger PDFs are skipped (brochures, scans) |
| `DEFAULT_BLOG_CAP` | 20 | code: [app/ingest/discover.py](../app/ingest/discover.py) | At most 20 `/blog` or `/journal` posts; blogs are stale and noisy for "what do you offer" questions (K1) |
| Priority order (K1) | start page → depth-1 → deeper → blog | code: `prioritize()` in discover.py | What survives a small `max-pages`; the pilot's bug was a sitemap listing the journal first |
| `max_depth` | 3 | code: `crawl_business()` | Link-following depth when there is **no sitemap** (BFS fallback); sitemap crawls ignore it |
| Skipped URLs | images/css/js/fonts; `/cart /checkout /login /account /search` | code: `_SKIP_EXTENSIONS`, `_SKIP_PATH_SUBSTRINGS` | Keeps transactional/UI pages out. Adding a site-specific skip needs a code change |
| Same-domain rule | bare domain, `www.` ignored | code: `same_domain()` | Other domains are never followed; a business with a second domain (booking site) needs its own crawl |
| `noindex` pages | skipped | code: `has_noindex()` | Respects the site owner's wish and drops junk landing pages |
| Sitemap handling | `sitemap.xml` + child sitemaps of an index, one level | code: `MAX_CHILD_SITEMAPS = 20` | A sitemap is the best page list; without one, BFS is slower and may miss orphan pages |
| robots.txt | obeyed | code: `RobotsChecker` | Disallowed URLs are never fetched (legal first, R-limits); `Crawl-delay` honoured, fractional values rounded up |

## 2. Politeness and fetching — speed vs. being blocked

| Knob | Default | Where | Effect / trade-off |
|---|---|---|---|
| Delay between requests | 0.5 s (`delay_seconds`) | code: `make_client()` / `crawl_business()` | Total crawl time ≈ pages × delay + fetch time. 200 pages ≈ 100 s of pure waiting. Lower = faster but rude and likelier to be blocked |
| robots `Crawl-delay` | site's value | robots.txt | Only **raises** our delay, never lowers it, however long |
| Request timeout | 15 s | code: `fetch_text` / `make_client` | Slow sites stall the crawl up to this per page |
| `follow_redirects` | on | code, fetch.py | `/menu.pdf` redirecting to an HTML page is detected by response type |
| `USER_AGENT` | `prototypeRAG-crawler/0.1 (...)` | code: fetch.py | Honest bot name. Some bot rules reject it |
| `Accept` / `Accept-Language` headers | realistic browser values | code: `REQUEST_HEADERS` | Default `*/*` got a 403 on bhbarbershop.com (#121) |
| Sequential fetching | one page at a time | code | Parallel fetching would be faster but breaks the delay promise; not built |
| Rendering | `httpx` only, no JavaScript | code (DEC-36) | JS-built sites return empty pages → add Crawl4AI/headless rendering when hit (slower, heavier, needed for quality on such sites) |
| Retries / backoff | none | code | A failed page is skipped; a transient 5xx loses it until the next crawl |

## 3. Cleaning — signal vs. noise

| Knob | Default | Where | Effect / trade-off |
|---|---|---|---|
| Discarded tags | `script style noscript svg`, all `<form>` | code: [app/ingest/clean.py](../app/ingest/clean.py) | Removes non-content |
| Boilerplate keywords | `cookie banner popup modal newsletter` (class/id match) | code: `_BOILERPLATE_KEYWORDS` | Too broad drops real content (a body class `has-banner-image` once did); structural tags (`html head body main article`) are protected |
| Chrome tags | `nav footer header` kept **separately** as Markdown | code: `_CHROME_TAGS` | Footer holds addresses and hours, so it is kept once as a pseudo-page, not thrown away |
| Markdown conversion | headings ATX, images stripped | code: `markdownify` call | Headings drive chunk boundaries (section 5) |
| Title / site name | `<title>` → first `<h1>`; `og:site_name` for the brand | code: clean.py | Becomes the brand name shown in prompts |
| `content_hash` | per page | code | Unchanged pages skip organize/chunk/facts on re-crawl (big speed-up for re-crawls) |

## 4. Organizing — structure of the site (plan 07)

Mostly rules, no LLM — fast and predictable; the main place where **quality** is decided.

| Knob | Default | Where | Effect / trade-off |
|---|---|---|---|
| Site-chrome dedupe | text on ≥ 3 pages and ≥ 50 % of pages | code: `detect_site_chrome(min_pages=3, min_fraction=0.5)` in [app/ingest/organize.py](../app/ingest/organize.py) | One shared pseudo-page instead of the same footer in every chunk set. Too low removes real shared content; too high leaves duplicates |
| Location detection | from the footer address list + nav links | code: `_ADDRESS_BLOCK_RE`, `_NAV_LINK_RE` | Decides which locations exist; a footer in an unusual format = missed locations and wrong per-location answers (DEC-37/49) |
| Page-type rules | slug lists: blog/journal, legal, faq, policy, contact, careers, landing, about, campaign, dated slugs | code: `_*_SLUGS`, `_BLOG_PREFIXES`, `_CAMPAIGN_RE`, `_DATED_SLUG_RE` | Each site needs its own slugs; a wrong type changes retrieval (below) |
| `PAGE_TYPES` | fixed set | code: `PAGE_TYPES` | A page type drives `retrievable`, priority and fact skipping |
| Not retrievable types | `blog legal careers landing site_chrome` | code: `_NOT_RETRIEVABLE_TYPES` | Their chunks are never searched: removes stale/irrelevant text from answers; a wrongly typed page becomes invisible |
| `ORGANIZER_VERSION` | "3" | code | Bump to force re-organizing every page after a rule change |
| Testimonials cut | section regex | code: `_TESTIMONIAL_SECTION_RE`, `cut_testimonials` | Reviews aren't facts; keeps praise out of answers (used for the summary instead) |
| Junk chunk filter (K5) | drop `< 40` chars after unwrapping links, and "Previous / Next / Read More / Back / Home" | code: `is_junk_chunk(min_chars=40)` | Cuts navigation debris; too high loses short real facts ("Open daily 9–12") |
| Chunk prefix | business + location name | code: `chunk_prefix` | Each chunk carries who/where it is about → better embedding match and fewer wrong-branch answers |

## 5. Chunking — the unit of retrieval

| Knob | Default | Where | Effect / trade-off |
|---|---|---|---|
| Split on headings | `#`…`######` | code: `split_by_headers` in [app/ingest/chunk.py](../app/ingest/chunk.py) | Chunks follow the page's own sections, each keeps its heading |
| `target_tokens` | 650 (≈ 2600 chars, 4 chars/token estimate) | code: `chunk_markdown` / `chunk_text` | Smaller = more precise match and shorter tool results (faster voice replies) but answers get split; larger = more context per hit, slower LLM, vaguer embeddings |
| `overlap_ratio` | 0.125 | code: `chunk_text` | Overlap between neighbouring chunks of one section; protects answers across a cut, costs index size |
| Paragraph packing / hard split | paragraphs packed up to the target; oversized paragraphs split on words | code | Never cuts mid-word |
| Changing any of these | needs re-chunk + re-embed | — | Bump `ORGANIZER_VERSION` (or re-crawl with changes) then `python -m app.rag.index` |

## 6. LLM extraction steps (profile, facts, summary, timezone)

The only slow, model-dependent part of the crawl. All go through [app/llm.py](../app/llm.py)
(`chat_json` with a JSON schema), so the model and its thinking mode matter.

| Knob | Default | Where | Effect / trade-off |
|---|---|---|---|
| `LLM_MODEL` | `google/gemma-4-12b` | env | Profile, summary, timezone. Fast, ok quality |
| `FACTS_LLM_MODEL` | empty = `LLM_MODEL` | env | Fact extraction is the heaviest call (many pages) and the most quality-critical; a stronger model (e.g. `qwen/qwen3.6-35b-a3b`, thinking on) improves facts at a cost of minutes per crawl. Audit candidates with `--only facts --dry-run --model ID` (OPEN-20) |
| `--no-profile` | off | CLI | Skips profile extraction (faster, no business profile) |
| Profile input | first 12 000 chars of top pages | code: [app/ingest/profile.py](../app/ingest/profile.py) | More text = more complete profile, slower, risk of mixing branches |
| `SECTION_CHARS` | 12 000 (≈ 3k tokens) | code: [app/ingest/facts.py](../app/ingest/facts.py) | Page text is cut into sections at headings for fact extraction; smaller = faster calls, more of them, facts may miss context |
| `FACT_TYPES` | address hours contact price service amenity policy booking capacity accessibility event about location_status | code | The vocabulary of the facts library; a missing type is a missing kind of answer |
| `TIME_SENSITIVE_TYPES` | hours price booking event location_status | code | Facts that go stale (esp. from blog posts) are down-ranked |
| `SKIP_PAGE_TYPES` | legal, careers | code | Never mined for facts |
| `EXTRACTOR_VERSION` | "1" | code | Bump to re-extract every page after a prompt/schema change |
| `--only facts` / `--only summary` / `--force` | — | CLI | Re-run one step without re-fetching; `--force` ignores the "nothing changed" check |
| Fact merge (`PAGE_PRIORITY`) | location/contact/pricing/service/chrome 0 → faq/policy 1 → about 2 → landing/event/other 3 → blog 4 | code: [app/ingest/merge.py](../app/ingest/merge.py) | When pages disagree (price in the FAQ vs on the blog) the higher-priority source wins; `--report conflicts` lists disagreements to review |
| Fact chunks | one `kind='fact'` chunk per active fact | code: [app/ingest/fact_chunks.py](../app/ingest/fact_chunks.py) | Short, quoted, source-linked context; retrieved with a ×1.5 boost ([VOICE_TUNING.md](VOICE_TUNING.md) §5) |
| Summary limits | one-liner 20 words, description 80, ≤ 5 highlights, ≤ 4 guest themes; 2500 chars per page, 12 000 total; sources = `about`, `location` pages | code: [app/ingest/summary.py](../app/ingest/summary.py) | Short summary in every prompt (prompt size vs. richness); it must contain no prices, hours or numbers |
| Timezone | LLM proposes an IANA zone, `zoneinfo` validates, only empty fields are filled | code: [app/ingest/timezone.py](../app/ingest/timezone.py) | Wrong timezone = wrong "open now" (V22); owner corrections are never overwritten |
| Thinking mode | model default | external (LM Studio) | A thinking model is far slower per call but fine offline; `chat_json` reads JSON from `reasoning_content` if `content` is empty |
| Prompts | system prompts per step | prompt: `_SYSTEM_PROMPT` in profile / facts / summary / timezone | The main quality lever for what is extracted and how it is worded |

## 7. Google Places (listing data)

| Knob | Where | Effect |
|---|---|---|
| `PlacesClient` with field mask `id, displayName, formattedAddress, phone, regularOpeningHours, priceLevel, websiteUri` | code: [app/ingest/places.py](../app/ingest/places.py) | Official, structured hours/phone/address to cross-check the website. A client exists; it isn't wired into `crawl_business` yet. Billed per call (R-limits), so field mask = price |

## 8. Embedding (indexing)

| Knob | Default | Where | Effect / trade-off |
|---|---|---|---|
| `EMBED_MODEL` / `EMBED_DIM` | nomic-embed-text-v1.5 / 768 | env | Retrieval quality; changing it means re-embedding every chunk (`embed_model` is stored per chunk, stale ones are re-done) |
| `batch_size` | 64 | code: `index_business()` in [app/rag/index.py](../app/rag/index.py) | Larger = fewer requests, bigger memory spikes; one committed transaction per batch, so a failure keeps earlier batches |
| `search_document:` prefix | required by nomic | code | Queries use `search_query:`; missing prefixes quietly degrade retrieval |
| Only changed chunks are embedded | on | code | Re-crawls re-index only what changed |

## 9. Re-crawl behaviour and operations

| Factor | Effect |
|---|---|
| Idempotent re-crawl | Same domain → same `business_id`; unchanged `content_hash` pages are skipped; pages no longer linked are deleted |
| Re-crawl frequency | Not scheduled (stage 1: manual). Hours/prices go stale; stage 2/3 adds a schedule (Cloud Run Job) |
| Mac memory | The crawl itself is light; the LLM steps need the model loaded (`lms ps`) and a 27–29 GB drafting model alongside gemma can cause swapping |
| Crawler in own Docker image | Stage 3 (DEC-28 says native in stage 1) |
| Cost | Fetching is free; LLM steps free locally, pay-per-token with Gemini; Places API pay-per-call |

## 10. Database organization (Postgres + pgvector)

One database for vectors and app data (see [DECISIONS.md](DECISIONS.md)); every table
carries `business_id`. How the data is laid out decides what can be filtered cheaply, what
is re-done on a re-crawl, and how it scales. Schema: [app/db/models.py](../app/db/models.py),
migrations in `app/db/migrations/versions/`.

| Table | Holds | Tuning-relevant design |
|---|---|---|
| `businesses` | one per domain; brand name, timezone | Domain → same `business_id` on every re-crawl |
| `locations` | branches; address, timezone, `is_default` | Partial unique index: one default per business. Chunks/facts/pages point at a location or are business-wide (`NULL`) |
| `pages` | cleaned page Markdown, `chrome_markdown`, `page_type`, `location_id`, `retrievable`, `duplicate_of`, hashes | Source of truth for re-processing: `content_hash`, `organized_hash`, `extracted_hash` + versions make each step incremental. Markdown is never overwritten by organize |
| `chunks` | the retrieval unit: `kind` = scraped / custom_reply / fact, `text`, `section_heading`, `embedding`, `embed_model`, `tsv`, `location_id` | A check constraint ties each kind to exactly one source (page / custom reply / fact); composite FKs `(business_id, id)` make cross-tenant links impossible |
| `facts`, `fact_sources` | atomic sourced facts, `status`, `fact_key`, validity dates | Partial unique index: one **active** fact per `(business, fact_key)`; a re-crawl updates `last_seen_at` instead of duplicating |
| `custom_replies` | owner-written answers | Become `custom_reply` chunks, boosted ×2 |
| `business_profile`, `business_summaries` | profile fields, summary text | Always put into the prompt, not searched |
| `conversations`, `messages`, `action_log`, `messages_for_owner` | call/chat history and actions | Not part of retrieval; history loads into the LLM prompt |

| Knob | Default | Where | Effect / trade-off |
|---|---|---|---|
| Vector index | **none** (exact scan of the business's rows) | migrations | `chunks` has btree indexes on `business_id`, `page_id`, `fact_id`, `location_id` and a GIN index on `tsv`, but no HNSW/IVFFlat. Exact search is fine for hundreds to low thousands of chunks per business and is 100 % accurate; with many businesses or 100k+ chunks add HNSW (`vector_cosine_ops`) — faster, approximate, build time and memory (stage 3, see [06-scale.md](06-scale.md)) |
| `EMBED_DIM` | 768 | env + column type `Vector(768)` | Fixed at table creation; changing model dimensions means a migration and re-embedding (R6). Smaller dimensions = smaller table and faster search, slightly lower recall |
| `embed_model` per chunk | stored | column | Search only uses chunks embedded by the **current** model, so a model switch hides un-re-embedded chunks until `app.rag.index` has run |
| Full-text config | `to_tsvector('simple', text)`, stored generated column + GIN | code: `Chunk.tsv` | `simple` = no stemming or stopword removal, works for any language, but "bath" won't match "baths". A language-specific config would improve one language and hurt others |
| Row-level security | not yet; `business_id` filter in every query, `app.business_id` set per session | [app/db/session.py](../app/db/session.py) | Policies are enforced in stage 3 before a second business (stage 3, per CLAUDE.md; `business_id` is in every table from stage 1); the filter, not the DB, isolates tenants today |
| Cascades | page delete → its chunks; fact delete → its chunks; location delete → `SET NULL` | FKs | Deleting pages no longer linked removes their chunks automatically; a deleted location turns its chunks business-wide |
| `chunks.content_hash` / `pages.content_hash` | per row | columns | Lets re-crawl and re-index skip unchanged rows (speed) |
| Connection use | LLM calls run outside DB sessions | code: [app/ingest/timezone.py](../app/ingest/timezone.py) and others | Don't hold a connection while a slow model thinks |
| Table/index statistics | default autovacuum | external | After a bulk re-crawl run `ANALYZE` so the planner uses the indexes; matters once there is a vector index |
| Docker Postgres resources | defaults | external (compose) | Memory/`shared_buffers` limit vector and GIN speed at scale; Neon in stage 2 has its own limits and cold-start wake-up latency |

## 11. Data retrieval (hybrid search)

Code: [app/rag/retrieve.py](../app/rag/retrieve.py), prompt in [app/rag/prompt.py](../app/rag/prompt.py),
location rule in [app/rag/location.py](../app/rag/location.py). One search = embed the question
→ vector top 10 + keyword top 10 → fuse → boost → de-duplicate → top N chunks → numbered text
for the LLM.

| Step / knob | Default | Where | Effect / trade-off |
|---|---|---|---|
| Query embedding | `search_query: <question>` | code: `retrieve()` | One embedding call per search (~tens of ms locally); must match the document prefix and the embed model |
| Vector search | cosine distance, top 10, current `embed_model` only | code: `vector_search` | Finds meaning ("how much" ≈ "price"); misses exact names/numbers |
| Keyword search | `ts_rank`, **OR** of the question's words, top 10 | code: `keyword_search` | Finds names, numbers, rare words. OR instead of AND because stopwords are kept (AND of a natural question almost never matches); ranking keeps specific matches on top |
| Candidate depth | 10 + 10 | code: `top_k` | Larger pool = more chance that the right chunk reaches fusion, slightly slower |
| Fusion (RRF) | `1/(k+rank)`, `k = 60` | code: `reciprocal_rank_fusion` | Rank-based, so scores of the two methods needn't be comparable; chunks found by both rise. Lower `k` = top ranks dominate |
| Kind boosts | custom reply ×2.0, fact ×1.5 | code: `CUSTOM_REPLY_BOOST`, `FACT_BOOST` | Owner's words > extracted facts > scraped text. Applied only to chunks already retrieved |
| Fact de-duplication (DEC-47) | on | code: `_drop_fact_duplicates` | Page chunks that contain a returned fact's quote are skipped → less repeated text, shorter prompt |
| `top_n` | 5 (voice tool `SEARCH_TOP_N`; `/chat` default 5) | code: [app/voice/tools.py](../app/voice/tools.py), [app/rag/answer.py](../app/rag/answer.py) | Chunks given to the LLM. Fewer = faster, cheaper, risk of missing; more = noise and latency (≈ 3 s over 5 chunks, V16) |
| Location filter (DEC-49) | business-wide chunks + default location's | code: `_location_filter`, `default_location_id` | Fixed by the entry point, never guessed per question; wrong default = wrong-branch answers. No filter if the business has no locations. Phone number → location comes in stage 3 (OPEN-21) |
| What is searchable | only chunked content; non-`retrievable` pages (blog, legal, careers, landing, chrome, duplicates, noindex) have **no chunks** | code: `_rechunk_page` in run.py | Filtering at ingest, not at query time: nothing to tune at search, but a wrongly typed page is invisible until re-organized |
| Result format | `[n] heading\ntext`, no scores shown | code: `format_chunks` | Heading helps the LLM tell sections apart; empty result → "No matching information found" (the LLM then says it doesn't know) |
| Always-in-prompt data | profile, location card, summary | code: `build_prompt` / voice session | Common questions are answered without any search (fastest); every extra line also costs prompt tokens on every turn |
| Reranker | none | — | A cross-encoder rerank of the top 10–20 would raise precision; costs another model call (latency) |
| Query rewriting | none (the raw question / tool `query` is used) | — | Follow-up questions ("and on Sunday?") rely on the LLM putting full context into the tool's `query`; a rewrite step is an idea, not built |
| Metadata filters | location only | — | Could add page type or fact type (e.g. restrict "how much" to `price` facts) |
| `source` field (vector / keyword / both) | kept on each result | code: `RetrievedChunk` | Used in debug output to see which method found a chunk; "both" is the strongest signal |

## How to measure quality

- **Counts after a crawl:** pages by `page_type`, retrievable vs not, chunks per page,
  chunks per location, facts per type, conflicts (`--report conflicts`), junk dropped.
- **Spot-check:** open the stored chunks for known questions (hours of each location, a price,
  a policy); is the answer there, labelled with the right location?
- **Retrieval inspection:** call `retrieve()` for a known question and look at `source`, `kind`, `score` of each chunk; check the SQL plan (`EXPLAIN ANALYZE`) when search gets slow and see whether a vector index is needed.
- **Retrieval eval:** the question set in [02-local-rag.md](02-local-rag.md) — recall of the
  right chunk is the real test of any chunking/organize change.
- **Timings:** `LOG_LEVEL=INFO` prints one line per LLM call (`app.llm`); the crawl summary
  returned by `crawl_business` gives page and chunk counts.

## Where to start (largest effect first)

1. **Get the page list right:** sitemap present? `--max-pages` high enough? Locations and pricing pages not crowded out (K1)?
2. **Get locations right** (footer/nav detection) — everything per-location depends on it.
3. **Check page types** on the new site; add slugs; wrong types hide or promote the wrong pages.
4. **Use a stronger `FACTS_LLM_MODEL`** if facts are wrong or missing (offline, so latency is free).
5. **Tune chunk size** only with the retrieval eval in hand.
6. **Lower the crawl delay** only on a site you own or whose robots.txt allows it.
7. **Retrieval:** check the right chunk is in the top 5 for real questions before touching `top_n`, boosts or `k`; add a vector index only when the table is large.
8. **Add rendering** (Crawl4AI) only when a site returns empty HTML.

## Open ideas (not built)

Parallel fetching per host with a shared rate limit · retries with backoff · JS rendering
(Crawl4AI, DEC-36) · HNSW vector index and `ANALYZE` after bulk loads · reranker and query rewriting · page/fact-type filters in search · wiring Google Places into the crawl as a source of hours · scheduled
re-crawls with change reports · per-site rule overrides (slugs, skip paths) instead of code
constants · a crawl quality report command (counts, junk, missing locations) · a configurable
`target_tokens` / `max_pages` / blog cap in `.env`.
