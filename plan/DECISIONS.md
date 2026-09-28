# Decisions Log

Single place for **what we decided, why, what we rejected, and what is still open**.
Details live in the part plans; this file is the index of reasoning.

Related files:
- [ARCHITECTURE_DRIVERS.md](ARCHITECTURE_DRIVERS.md) — drivers (D1–D7), wants (W1–W9), restrictions (R1–R20)
- [PROJECT_PLAN.md](PROJECT_PLAN.md) — what we build · [DEVELOPMENT_PLAN.md](DEVELOPMENT_PLAN.md) — how
- Parts: [01 crawler](01-crawler.md) · [02 local RAG](02-local-rag.md) · [03 voice](03-voice-channel.md) · [04 actions](04-actions.md) · [05 cloud](05-cloud-migration.md)

How to use:
- New decision → add a row to the summary + a section with options table.
- Changing a decision → mark the old one **Superseded by DEC-xx**, don't delete it.
- A decision that breaks a driver must reference the restriction (R-xx) that forces it.

---

## 1. Priorities

### Decision priority (when goals conflict)

1. **Correctness & legal** — no wrong bookings, no data leaks between businesses, consent/disclosure, vendor terms.
2. **Caller experience** — voice latency, natural conversation, no dead air.
3. **One deployment for many businesses** (D6).
4. **Same tech local and cloud** (D2) — migration by env vars.
5. **Managed and simple** (D4) — few services, nothing to patch.
6. **Scale to zero / low cost** (D5).
7. **Local first** (D1) — learning and manual testing at ~zero cost.

### Build order

| Step | What | Why this order |
|---|---|---|
| 0 | Foundation: Docker Compose (API + Postgres/pgvector), Alembic, base tables + RLS | Every later part writes into these tables |
| 1 | Crawler → pages, profile, chunks in Postgres | Need real data before RAG |
| 2 | Local RAG: index, retrieval, `/chat`, eval questions, debug views | Core value; learn how RAG works |
| 3 | Voice: Twilio + Google Speech, ngrok | Builds on working RAG |
| 4 | Actions: tool calling, bookings, summaries, dashboard | Needs RAG + channels |
| 5 | Quality check with Gemini (env var switch, still local) | Validate before paying for hosting |
| 6 | Cloud: Cloud Run + Neon + Gemini | Only after it works locally |
| 7 | Second business with no code changes | Proves multi-tenancy |

---

## 2. Decision summary

Status: ✅ Decided · 🔄 Decided, revisit at trigger · ❓ Open (see section 4)

| ID | Topic | Decision | Status |
|---|---|---|---|
| DEC-01 | Tenancy model | One deployment, many businesses (`business_id`) | ✅ |
| DEC-02 | Vector store | Postgres + pgvector (one DB for everything) | ✅ |
| DEC-03 | Cloud DB hosting | Neon free tier → Cloud SQL when live | 🔄 |
| DEC-04 | Tenant isolation | Shared tables + `business_id` + Row-Level Security | ✅ |
| DEC-05 | LLM interface | `openai` client against OpenAI-compatible endpoints | ✅ |
| DEC-06 | Local LLM runtime | Ollama, native on Mac, general instruct model | ✅ |
| DEC-07 | Cloud LLM | Gemini Flash, paid key, Gemini API first | 🔄 |
| DEC-08 | Embeddings | `nomic-embed-text` local, `gemini-embedding-001` cloud, 768 dims, re-index on switch | 🔄 |
| DEC-09 | Crawled content storage | Postgres tables, no file storage | ✅ |
| DEC-10 | Crawler tech | Crawl4AI + httpx fallback, separate crawler image | ✅ |
| DEC-11 | Google listing data | Places API, not scraping; storage limited by terms | ❓ (R9) |
| DEC-12 | Retrieval | Hybrid: pgvector + Postgres full-text, rank fusion, exact search per business | ✅ |
| DEC-13 | Speech | Google STT/TTS, also locally; Gemini Live evaluated later | ✅ |
| DEC-14 | Telephony | Twilio Media Streams; ngrok locally | 🔄 |
| DEC-15 | Compute | Cloud Run (API) + Cloud Run Jobs + Scheduler (crawler) | ✅ |
| DEC-16 | Voice cold start | `min-instances=1` for voice in production | ✅ |
| DEC-17 | DB wake-up on call | Lookup in Twilio webhook (caller hears ringing); pre-generated greeting | 🔄 |
| DEC-18 | Actions | LLM tool calling; confirmation enforced in code; internal bookings first | ✅ |
| DEC-19 | Dashboard | FastAPI + Jinja + HTMX, no SPA | ✅ |
| DEC-20 | Frameworks | No LangChain / LlamaIndex | ✅ |
| DEC-21 | Secrets | `.env` locally, Secret Manager in cloud | ✅ |
| DEC-22 | Recordings | Transcripts only; audio recording off by default | 🔄 (R16) |
| DEC-23 | Docs layout | Flat `plan/` folder | ✅ |

