# Data layer

Turns a crawled website into what the assistant can know: typed pages, locations, chunks,
embeddings, a facts library, a business profile and a business summary, all in Postgres +
pgvector; and serves it back through hybrid retrieval.

Written from the code on `main` (commit `75d3bfd`, 2026-11-02), not from the plans. Where the
code and `plan/07-knowledge-quality.md` differ, this file describes the code. Line numbers are
not quoted because they move; function names are.

## 1. Purpose and boundaries

**In the layer:** everything between "a page's cleaned Markdown is stored" and "a ranked list
of chunks is returned for a question".

| Owns | Does not own |
|---|---|
| Organizing pages (type, location, retrievable, duplicates, site chrome) | Discovering, fetching and cleaning pages ([crawler.md](crawler.md)) |
| Chunking and storing chunks | The answer prompt and the LLM answer ([voice.md](voice.md)) |
| Embedding (indexer) | The `openai` / embedding clients themselves ([integrations.md](integrations.md)) |
| Facts extraction, merge, conflicts, fact chunks | HTTP endpoints that expose any of this ([api.md](api.md)) |
| Business profile, summary, timezone per location | Bookings, SMS, call summaries ([actions.md](actions.md)) |
| Hybrid retrieval (vector + keyword, RRF, boosts) | The screens that show it ([web.md](web.md)) |
| DB schema, migrations, `tenant_session` | |
| Top-questions insights (pure clustering) | |

**Boundary inside `app/ingest/run.py`.** The CLI runs the crawl half (crawler layer) and then
calls this layer in order: `organize_business` → `_rechunk_page` per organized page →
`delete_pages_not_in` (crawler) → profile → `assign_timezones` → `extract_facts` → `summarize_business`
(→ greeting audio, Voice layer). `_rechunk_page`, `extract_facts` and the profile block
live in `run.py` but are Data-layer logic.

**Boundary with Voice.** `app/rag/retrieve.py` (this layer) returns `RetrievedChunk`s;
`app/rag/answer.py` and `app/rag/prompt.py` (Voice layer) turn them into a prompt.
`app/rag/location.py` sits on the line: `default_location_id` picks the retrieval scope (used
here and by callers), `format_locations` writes a prompt block (Voice layer use). It is
documented here because it reads only `locations`.

**`app/db/`** (models, migrations, session) is listed under this layer, but every other layer
reads and writes its own tables through it (actions: bookings, sessions, calendar; api:
conversations, messages). Only the Data-layer tables are described in detail below.

## 2. Modules and their roles

| Module | Role | LLM? | DB? |
|---|---|---|---|
| `app/ingest/organize.py` | Site-chrome dedupe into one pseudo-page, location detection from the footer/nav, page type by URL rules, retrievable flag, duplicate detection, blog dates, junk-chunk filter, testimonial cut, location prefix for chunks | No | Yes (`organize_business`) |
| `app/ingest/chunk.py` | Header-aware chunking: split on ATX headings, pack paragraphs to ~650 tokens (chars/4), 12.5% overlap on word boundaries | No | No |
| `app/ingest/store.py` (`replace_chunks` only) | `replace_chunks`: full replace of a page's `scraped` chunks. `upsert_page` and `delete_pages_not_in` in the same file belong to the Crawler layer ([crawler.md](crawler.md)) | No | Yes |
| `app/ingest/facts.py` | Per-page fact extraction with a strict JSON schema, code-side validation, hours parsing, backend choice (haiku / qwen / gemma) | Yes | Read only (`pages_to_extract` takes loaded pages) |
| `app/ingest/merge.py` | Store facts and sources, normalize fact keys, merge duplicates, pick the active fact per key, conflicts report | No | Yes |
| `app/ingest/fact_chunks.py` | One `chunks` row (`kind='fact'`) per active fact, kept in sync | No | Yes |
| `app/ingest/profile.py` | `business_profile` extraction from the first 12k chars of all pages; placeholder-name rules used app-wide | Yes | No (run.py writes) |
| `app/ingest/summary.py` | Business summary from root/about/location pages and testimonials, with no numbers and no customer names | Yes | Yes |
| `app/ingest/timezone.py` | IANA timezone per location from its address, validated with `zoneinfo` | Yes | Yes |
| `app/rag/index.py` | Indexer: embed chunks with no embedding or a stale `embed_model` | Embeddings | Yes |
| `app/rag/retrieve.py` | Hybrid retrieval: pgvector cosine + Postgres full-text, RRF, kind boosts, fact-duplicate drop | Embeddings | Read |
| `app/rag/location.py` | Default location of a business; "other locations" prompt block | No | Read |
| `app/rag/eval.py` | Eval script over `tests/eval/*.yaml`: retrieval and answer checks | Via answer.py | Read |
| `app/insights/questions.py` | Top questions: pair turns, greedy cosine clustering, medoid, rank. Pure numpy, no I/O | No | No |
| `app/db/models.py` | SQLAlchemy models for all tables; `EMBED_DIM = 768` | — | — |
| `app/db/session.py` | `engine`, `SessionLocal`, `tenant_session(business_id)` | — | — |
| `app/db/migrations/` | Alembic, `0001`–`0016` | — | — |

