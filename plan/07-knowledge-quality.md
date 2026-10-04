# Part 7 — Knowledge quality (facts library, business summary, organize step)

**Goal:** turn a crawled website into **consistent, answer-ready knowledge**: short,
sourced, location-scoped **facts** (where, when, how, how much, how many) as the main
retrieval library; a short **business summary** (what it is, highlights, tone, what
guests say) instead of answer-ready review/marketing text; and an **organize / clean**
step between crawl and index that is safe to re-run on every re-crawl.

**Done when** (pilot business, stage 1, local):
- The whole pilot domain is crawled (service and location pages before blog posts) and
  every page has a type, a location (or "business-wide") and a language.
- `facts` holds the pilot's prices, hours, addresses, phones, amenities, treatments
  (with durations), policies — each with a source page and a verbatim quote that is
  found in that page.
- Voice and chat answer from facts first; raw chunks only as fallback (OPEN-22).
- The extended eval (`tests/eval/bathhouse.yaml`) passes, incl. hours (V23) and
  per-location questions; "current local time" uses the location's timezone (V22).
- Tool results for `search_business_info` are measurably shorter, and the post-search
  time to first sentence (V16) is re-measured and recorded.
- Re-running crawl + organize + extract on an unchanged site changes nothing;
  owner-confirmed facts are never overwritten (DEC-11).

Decisions: **DEC-37** (business = domain, sub-pages = locations), **DEC-38** (facts
library), **DEC-39** (business summary from website testimonials/marketing), **DEC-40**
(organize step) — reasons and options in [DECISIONS.md](DECISIONS.md). Open:
OPEN-20 … OPEN-23.

---

## 1. What we found in the pilot data (2026-10-04)

Read-only look at the stage-1 data (`pages`, `chunks`, `business_profile`) and the live
site. Details in [DECISIONS.md](DECISIONS.md) → "Knowledge from the discussion".