---

## 3. Decisions with options

### DEC-01 — Tenancy model

| Option | Pros | Cons | Verdict |
|---|---|---|---|
| **One deployment, many businesses** | Near-zero cost per business; one codebase to update | Must enforce isolation (DEC-04) | ✅ |
| Separate RAG stack per business | Strong isolation | Cost and ops multiply per business; N deployments to update | ❌ |

Reason: D5, D6, W7. Onboarding = DB rows + phone number.

### DEC-02 — Vector store

| Option | Local | Managed cloud | Pros | Cons | Verdict |
|---|---|---|---|---|---|
| **Postgres + pgvector** | Docker | Neon / Supabase / Cloud SQL | One DB for vectors + data; SQL transactions; full-text for hybrid; same everywhere | Not built for 100M+ vectors | ✅ |
| Qdrant | Docker | Qdrant Cloud / self-hosted VM | Fast, great filtering | Second DB; not GCP-managed; data sync | ❌ |
| Firestore + Qdrant | Emulator + Docker | Firestore + Qdrant Cloud | Firestore scales to zero | Two DBs; no SQL transactions for bookings; no single-query hybrid search | ❌ |
| Vertex AI Vector Search | — | Vertex | Massive scale | No local version; always-on endpoint cost | ❌ |
| Chroma / FAISS | Embedded | — | Zero setup | No managed cloud equivalent; no relational data | ❌ |

Reason: D2, D4, D7, R17. Scale fits: hundreds–thousands of chunks per business.

### DEC-03 — Cloud database hosting

| Option | Cost | Pros | Cons | Verdict |
|---|---|---|---|---|
| **Neon** | Free tier → usage | Scales to zero; branching; pooler | ~0.5 s wake after idle; outside GCP | ✅ start here |
| Supabase | Free → ~$25/mo | Generous tier, dashboard | Free projects pause after ~1 week idle — bad for a phone line | ❌ |
| **Cloud SQL** | ~$10/mo min | Inside GCP (IAM, one bill), HA, backups | Always-on cost | ✅ later |
| Postgres/Qdrant on Spot VM | ~$5/mo | Cheapest | Self-managed; Spot can be stopped mid-call | ❌ |

Revisit trigger: first live business, or measured wake-up latency hurts calls (R3, R13).
Move = `pg_dump`/restore + new `DATABASE_URL`.

### DEC-04 — Tenant isolation

| Option | Pros | Cons | Verdict |
|---|---|---|---|
| **Shared tables + `business_id` + RLS** | One migration path; simple admin queries; DB enforces isolation | Needs non-owner role, `FORCE RLS`, `SET LOCAL` per transaction | ✅ |
| Shared tables, app filter only | Simplest | One missing `WHERE` leaks data | ❌ |
| Schema per business | Strong isolation, easy tenant drop | Migrations × N; catalog bloat; harder cross-tenant admin | ❌ |
| Database per business | Strongest isolation | Cost and ops per business | ❌ |

Implementation: see R18 in [ARCHITECTURE_DRIVERS.md](ARCHITECTURE_DRIVERS.md).

### DEC-05 — LLM interface

| Option | Pros | Cons | Verdict |
|---|---|---|---|
| **`openai` client, OpenAI-compatible endpoints** | Ollama and Gemini both support it (chat, embeddings, tools); switch = env vars | Doesn't cover Speech / Gemini Live (R5) | ✅ |
| LangChain / LlamaIndex | Many integrations | Heavy, hides what happens (bad for learning), version churn | ❌ |
| Own provider classes per vendor | Full control | Custom code to maintain | ❌ |
| LiteLLM proxy | Many providers | Extra layer not needed for 2 providers | ❌ |

### DEC-06 — Local LLM runtime

| Option | Pros | Cons | Verdict |
|---|---|---|---|
| **Ollama, native on Mac** | Uses Apple GPU; OpenAI-compatible API | Not in Docker (R7) | ✅ |
| Ollama in Docker on Mac | Everything in Compose | CPU only, very slow | ❌ |
| LM Studio / llama.cpp server | Also OpenAI-compatible | No advantage for us | — (fallback) |

Model: general instruct model with tool calling (e.g. `llama3.1:8b`, Qwen instruct).
**Not** a coding model like `qwen2.5-coder`.

### DEC-07 — Cloud LLM

| Option | Pros | Cons | Verdict |
|---|---|---|---|
| **Gemini Flash via Gemini API (paid key)** | Cheap per token; OpenAI-compatible; simplest setup | API key auth; fewer enterprise controls | ✅ start |
| Gemini via Vertex AI | IAM/service account, regional data residency | More setup | 🔄 if R16 requires region control |
| Gemini free tier | $0 | Data may be used by Google (R12) | ❌ for customer data |