`app/ingest/places.py` (Google Places client) is in [integrations.md](integrations.md). It is
not called anywhere on `main` today; the profile keeps a `place_id` column for it (R9).

## 3. Data flow

### In

```
crawler (run.py crawl half)
  └─ upsert_page(url, title, markdown, content_hash, chrome_markdown, language)   → pages
      noindex_urls (in memory only)  ──────────────────────────────────┐
                                                                       ▼
organize_business(business_id, start_url, site_root, noindex_urls)
  ├─ detect_site_chrome(all pages' chrome_markdown) → pseudo-page "<root>/__site_chrome__"
  ├─ detect_locations_from_chrome → _upsert_locations                        → locations
  └─ _organize_pages: page_type, location_id, duplicate_of, retrievable,
                      published_at, organized_hash                           → pages
_rechunk_page (per organized page)
  cut_testimonials → pages.testimonials
  chunk_markdown → filter_junk_chunks → prefix "<Business> <Location> — "
  replace_chunks(kind='scraped', location_id)                                 → chunks
delete_pages_not_in [crawler] (only if ≥50% of stored pages were fetched)  — cascades to chunks, fact_sources
profile extraction (skipped if owner-confirmed)                              → business_profile, businesses.name
assign_timezones                                                             → locations.timezone, businesses.timezone
extract_facts
  pages_to_extract → extract_page_facts (parallel LLM) → apply_page_facts    → facts, fact_sources
  settle_facts (rekey + pick winners) → sync_fact_chunks                     → facts.status, chunks(kind='fact')
summarize_business (only if source pages changed)                            → business_summaries
```

Separately:

- `python -m app.rag.index --business-id …` embeds every chunk with `embedding IS NULL` or a
  different `embed_model`. **Not run by the crawl**; it is a manual step.
- `app/api/custom_replies.py` (api layer) writes one `kind='custom_reply'` chunk per owner
  reply and embeds it synchronously on create/update.

### Out

| Consumer | Reads |
|---|---|
| `retrieve()` ← `app/api/chat.py`, `app/voice/tools.py` (`search_business_info`), `app/rag/answer.py` | `chunks` (+ `fact_sources.quote` for dedupe) |
| `app/rag/answer.py`, `app/voice/session.py` (prompt) | `business_profile`, `business_summaries`, `locations` via `format_locations` |
| `app/voice/greeting_cache.py`, `app/api/businesses.py` | `is_placeholder_name`, `businesses.name`, profile name, default location |
| `app/api/business_settings.py` | `insights.questions` over `messages`, with embeddings from `app.llm.embed` |
| `--report conflicts` (CLI) | `facts`, `fact_sources` |

## 4. DB schema (Data-layer tables)

Every tenant table has `business_id` (FK `businesses.id ON DELETE CASCADE`). Child tables use
**composite FKs `(business_id, id)`** so a row can never point at another business's row, even
without RLS.