| # | Finding | Effect |
|---|---|---|
| K1 | The crawl stored **8 pages: `/williamsburg` + 7 `/journal/` blog posts** (`--max-pages 8` took the first sitemap entries; the sitemap lists the journal first). Not crawled: `/day-pass`, `/memberships`, `/treatments/*`, `/first-timers`, `/what-to-expect`, `/health-and-safety`, `/contact`, other locations | 1 of 8 pages has service facts; most chunks are blog prose |
| K2 | **Opening hours exist on the site** — in the site-wide `<footer>` ("Hours 7 days a week 8am to 11:30pm", all 4 locations' addresses and phones) and on `/contact`. `clean.py` drops `<footer>` as boilerplate | **V23 cause: data missing (removed by cleaning), not a retrieval miss.** `opening_hours` is null |
| K3 | The domain has **4 open locations** (Williamsburg, Flatiron, Atlantic Ave, Philadelphia) + coming-soon pages (Chicago, Berkeley Heights) | One business with locations, not one business per page (DEC-37) |
| K4 | Profile extraction joins all pages and cuts at 12,000 chars; it mis-files values (`booking_policy` = "Day Pass, Starts at $39") | One flat record can't hold "per location, per service" facts |
| K5 | 5 of 28 chunks are 11-char junk from Squarespace "Previous/Next" post links (`## [Title](/journal/…)` parsed as headings); the journal index page is teaser text | Junk competes in retrieval |
| K6 | The `/williamsburg` amenities chunk (2,589 chars) mixes pool temperatures, treatments, prices and **5 customer testimonials** | Big tool results (V16); opinions next to facts |
| K7 | Sitemap has 60 URLs incl. campaign/test pages (`ag1-*`, `ai-search*`, `ldv-iush`, `micro/membership`) and dated event pages (`aufguss-event-2026`) | Need page types and staleness, not "crawl everything equally" |

## 2. Pipeline with the new step

```
discover (priority order) ─► fetch ─► clean (main text + site chrome kept apart)
   ─► ORGANIZE: chrome dedupe · page type · location · language · near-dupes · staleness · junk filter
   ─► EXTRACT FACTS (per changed page, JSON schema, verbatim quote)  ─► MERGE (dedupe, conflicts, owner lock)
   ─► SUMMARY (testimonials + about/marketing → business_summaries)
   ─► CHUNK retrievable pages (fallback text) ─► INDEX (facts + chunks, same indexer)
```

Each stage is a function in `app/ingest/` and can be re-run alone
(`python -m app.ingest.run --business-id … --only organize|facts|summary`) so every
step stays inspectable (W8).

## 3. Business identity and locations (DEC-37, decided)

- **One business = one main website domain**: the website host without `www.`
  (`https://www.abathhouse.com/saunas`, `/massage`, `/williamsburg` → `abathhouse.com`;
  the path never matters). The crawler looks up `businesses.domain` and **reuses the same
  `business_id`** for the same domain — a re-crawl never creates a second business.
- Sub-pages are **pages of that business**, never separate businesses. Some of them are
  location pages (`/williamsburg`), most are not (`/saunas`, `/massage`): **locations
  come from the site's content** (the footer / a locations page listing addresses), found
  by the organize step and fact extraction — never from the crawl's start URL path.
- Every business has a **default location** (the one the start URL is about if that page
  is a location page, else the first found, else one named after the business); a
  single-location business has exactly one, the default.
- **Location-scoped facts** — address, phone, email, hours, timezone, prices that differ
  per site — belong to a location. Business-wide facts (brand policies, membership
  rules) have `location_id = NULL`.
- The schema (`businesses.domain`, `locations` with a default flag) is being built by a
  separate change; **this plan only adds foreign keys to `locations`** — composite
  `(business_id, location_id)` like every other child table — and does not define
  location columns. If `locations` carries address/hours columns, they are written
  **only** by the fact merge (§5.4) or the owner — one writer, no second copy to drift.
- Timezone (fixes **V22**): derived per location from its address fact (LLM proposes an
  IANA name, code validates it with `zoneinfo`), owner can correct. Voice uses the
  conversation's location timezone; `businesses.timezone` follows the default location
  until the identity change decides whether to keep it.
- Pilot effect: the business is **Bathhouse** (`abathhouse.com`); Williamsburg is the
  default location (DEC-31's pilot is unchanged — it now has 4 locations).
- Domain rule (user decision 2026-10-04): host, lowercased, leading `www.` removed — no
  Public Suffix List in stage 1. Other subdomains (`shop.example.com`) stay separate;
  revisit in stage 3 onboarding if a business needs several hosts merged (manual override
  or PSL) — see the knowledge table.

## 4. Organize / clean step (DEC-40)

Runs after fetch+clean, before extraction. Writes per-page metadata, never deletes
`pages.markdown` (raw cleaned text stays for re-runs).

| Task | How (simplest first) | Output |
|---|---|---|
| **Crawl priority** (crawler side) | Order sitemap/BFS URLs: start URL → location pages → depth-1 non-blog pages → service sub-pages → blog last (cap, e.g. 20 posts); skip legal/careers for extraction; honour `<meta name="robots" content="noindex">` as "not retrievable" | Fixes K1 |
| **Site chrome** (header/footer/nav) | `clean.py` returns main text **and** chrome text separately. Organize hashes chrome blocks across pages; a block on ≥ 50 % of pages (min 3) is chrome. Unique chrome is stored **once** as a pseudo-page (`page_type='site_chrome'`) → facts are extracted from it once; it is never chunked | Fixes K2 (hours, all locations' phones) without repeating them on every page |
| **Page type** | URL rules first (`/journal/`, `/privacy-policy`, `/terms-of-service`, `/careers`, `/contact`); LLM classification (JSON schema) for the rest | `pages.page_type` ∈ `location · service · pricing · faq · policy · contact · about · blog · event · legal · careers · landing · site_chrome · other` |
| **Location** | Page URL path matches a location slug → that location; pages listing several locations (`/contact`, chrome) → per-fact location from the extractor; else business-wide | `pages.location_id` (nullable) |
| **Language** | `<html lang>`; LLM classification as fallback | `pages.language` (pilot: `en`) |
| **Exact / near duplicates** | Exact: same `content_hash` at two URLs (`/home` vs `/`) → keep canonical (`<link rel=canonical>`, else shortest URL). Near: 5-word-shingle Jaccard ≥ 0.9 on main text, **never across different locations** (location pages share a template but not facts) | `pages.duplicate_of` |
| **Retrievable?** | `blog`, `legal`, `careers`, `landing`, duplicates, `noindex` → not retrievable as chunks (still summary input) | `pages.retrievable` |
| **Staleness** | Blog/journal post date → `published_at`; event pages with a past date → `valid_until`; time-sensitive facts (price, hours, event, promotion) are **not** taken from blog posts | Feeds fact `valid_from`/`valid_until` |
| **Chunk junk filter** | Drop chunks < ~40 chars or link-only; strip Markdown links from `section_heading`; drop "Previous / Next / Read More" blocks | Fixes K5 |
| **Testimonials** | Detect review blocks (quote + first name/initial, "Reviews" heading) → cut from chunk text, passed to the summary step | Fixes K6 (opinions out of facts/chunks) |

Re-run rules (idempotent):
- A page is re-organized/re-extracted only when its `content_hash` changed or
  `ORGANIZER_VERSION` / `EXTRACTOR_VERSION` (constants in code) changed — stored per page
  (`organized_hash`, `extracted_hash`, `extractor_version`).
- Classification results are deterministic per hash (same input → keep the stored
  result; no re-asking the LLM for unchanged pages).

## 5. Facts library (DEC-38)

### 5.1 What a fact is

One **atomic, self-contained, sourced statement** that answers where / when / how /
how much / how many, scoped to a location or the whole business. Example:

> "Bathhouse Williamsburg: the Banya runs between 185°F and 195°F."
> type `amenity` · subject `Banya` · attribute `temperature` · value `185–195°F` ·
> location Williamsburg · source `/williamsburg`, quote "the Banya runs between 185°F and 195°F"

Not a fact: opinions, testimonials, marketing adjectives ("best place to relax"),
history essays, advice ("rotate between pools") — those go to the summary (§6) or stay
in fallback chunks.

### 5.2 Schema sketch (stage 1 migration, after the `locations` change)

```sql
facts (
  id, business_id,
  location_id      NULL,      -- NULL = business-wide; FK (business_id, location_id) → locations
  type             TEXT,      -- address · hours · contact · price · service · amenity · policy
                              --  · booking · capacity · accessibility · event · about · location_status
  subject          TEXT,      -- entity: 'Day Pass', 'Full-Body Massage 50 min', 'Rooftop Pool'
  attribute        TEXT,      -- 'price', 'duration', 'temperature', 'hours', 'seats', 'phone' …
  value            TEXT,      -- human-readable: 'from $39', '8:00–23:30 daily'
  value_json       JSONB,     -- structured where it helps code: {"amount":39,"currency":"USD","qualifier":"from"},
                              --  hours {"mon":[["08:00","23:30"]],…}; NULL otherwise
  statement        TEXT,      -- self-contained sentence incl. business/location name — the embedding unit
  fact_key         TEXT,      -- normalized type|location|subject|attribute — dedupe key
  status           TEXT,      -- candidate · active · conflict · stale · rejected
  origin           TEXT,      -- crawl · owner
  confidence       REAL,      -- extractor score, raised by agreeing sources
  valid_from, valid_until    TIMESTAMPTZ NULL,
  first_seen_at, last_seen_at TIMESTAMPTZ,
  owner_confirmed_at          TIMESTAMPTZ NULL,   -- set → locked against crawl changes (DEC-11)
  UNIQUE (business_id, fact_key) WHERE status = 'active'
)
fact_sources (fact_id, business_id, page_id, quote TEXT, page_hash TEXT, extracted_at)  -- n per fact
business_summaries (business_id PK, …)  -- §6
pages     + page_type, location_id, language, retrievable, duplicate_of, published_at,
            organized_hash, extracted_hash, extractor_version
chunks    + kind 'fact' (and 'summary' only if OPEN-22 says so), fact_id, location_id
```

Facts are retrieved through **`chunks` rows with `kind='fact'`** (one per active fact,
`text = statement`), exactly like `custom_replies` → `kind='custom_reply'` today: the
indexer, `embed_model`/re-index logic (R6), `tsv` keyword search and `tenant_session`
work unchanged. The `facts` row is the structured truth; its chunk row is rebuilt when
the statement changes.

### 5.3 Extraction (per page, LLM structured output)

- Input: one page's main text (or the site-chrome pseudo-page), its `page_type`, the
  business's location names, the page date. Long pages split by section to ~3k tokens.
  Per page instead of today's "all pages, cut at 12,000 chars" (K4).
- Output (JSON schema via `chat_json`, DEC-05): list of
  `{type, subject, attribute, value, value_json?, location: "this" | "all" | "<location name>", statement, quote, valid_until?, confidence}`.
- **Validation in code** (correctness first — R1, local models are weaker):
  - `quote` must be found in the page text (whitespace/case-normalized) → else rejected.
  - Numbers in `value` must appear in `quote` (no invented prices/times).
  - Placeholder values (`null`, `n/a`, field name) → dropped (same rule as `profile.py`).
  - `type` must be in the enum; time-sensitive types from `blog` pages → rejected.
- Model: offline job → thinking allowed (DEC-29 / [02-local-rag.md](02-local-rag.md)
  prompt rules). Which local model: **OPEN-20**. Cloud: Gemini Flash (stage 2;
  cost ≈ cents per full pilot crawl — approximate, re-check).

### 5.4 Merge, dedupe, conflicts

- Same `fact_key` + same normalized value → one fact, one more `fact_sources` row,
  confidence up.
- Same `fact_key`, **different value** →
  1. owner-confirmed fact always wins; the crawl value becomes `conflict` and is shown
     to the owner (dashboard, step 5 / stage 3 onboarding);
  2. otherwise source priority by page type: `location · contact · pricing · service ·
     site_chrome` > `faq · policy` > `about` > `landing` > `blog`; tie → newest page;
  3. the loser is kept as `conflict` (not deleted) and listed by
     `python -m app.ingest.run --report conflicts`.
- Fact no longer found on any source page after a re-crawl → `stale` (excluded from
  retrieval); deleted after it stays stale for a set number of crawls — **unless
  owner-confirmed**: then it stays `active` and is flagged "source changed" for the owner.
- Core location card (address, phone, email, hours, timezone) = the active facts of
  those types per location; if `locations` has columns for them, the merge writes them.

### 5.5 Retrieval and prompt (DEC-12 hybrid stays)

- **Always in the prompt** (DEC-12: critical facts not left to search): the current
  location's core card + a one-line list of all locations (name, address) + the
  summary's one-liner (§6). For the pilot ≈ 150–250 tokens.
- **Retrieved:** hybrid search over `chunks` filtered by
  `business_id` and `location_id IS NULL OR location_id = :current_location`
  (+ explicit location if the question names one, OPEN-23). Boosts: `custom_reply` ×2 >
  `fact` ×1.5 > `scraped`. Only `retrievable` pages' chunks.
- **Tool result** (`search_business_info`): up to ~8 fact statements (≈ 20–40 tokens
  each) + at most 1–2 fallback chunks only if facts don't cover the query (OPEN-22);
  each line tagged with location. Target: < 40 % of today's tool-result tokens (V16, T1b).
- `business_profile` stays readable until the facts card passes the eval, then it is
  retired (rows derived from facts, or table dropped in a migration — decided then).
  DEC-11's rule moves from "the profile row is confirmed" to "each fact is confirmed".

## 6. Business summary from reviews and marketing (DEC-39)

- **Sources:** the business's own website only — home/about/location pages, marketing
  copy, journal intros, and **testimonials published on the site** (owner-curated, part
  of the website = DEC-11's source of truth). **Not** Google reviews: R9/DEC-11 —
  reviews are not stored, and a stored summary derived from Places review text is
  likely also not allowed (to verify under OPEN-04). A live Places rating lookup is a
  stage-3 idea at most, never stored.
- **Output** (`business_summaries`, one row per business; per-location later if needed):
  `one_liner` (≤ 20 words), `description` (≤ 80 words), `highlights` (≤ 5),
  `guest_themes` (≤ 4, e.g. "guests often mention friendly staff and cleanliness"),
  `tone` (formality, a few brand words), `source_page_ids`, `generated_at`,
  `owner_confirmed_at`.
- **Use:** in the system prompt (≤ ~120 tokens) for "what is this place / what's it
  like / is it good for…" and for the assistant's tone. **Not** a source of facts: the
  prompt says guest themes are opinions and must be attributed ("guests often say…");
  no prices, hours or numbers in the summary. Customer names from testimonials are
  never repeated (personal data, R16).
- Regenerated only when one of its source pages' hashes changed; owner-confirmed summary
  is not overwritten (shown as a suggested update instead).

## 7. Measuring answer quality before / after

| Metric | How | Baseline (today) | Target |
|---|---|---|---|
| Eval pass rate | `python -m app.rag.eval` on `tests/eval/bathhouse.yaml` | 7/7 on 7 questions | no regression; new questions pass |
| Extended eval | + hours (V23), per-location address/phone (Flatiron, Philadelphia), treatment price + duration, pool/sauna temperatures, rooftop pool in winter, membership, "is X open now" (timezone), out-of-scope, "blog trap" (history question must not be stated as a business fact) | run once on the old pipeline first and record | all pass |
| Retrieval hit@k | new eval field `expected_in_context`: fact substring present in the retrieved context | — | ≥ 90 % |
| Tool result size | tokens/chars of `search_business_info` output, logged per call | 5 chunks, up to ~2.6k chars each | < 40 % of baseline |
| Voice post-search time (V16) | mic test page `latency` events (search turns), 10+ turns | ≈ 3.7 s after the search, ≈ 5.7 s to first audio | measured and recorded; expected to drop |
| Fact precision | manual audit of 30 random facts: quote matches, type/location right | — | ≥ 90 % correct, **0 wrong prices/hours/addresses** |
| A/B | `RETRIEVAL_MODE=chunks|facts|both` env flag (debug only) on the same eval | — | decides OPEN-22 |

Eval runs with the local model and again with Gemini at step 6 (R1).

## 8. Code layout (planned)

```
app/ingest/
  organize.py   chrome dedupe, page type, location, language, near-dupes, staleness, junk filter
  facts.py      per-page extraction + validation (quote check, enum, placeholders)
  merge.py      fact_key, dedupe, conflict rules, owner lock, stale handling, location card
  summary.py    business summary from testimonials/marketing pages
  clean.py      + returns chrome text separately (no longer thrown away)
  discover.py   + priority order, noindex
  run.py        + --only organize|facts|summary, --report conflicts
app/rag/retrieve.py   + location filter, fact boost, retrievable pages only
app/rag/prompt.py     + location card, locations list, summary
app/voice/tools.py    + fact-first tool result, optional location argument
app/api/…             + GET /debug/facts?business_id=…&location=… (inspect facts, sources, conflicts)
```

## 9. Tasks

Place in the build order (DECISIONS.md section 1): an unnumbered **"Knowledge quality"
block after step 4 and before step 5** — it may overlap with step 4's Twilio part (it
touches `ingest/` and `rag/`, not the call loop), but must be done before step 5.

### Stage 1 — local, pilot business

- [ ] **Baseline:** extend `tests/eval/bathhouse.yaml` (§7), add `expected_in_context`
  and tool-result size logging to `rag/eval.py`; run on the current pipeline and record
  the numbers here
- [ ] Wait for / align with the identity change (DEC-37): `businesses.domain`,
  `locations` with default flag, crawler reuses `business_id` by domain
- [ ] Crawl priority order + blog cap + `noindex` — `discover.py`/`run.py` (K1)
- [ ] `clean.py` keeps chrome text separately; tests on a saved pilot HTML fixture with
  the footer (K2)
- [ ] Migration: `pages` metadata columns, `facts`, `fact_sources`, `business_summaries`,
  `chunks.kind` += `fact`, `chunks.fact_id`, `chunks.location_id` (composite FKs)
- [ ] `organize.py`: chrome pseudo-page, page type (URL rules + LLM), location,
  language, duplicates, staleness, junk-chunk filter, testimonial cut — fixture tests
- [ ] Decide OPEN-20 (extraction model): run `facts.py` on 5 pilot pages with both
  candidates, audit precision
- [ ] `facts.py` + `merge.py`: extraction, quote/number validation, dedupe, conflicts,
  stale handling, owner lock; idempotency test (second run = no changes)
- [ ] Re-crawl the whole pilot domain; audit 30 facts (§7); `/debug/facts`
- [ ] Location card + timezone per location → prompt and voice "current local time"
  (V22); re-check V23 (hours now in the data)
- [ ] Retrieval: fact chunks, location filter, boosts, retrievable pages only; tool
  result fact-first; optional `location` argument (OPEN-23)
- [ ] `summary.py` + prompt (≤ ~120 tokens), guest themes attributed, no names
- [ ] Run extended eval + A/B (`RETRIEVAL_MODE`) → decide OPEN-22; re-measure V16 on the
  mic test page; record numbers here and in [03-voice-channel.md](03-voice-channel.md)
- [ ] Retire `business_profile` from the prompt once the location card passes the eval
- [ ] Update [01-crawler.md](01-crawler.md) / [02-local-rag.md](02-local-rag.md) data-model
  sections to the built state

### Stage 2 — cloud, one pilot

- [ ] Crawler Cloud Run Job runs organize + extract + summary after the crawl (same CLI)
- [ ] Re-run extraction with Gemini Flash; compare fact precision with the local model;
  record cost per full crawl and per re-crawl (approximate)
- [ ] Re-index facts with Gemini embeddings (R6 — fact chunks re-embed like all chunks)

### Stage 3 — scale

- [ ] Owner review of facts, conflicts, stale facts and summary in onboarding
  ([06-scale.md](06-scale.md) step 3 "owner confirms/edits") — `owner_confirmed_at` per fact
- [ ] Multi-language sites: one fact per `fact_key`, statement in the site's main
  language; language-aware `tsv` only if keyword recall suffers (DEC-12 note)
- [ ] PDF menus/price lists (01-crawler task) feed `facts.py`
- [ ] Phone number per location vs per business (OPEN-21)
- [ ] Shared-host and multi-domain businesses (DEC-37 edge cases)

## 10. Open questions (details in [DECISIONS.md](DECISIONS.md))

| ID | Stage | Question | Needed before |
|---|---|---|---|
| OPEN-20 | 1 | Local model for fact extraction | `facts.py` |
| OPEN-21 | 3 | Phone number per location or per business | Assigning numbers at onboarding |
| OPEN-22 | 1 | Role of raw page chunks once facts exist | Retrieval change; decided by the A/B eval |
| OPEN-23 | 1 | How a conversation picks its location | Location-scoped retrieval and the per-location eval |