Model name is checked at deploy time (current Flash model).

### DEC-08 — Embeddings

| Option | Pros | Cons | Verdict |
|---|---|---|---|
| **Local `nomic-embed-text`, cloud `gemini-embedding-001`, both 768 dims** | Fully local learning; same column size | Re-index on migration (R6) | ✅ current |
| Gemini embeddings everywhere (also locally) | Tested index = shipped index; no re-index | Small cloud cost/dependency locally | ❓ considering |

Re-index is cheap: chunk text is in Postgres; indexer re-embeds rows where `embed_model` differs.

### DEC-09 — Crawled content storage

| Option | Pros | Cons | Verdict |
|---|---|---|---|
| **Postgres tables (`pages`, `business_profile`, `chunks`)** | Same local/cloud; re-embed without re-crawl; queryable | Raw HTML not kept | ✅ |
| Local files → Cloud Storage | Keeps raw HTML | Storage switch local/cloud; second system | ❌ (add GCS later only if needed) |

### DEC-10 — Crawler

| Option | Pros | Cons | Verdict |
|---|---|---|---|
| **Crawl4AI** (headless browser, Markdown output) | Handles JS sites; clean Markdown | Heavy image | ✅ primary |
| httpx + BeautifulSoup | Light, fast | Fails on JS sites | ✅ fallback |
| Owner uploads | Works when crawling is blocked | Manual | ✅ fallback (R10) |

Runs in a **separate crawler image** as a Cloud Run Job, so the API image stays small (R8).

### DEC-11 — Google listing data

| Option | Pros | Cons | Verdict |
|---|---|---|---|
| **Places API** | Official, stable | Storage/caching limits (only `place_id` permanent) | ✅ source |
| Scraping Google Maps | "Free" | Violates ToS; breaks often | ❌ |

Open: what exactly we may store/cache → fetch live vs store (R9).

### DEC-12 — Retrieval

| Option | Verdict |
|---|---|
| **Hybrid: pgvector cosine + Postgres full-text (`tsvector`), reciprocal rank fusion, custom replies boosted** | ✅ |
| Vector only | ❌ misses exact names, prices, dish names |
| HNSW index | Later — exact search within one business is fast enough |

Critical facts (hours, address, phone) go into the prompt from `business_profile`, not via search.

### DEC-13 — Speech

| Option | Pros | Cons | Verdict |
|---|---|---|---|
| **Google Speech-to-Text / Text-to-Speech (also locally)** | Same local/cloud; good on phone audio; cents while testing | Cloud dependency in local dev (R4) | ✅ |
| Whisper + Piper locally | Free, offline | Local-only tech; weaker on 8 kHz audio; Piper fork is GPL | ❌ |
| Gemini Live API (audio in/out) | Fewer stages, lower latency | Newer; less control per stage | ❓ spike after classic pipeline works |

### DEC-14 — Telephony

| Option | Verdict |
|---|---|
| **Twilio + Media Streams (WebSocket)** | ✅ start — best docs, easy local testing with ngrok |
| Telnyx / Vonage / Plivo | 🔄 compare price & number availability for target country |

### DEC-15 — Compute

| Option | Pros | Cons | Verdict |
|---|---|---|---|
| **Cloud Run + Cloud Run Jobs + Cloud Scheduler** | Same Docker images; scale to zero; WebSockets; managed | 60 min request limit (R19) | ✅ |
| Compute Engine VM | Full control | Always-on, self-managed | ❌ |
| GKE (Kubernetes) | Powerful | Overkill | ❌ |
| Cloud Functions | Cheap | Poor fit for WebSockets / long calls | ❌ |

### DEC-16 — Voice cold start

`min-instances=1` for the service handling calls in production (few $/month); cold starts
tolerated in prototype. Reason: R2 — caller experience ranks above cost.

### DEC-17 — Database wake-up on call

| Option | Verdict |
|---|---|
| **Business lookup inside the Twilio webhook (caller hears ringing), pre-generated greeting audio per business** | ✅ |
| Generic "please wait while I load the assistant" message (Gemini suggestion) | ❌ worsens every call to hide a delay the caller doesn't hear |
| Always-on Cloud SQL | 🔄 if measured latency is still a problem |

### DEC-18 — Actions

- LLM **tool calling** (same `tools=[...]` for Ollama and Gemini).
- Confirmation before any booking is enforced **in code**, not only in the prompt.
- Caller phone from caller ID; availability re-checked in a DB transaction (R17).
- Booking backends in order: internal Postgres table → Google Calendar → Cal.com → OpenTable etc.
- Tool testing mainly with Gemini — small local models are unreliable at tool calls (R1).