| Table | Key columns | Notes |
|---|---|---|
| `businesses` | `name`, `website`, `domain` UNIQUE, `timezone` (default `UTC`), `phone_numbers[]`, `settings` JSONB | `name` starts as `(pending)`; set from `og:site_name`, then profile name, then the start page title |
| `business_profile` | PK `business_id`; `name, address, phone, email, opening_hours` (JSONB), `booking_policy, price_range, languages[]`, `place_id`, `confirmed_at` | One row per business. `confirmed_at` set = never re-extracted (DEC-11) |
| `locations` | `name, url, address, timezone, is_default` | UNIQUE `(business_id, url)`; partial unique index: one `is_default` per business |
| `pages` | `url` UNIQUE per business, `title, markdown, chrome_markdown, content_hash, scraped_at`; organize metadata `page_type, location_id, language, retrievable, duplicate_of, published_at, organized_hash`; facts stamp `extracted_hash, extractor_version`; `testimonials[]` | `markdown` is never rewritten by organize (DEC-09). `scraped_at` moves only when `content_hash` changes |
| `chunks` | `kind` ∈ `scraped / custom_reply / fact`; exactly one of `page_id / custom_reply_id / fact_id` (check constraint); `location_id, chunk_index, section_heading, text, embedding vector(768), embed_model, tsv` (generated `to_tsvector('simple', text)`), `content_hash` | GIN index on `tsv`. **No ANN index on `embedding`** (exact scan) |
| `facts` | `location_id, type, subject, attribute, value, value_json, statement, fact_key, status, origin, confidence, valid_from, valid_until, first_seen_at, last_seen_at, owner_confirmed_at` | Partial unique index `(business_id, fact_key) WHERE status='active'` |
| `fact_sources` | `fact_id, page_id, quote, page_hash, extracted_at` | One row per (fact, page). Cascades from both sides |
| `business_summaries` | PK `business_id`; `one_liner, description, highlights[], guest_themes[], tone, source_page_ids[], generated_at, owner_confirmed_at` | |
| `custom_replies` | `question, answer` | Written by the api layer; chunked as `custom_reply` |

### Migrations

Alembic in `app/db/migrations/versions/`, linear `0001` → `0016`. Data-layer ones:

| Rev | What |
|---|---|
| `0001` | `vector` extension; businesses, profile, pages, chunks, custom_replies, conversations, messages |
| `0002` | `businesses.domain`, `locations`; backfills one default location per business; refuses if two businesses collapse onto one domain; downgrade restores the old `website` |
| `0003` | `facts`, `fact_sources`, `business_summaries`; organize metadata on `pages`; `fact_id`/`location_id` and kind `fact` on `chunks` |
| `0004` | `pages.testimonials` |

`0005`–`0016` belong to conversations, actions and calendar (see [actions.md](actions.md)).
Changing `EMBED_DIM` needs a new migration.

## 5. Organizing pages

- **Site chrome (K2):** blocks (split on blank lines) that appear on ≥50% of pages and at least
  3 pages become one `site_chrome` pseudo-page at `<root>/__site_chrome__`. Not retrievable as
  page chunks, but it is read by fact extraction (that is where the opening hours live).
- **Locations (DEC-37):** regex on the chrome for `Name / street / City, ST 12345` blocks, page
  path from a matching nav link or a slug match. Existing rows are matched by URL first, then
  name, and renamed to the chrome's name. Exactly one default: the location matching the start
  URL, else the first found, else one named after the business.
- **Page type:** URL rules only (`classify_page_type`), no LLM. Types: location, service,
  pricing, faq, policy, contact, about, blog, event, legal, careers, landing, site_chrome,
  other.
- **Retrievable:** false for blog, legal, careers, landing, site_chrome, duplicates and noindex
  pages.
- **Duplicates:** pages with the same `content_hash`; the shortest URL is canonical, the
  others get `duplicate_of`.
- **Idempotency:** a page is skipped when `organized_hash == "<content_hash>:<ORGANIZER_VERSION>"`
  and its duplicate status hasn't changed (unless this run saw noindex on a still-retrievable
  page). Bump `ORGANIZER_VERSION` (now `3`) to re-organize everything.
- **Before chunking:** testimonial blocks are cut out (kept in `pages.testimonials`), junk
  chunks (`Previous`/`Next`/`Read More`, under 40 chars once links are stripped) are dropped,
  and location pages' chunks get the `"<Business> <Location> — "` prefix and `location_id`.

## 6. Embeddings

- Model from `EMBED_MODEL` (default `text-embedding-nomic-embed-text-v1.5` on LM Studio),
  dimension `EMBED_DIM=768` sent as `dimensions` (Gemini honours it, LM Studio ignores it).
- nomic task prefixes: `search_document: ` when indexing (index.py, custom_replies.py),
  `search_query: ` for the question (retrieve.py).
- `chunks.embed_model` records which model made each vector. The indexer re-embeds chunks whose
  model differs; `vector_search` only considers chunks from the current model (R6), so a
  half-done re-index loses vector hits, never mixes spaces.
- Indexer commits one transaction per batch of 64, so a failed batch doesn't roll back earlier
  ones; failed batches are counted and retried next run.
- Changed content clears the vector: `replace_chunks` recreates page chunks with
  `embedding=NULL`; `sync_fact_chunks` sets it to NULL when a fact's statement or location
  changes.
- Insights embed questions on the fly (api layer) and never store them.

## 7. Facts extraction

`extract_facts` in `run.py` drives it; `facts.py` holds the logic.

1. **Which pages:** `pages_to_extract`: not a duplicate, has a page type, not legal/careers,
   and `extracted_hash != "<content_hash>:<EXTRACTOR_VERSION>"` (or `--force`). The site-chrome
   page is included. An unchanged site gives an empty list.
2. **Which model (D21-C):** `--facts-model` / `FACTS_MODEL_CHOICE` (`haiku` cloud via
   Anthropic's OpenAI-compatible endpoint, `qwen` local thinking, `gemma` local), else asked on a
   terminal, else `FACTS_LLM_MODEL` / `LLM_MODEL` with `response_format`. Thinking and cloud
   backends get the schema in the prompt (`prompted=True`) and `FACTS_THINKING_MAX_TOKENS`.
3. **Call:** pages longer than 12,000 chars are split at headings. Schema has
   `type` (13-value enum), `subject, attribute, value, location` (enum of `this`, `all`, the
   location names), `statement, quote, confidence`. Calls run `FACTS_PARALLEL` at a time in a
   thread pool, with a 900 s timeout; the DB writes stay on the main thread, page by page.
4. **Validation in code (`validate_fact`)**, because local models are weaker (R1):
   type in the enum; no empty or placeholder fields; the normalized `quote` must be a substring
   of the normalized page text; every number in `value` must appear in `quote`; time-sensitive
   types (hours, price, booking, event, location_status) from blog pages are rejected;
   `location` must map to a known location. Rejections are counted and printed with a reason.
5. **Hours:** `parse_hours` turns "daily 8am to 11:30pm"-style values into
   `{"mon": [["08:00","23:30"]], …}` in `value_json`; anything irregular stays text only.
6. **Same fact twice on one page** is dropped by identity (type, location, subject, attribute,
   value).

`--only facts --dry-run [--model ID]` prints what a model extracts and writes nothing.

## 8. Duplicates and conflicts

**Fact key** (`make_fact_key`): `type | location_id or "-" | subject | attribute`, where the
subject is lower-cased, stripped of punctuation and of the business name and the fact's own
location name (also with `'s`). So "Bathhouse Williamsburg day pass" and "Day Pass" share a key.
Values are compared with `normalize_value` (case, `$ , . ; : !` and spaces ignored).

**`apply_page_facts`** (per page): delete this page's old `fact_sources`; for each fact, join an
existing row with the same key and value (one more source; crawl-origin rows get the new
statement and the higher confidence) or insert a new row with `status='conflict'`; add a
`fact_sources` row with the quote and `page_hash`; stamp the page's `extracted_hash`.

**`settle_facts`** (once per run, after all pages): first `rekey_facts` recomputes keys (names
may have changed) and merges rows with the same key and value: owner fact wins, else the
earliest `first_seen_at`; sources move to the winner. Then per key:

| Case | Winner (`active`) | Others |
|---|---|---|
| An owner fact exists (`owner_confirmed_at` set or `origin='owner'`) | Latest-confirmed owner fact | Crawl facts: `conflict` if they still have a source, else `stale`. Other owner facts keep their status |
| No owner fact | Fact whose best source ranks highest: `PAGE_PRIORITY` (location/contact/pricing/service/site_chrome 0 · faq/policy 1 · about 2 · landing/event/other 3 · blog 4), then newest `published_at`/`scraped_at` | `conflict` with a source, `stale` without |

Statuses are demoted first, then promoted, so the partial unique index never sees two active
rows. The result depends only on facts and sources, not on page order, and a second run changes
nothing.

**Fact chunks:** `sync_fact_chunks` keeps exactly one `kind='fact'` chunk per active fact
(`text = statement`, same `location_id`) and deletes chunks of non-active facts, so a stale or
losing value is never retrieved.

**Page-level duplicates** are handled in organize (`duplicate_of`, section 5). **Retrieval-time
duplicates** are handled in `_drop_fact_duplicates` (section 9).

`python -m app.ingest.run --url … --report conflicts` prints each key that has a `conflict`
row, its active value and each losing value with the URLs that state it.

## 9. Retrieval

`retrieve(session, business_id, question, top_n=5, location_id=None)`:

1. Embed `search_query: <question>`.
2. `vector_search`: top 10 by cosine distance, only rows with an embedding from the current
   model.
3. `keyword_search`: top 10 by `ts_rank`, with the question's words OR-ed (`simple` config, no
   stemming, stopwords kept; DEC-12).