### DEC-19 — Dashboard

FastAPI + Jinja templates + HTMX. No separate frontend build (React/SPA) — D4.

### DEC-20 — Frameworks

No LangChain / LlamaIndex. `openai` client + SQLAlchemy + SQL. Reason: learning (W8),
fewer dependencies, easier debugging.

### DEC-21 — Secrets

`.env` locally (never committed), Secret Manager in cloud mounted as env vars.

### DEC-22 — Recordings

Transcripts only by default; audio recording is a per-business opt-in later (needs Cloud
Storage + consent, R15/R16).

### DEC-23 — Docs layout

Flat `plan/` folder with overview files + numbered part files + this log.

---

## 4. Still considering (open questions)

| ID | Question | Options | Depends on / trigger | Needed before |
|---|---|---|---|---|
| OPEN-01 | Which business type first? | Restaurant (table booking) · appointment-based (salon, clinic) | Access to a real pilot business | Part 4 |
| OPEN-02 | Target country / region | EU (`europe-west1`) · US (`us-central1`) · other | Where pilot businesses are | Part 5, legal (R16) |
| OPEN-03 | Languages for voice | English only · + local language(s) | OPEN-02 | Part 3 (STT/TTS/model choice) |
| OPEN-04 | Places API data we may store | Store `place_id` only + live fetch · short cache | Read current Places terms (R9) | Part 1 |
| OPEN-05 | Embeddings locally | Local `nomic` + re-index · Gemini embeddings everywhere | Whether offline learning matters more than index parity | Part 2 |
| OPEN-06 | Specific local model | `llama3.1:8b` · Qwen instruct · other | Laptop RAM/GPU, eval results | Part 2 |
| OPEN-07 | Gemini API vs Vertex AI | API key (simple) · Vertex (IAM, region) | OPEN-02, data residency needs | Part 5 |
| OPEN-08 | Classic voice pipeline vs Gemini Live | STT→LLM→TTS · Gemini Live | Latency/quality measured in Part 3 | After Part 3 works |
| OPEN-09 | Telephony provider | Twilio · Telnyx · Vonage · Plivo | OPEN-02 (price, number availability) | Part 5 |
| OPEN-10 | Neon → Cloud SQL | Stay on Neon (paid) · Cloud SQL | First live business / call latency | Go-live |
| OPEN-11 | Code licence | Private, no licence · MIT · Apache 2.0 · AGPL | Public repo? commercial plans? | Before making repo public |
| OPEN-12 | Owner login for dashboard | Google sign-in · magic link | — | Before first real business |
| OPEN-13 | CI/CD + Terraform timing | From start · after first cloud deploy | Setup stability | Part 5 |
| OPEN-14 | Call recordings | Never · opt-in per business | OPEN-02, legal review | Go-live |

---

## 5. Knowledge from the discussion (facts behind decisions)

Corrections to external advice (Gemini) and facts we rely on:

| Topic | Fact | Affects |
|---|---|---|
| Local model choice | `qwen2.5-coder` is a coding model; a customer-facing bot needs a general instruct model | DEC-06 |
| Vertex AI Vector Search | Always-on endpoint → not cheap at small scale | DEC-02 |
| Gemini 1.5 Flash "2M context" | Was 1M (2M was Pro); model generations change — check current model at deploy | DEC-07 |
| Qdrant snapshot migration | Doesn't help when switching embedding models — vectors must be recomputed anyway | DEC-08 |
| Twilio webhook timing | While our webhook runs, the caller hears ringing (Twilio waits up to 15 s) — no dead air | DEC-17 |
| Neon wake time | Doesn't depend on data size (tenant pattern doesn't change it) | DEC-04, DEC-17 |
| RLS pitfall | RLS doesn't apply to the table owner unless `FORCE ROW LEVEL SECURITY`; app must use a non-owner role | DEC-04 |
| Supabase free tier | Projects pause after ~1 week idle | DEC-03 |
| Spot VMs | Can be stopped at any time — unsuitable for a live database | DEC-03 |
| Ollama on Mac in Docker | No GPU access → CPU only | DEC-06 |
| Piper TTS | Maintained fork (`piper1-gpl`) is GPL-3.0; voices have separate licences | DEC-13 |
| Llama licence | Commercial use allowed with conditions ("Built with Llama", acceptable use policy) | OPEN-06 |
| Places API | Scraping Google Maps violates ToS; API data has storage limits | DEC-11 |
| Gemini free tier | Submitted data may be used to improve Google products | DEC-07 |
| Cost at scale | Telephony + speech minutes dominate, not the database | DEC-03, DEC-14 |

Free-tier limits, model names and pricing change — re-check before each decision that
depends on them.