4. Both scoped by `business_id`, and with `location_id` to `location_id IS NULL OR = location_id`.
5. Reciprocal rank fusion, `k=60`.
6. Boosts among already-found chunks only: `custom_reply` ×2, `fact` ×1.5.
7. `_drop_fact_duplicates` (DEC-47): skip a scraped chunk that contains the quote of a fact in
   the top `top_n`, and skip any chunk whose normalized text repeats one already picked.
8. Return `RetrievedChunk(chunk_id, text, section_heading, kind, score, source)`, where source
   is `vector`, `keyword` or `both`.

Callers pass `default_location_id(...)` as the scope (DEC-49): every conversation is about the
business's default location until an entry point can choose one (OPEN-21).

## 10. Profile, summary, timezone

- **Profile:** one `chat_json` call over the first 12,000 chars of all crawled pages; nulls for
  anything not stated; placeholder strings (`null`, `n/a`, the field's own name) become null.
  Skipped when `confirmed_at` is set; `place_id` is preserved across re-crawls.
- **Summary:** sources are the site root, about and location pages, plus any page with
  testimonials; never duplicates, chrome, legal or careers. Customer names are removed before
  the call, and code drops any sentence or item with a digit or a customer name, then enforces
  word and item limits. Regenerated only when the source set changes or a source's `scraped_at`
  is newer than `generated_at`. Never overwrites an owner-confirmed summary, including one
  confirmed while the model was running.
- **Timezone:** per location with an address and no timezone yet (the default location falls
  back to the profile address); the LLM proposes an IANA name and `valid_timezone` keeps only
  geographic `Region/City` zones. `businesses.timezone` follows the default location.

## 11. Key functions

| Function | File | Contract |
|---|---|---|
| `tenant_session(business_id)` | db/session.py | One transaction; sets `app.business_id` with `set_config(..., true)`; commits or rolls back |
| `organize_business(...)` → `OrganizeResult` | ingest/organize.py | Returns only the page ids that need re-chunking |
| `chunk_markdown(md, target_tokens=650)` | ingest/chunk.py | Continuous `chunk_index` across the page |
| `replace_chunks(session, bid, page_id, drafts, location_id)` | ingest/store.py | Full replace of `scraped` chunks; embeddings cleared |
| `extract_page_facts(page, name, locations, ...)` → `PageResult` | ingest/facts.py | No DB writes; validated facts + rejected with reasons |
| `apply_page_facts(session, bid, page, facts, stats)` | ingest/merge.py | Replaces this page's sources |
| `settle_facts(session, bid, stats)` | ingest/merge.py | Idempotent; decides every status |
| `sync_fact_chunks(session, bid)` → `FactChunkStats` | ingest/fact_chunks.py | Idempotent; keeps embeddings of unchanged facts |
| `summarize_business(bid, force)` → status string | ingest/summary.py | `generated / unchanged / owner_confirmed / no_sources` |
| `assign_timezones(bid)` → count | ingest/timezone.py | Never overwrites a set timezone |
| `index_business(bid, batch_size=64)` | rag/index.py | `{chunks_indexed, batches_failed}` |
| `retrieve(session, bid, question, top_n, location_id)` | rag/retrieve.py | Caller owns the session |
| `top_questions(turns, vectors, limit, threshold=0.80)` | insights/questions.py | Pure; caller embeds |

## 12. Config

From `app/config.py` (env vars, `.env` locally):

| Var | Default | Used for |
|---|---|---|
| `DATABASE_URL` | `postgresql+psycopg://app:app@localhost:5432/app` | Engine |
| `EMBED_BASE_URL`, `EMBED_API_KEY`, `EMBED_MODEL`, `EMBED_DIM` | LM Studio, nomic v1.5, 768 | Indexer, retrieval, custom replies, insights |
| `LLM_MODEL` | `google/gemma-4-12b` | Profile, summary, timezone; facts fallback and `gemma` choice |
| `FACTS_LLM_MODEL` | empty (= `LLM_MODEL`) | Facts when no choice is made |
| `FACTS_MODEL_CHOICE` | empty (= ask on a terminal) | `haiku / qwen / gemma` |
| `FACTS_MAX_TOKENS` / `FACTS_THINKING_MAX_TOKENS` | 4000 / 16000 | Per-call cap |
| `FACTS_PARALLEL` | 4 | Concurrent fact calls |
| `FACTS_HAIKU_MODEL`, `FACTS_QWEN_MODEL` | `claude-haiku-5-5`, `qwen/qwen3.6-35b-a3b` | Backends |
| `ANTHROPIC_API_KEY`, `ANTHROPIC_BASE_URL` | empty, Anthropic `/v1/` | `haiku` only; a missing key fails before any page |

Code constants worth knowing: `ORGANIZER_VERSION`, `EXTRACTOR_VERSION` (bump to redo all
pages), `SECTION_CHARS=12000`, `FACT_TIMEOUT_SECONDS=900`, `PAGE_PRIORITY`, `CUSTOM_REPLY_BOOST`,
`FACT_BOOST`, `MIN_CRAWLED_FRACTION=0.5`, `DEFAULT_THRESHOLD=0.80` (insights).

## 13. Error handling

- **Per page, not per run:** a failed fact call is logged and its page keeps the old
  `extracted_hash`, so the next run retries it. A page re-crawled while its model call ran is
  skipped (hash check before writing). Ctrl-C cancels queued calls and keeps finished pages.
- **Steps are isolated in `crawl_business`:** profile, timezone, facts, summary and greeting are
  each wrapped in `try/except` that logs and continues; one failing doesn't stop the next.
- **LLM calls run outside DB transactions** (facts, summary, timezone) so no connection is held
  while a model thinks; results are re-checked against the live row before writing (page hash,
  summary confirmation, empty timezone).
- **Mass-delete guard:** pages are deleted only when this crawl fetched at least 50% of the
  stored pages; otherwise the deletes are skipped and reported.
- **Bad model output never reaches the DB unchecked:** schema enums, quote/number checks,
  placeholder filter, `zoneinfo` check, digit/name filter on summaries.
- **Indexer:** one transaction per batch; failures counted, not raised.
- **Tenant safety:** composite FKs reject cross-business links; `tenant_session` validates the
  UUID and scopes `app.business_id` to the transaction so it never leaks across pooled
  connections (R20).

## 14. Known gaps

Seen in the code on `main`; none of these is a bug report against a decision.

| Gap | Effect |
|---|---|
| Indexing is a separate manual step (`python -m app.rag.index`) | New or changed page and fact chunks have no embedding after a crawl: keyword search finds them, vector search doesn't, until the indexer runs |
| No ANN index on `chunks.embedding` | Exact cosine scan per query; fine for one business, a cost at stage 3 scale |
| RLS not enabled | `app.business_id` is set but no policy reads it; isolation is the `WHERE business_id` in every query plus composite FKs (stage 3 per DEC) |
| `_business_by_domain` uses a plain `SessionLocal` | Cross-tenant read by design (business id unknown yet); needs an exception once RLS is on |
| Page type rules are hard-coded pilot slugs (`day-pass`, `rooftop-pool-nyc`, …) | A new business's pages mostly become `other` (still retrievable, priority 3 in merge) |
| Location regex expects a US `City, ST 12345` address in the chrome | Non-US or differently laid-out sites get one default location named after the business |
| Only the default location is ever used for retrieval (OPEN-21) | Other locations' chunks and facts are never retrieved |
| `noindex` is not persisted | A later run that doesn't re-fetch can't re-apply it |
| No owner editing of facts on `main` | `origin='owner'` and `owner_confirmed_at` are honoured by merge but nothing writes them; `rejected` status and `valid_from`/`valid_until` are never set; default status `candidate` is never used (inserts use `conflict`) |
| Profile reads only the first 12,000 chars of all pages joined | Facts on later pages don't reach `business_profile` (the facts library covers them) |
| `business_profile.opening_hours` is JSONB but extracted as a string | Stored as a JSON string, not structured hours |
| Facts are re-settled only by the facts step | If the facts step fails after pages were deleted, facts that lost their sources stay `active` (and in retrieval) until the next successful run |
| Chunk size uses chars/4, no tokenizer | Sizes are approximate for non-English text |
| `places.py` is not wired in | `place_id` is never filled by the crawl |

## 15. Depends on / Used by

**Depends on**

| Layer | What |
|---|---|
| [crawler.md](crawler.md) | Stored `pages` rows (`markdown`, `chrome_markdown`, `content_hash`, `language`), `noindex_urls`, `start_url`/site root; `content_hash` from `clean.py`; `domain_of`/`site_root` from `identity.py` |
| [integrations.md](integrations.md) | `app/llm.py`: `chat_json`, `embed`, `get_anthropic_client` |
| `app/config.py` (shared, not a layer) | `get_settings()`: database, embedding and facts settings (section 12) |
| [voice.md](voice.md) | Only one call back: `run.py` calls `app.voice.greeting_cache.pregenerate` after the summary (crawl orchestration, not Data-layer logic); `app/rag/eval.py` imports `answer_question` from `app/rag/answer.py` |

**Used by**

| Layer | What |
|---|---|
| [voice.md](voice.md) | `retrieve` (via `search_business_info` in `voice/tools.py` and `rag/answer.py`), `default_location_id`, `format_locations`, `business_profile`, `business_summaries`, `is_placeholder_name` (greeting) |
| [api.md](api.md) | `retrieve` + `default_location_id` (`api/chat.py`), `insights.questions` (`api/business_settings.py`), `is_placeholder_name` (`api/businesses.py`), `custom_replies` writes `custom_reply` chunks and embeds them |
| [actions.md](actions.md) | `app/db` models and `SessionLocal`/`tenant_session` for its own tables; `businesses.timezone` (set from the default location here) for booking times |
| [web.md](web.md) | Indirectly, through the API: business list, default location, top questions |
| [crawler.md](crawler.md) | `organize.chrome_pseudo_url` is called from the crawl half of `run.py` (the crawler's own `store.upsert_page`/`delete_pages_not_in` write `pages`, defined in this layer's schema) |
| [integrations.md](integrations.md) | Violation V-1: `app/voice/twilio_numbers.py` imports `app.db.models` (`Business`, `BusinessProfile`) and `SessionLocal` and reads businesses across tenants |

## 16. Tests

`tests/test_facts.py`, `test_fact_chunks.py`, `test_summary.py`, `test_rag.py`,
`test_location_scope.py`, `test_custom_replies.py`, `test_embed_dim.py`, `test_top_questions.py`,
`test_crawler.py` (organize, chunk, store) and `tests/eval/` for `app.rag.eval`.
