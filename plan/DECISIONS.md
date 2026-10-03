# Decisions Log

Single place for **what we decided, why, what we rejected, and what is still open**.
Details live in the part plans; this file is the index of reasoning.

Related files:
- [ARCHITECTURE_DRIVERS.md](ARCHITECTURE_DRIVERS.md) — drivers (D1–D7), wants (W1–W9), restrictions (R1–R21), stage focus
- [PROJECT_PLAN.md](PROJECT_PLAN.md) — what we build · [DEVELOPMENT_PLAN.md](DEVELOPMENT_PLAN.md) — how
- Parts: [01 crawler](01-crawler.md) · [02 local RAG](02-local-rag.md) · [03 voice](03-voice-channel.md) · [04 actions](04-actions.md) · [05 cloud (stage 2)](05-cloud-migration.md) · [06 scale (stage 3)](06-scale.md)

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

### Stage focus

The project runs in three learning stages (DEC-27, details in
[DEVELOPMENT_PLAN.md](DEVELOPMENT_PLAN.md)). The priority list above always holds for
correctness & legal; the **focus** below decides trade-offs inside a stage.

| Stage | Goal | Optimize for | Deliberately defer |
|---|---|---|---|
| **1. Local** | Understand the principles (crawl, RAG, voice, tools) | Learning & fast debug loop, $0 | RLS, login, OAuth, onboarding, cloud, CI/CD |
| **2. Cloud** | Understand cloud infrastructure (one pilot business) | Understanding each GCP piece, same tech as local, ~$0 idle | min-instances, load tests, service split, Cloud SQL, Terraform, CI/CD |
| **3. Scale** | Many businesses: safety, reliability, price | Tenant isolation, caller experience, cost per business | — (items introduced by trigger) |

### Build order

| Step | Stage | What | Why this order |
|---|---|---|---|
| 0 | 1 | Foundation: Postgres/pgvector in Docker, Alembic, base tables with `business_id`, `tenant_session` | Every later part writes into these tables |
| 1 | 1 | Crawler → pages, profile, chunks | Need real data before RAG |
| 2 | 1 | RAG: index, retrieval, `/chat`, eval, debug views | Core value; learn how RAG works |
| 3 | 1 | Voice: mic test → Twilio + ngrok | Builds on working RAG |
| 4 | 1 | Actions: tool calling, bookings, summaries, minimal dashboard | Needs RAG + channels |
| 5 | 1 | Gemini comparison (env vars) | Validate quality before cloud |
| 6 | 1 | Containerize (API + crawler images) | Stage 2 readiness |
| 7 | 2 | Cloud Run + Neon + Gemini, one pilot business, `deploy.sh` | Learn infra on a working system |
| 8 | 3 | RLS enforcement → second business | Hard gate for multi-tenancy |
| 9 | 3 | Onboarding, cost per business, reliability, ops, DB growth, cost reduction | By trigger, see [06-scale.md](06-scale.md) |

---

## 2. Decision summary

Status: ✅ Decided · 🔄 Decided, revisit at trigger · ❓ Open (see section 4)

| ID | Topic | Decision | Stage | Status |
|---|---|---|---|---|
| DEC-01 | Tenancy model | One deployment, many businesses (`business_id`) | 1 | ✅ |
| DEC-02 | Vector store | Postgres + pgvector (one DB for everything) | 1 | ✅ |
| DEC-03 | Cloud DB hosting | Neon free tier in stage 2; Neon paid vs Cloud SQL decided in stage 3 | 2 → 3 | 🔄 |
| DEC-04 | Tenant isolation | One shared database: shared tables + `business_id` (stage 1) + Row-Level Security (stage 3, before 2nd business); dedicated database only as a per-region cell (OPEN-16) | 1 (model) · 3 (RLS) | ✅ |
| DEC-05 | LLM interface | `openai` client against OpenAI-compatible endpoints | 1 | ✅ |
| DEC-06 | Local LLM runtime | ~~Ollama~~ — **Superseded by DEC-29** | 1 | — |
| DEC-07 | Cloud LLM | Gemini Flash, paid key, Gemini API first | 1 (compare) · 2 | 🔄 |
| DEC-08 | Embeddings | `nomic-embed-text-v1.5` (LM Studio) local, `gemini-embedding-001` cloud, 768 dims, re-index on switch | 1 · 2 (re-index) | 🔄 |
| DEC-09 | Crawled content storage | Postgres tables, no file storage | 1 | ✅ |
| DEC-10 | Crawler tech | Crawl4AI + httpx fallback, separate crawler image | 1 · 2 (image) | ✅ |
| DEC-11 | Google listing data | Website + owner are the stored source of truth; Places = `place_id` + live lookup | 1 | 🔄 (OPEN-04) |
| DEC-12 | Retrieval | Hybrid: pgvector + Postgres full-text, rank fusion, exact search per business | 1 | ✅ |
| DEC-13 | Speech | Google STT/TTS, also locally; Gemini Live compared in stage 2 (DEC-30) | 1 | ✅ |
| DEC-14 | Telephony | Twilio Media Streams; ngrok locally | 1 | 🔄 |
| DEC-15 | Compute | Cloud Run (API) + Cloud Run Jobs + Scheduler (crawler) | 2 | ✅ |
| DEC-16 | Voice cold start | Stage 2: `min-instances=0`, measure; stage 3: `min-instances=1` for calls | 2 · 3 | ✅ |
| DEC-17 | DB wake-up on call | Lookup in Twilio webhook (caller hears ringing); pre-generated greeting | 2 | 🔄 |
| DEC-18 | Actions | LLM tool calling; confirmation enforced in code; internal bookings first | 1 | ✅ |
| DEC-19 | Dashboard | FastAPI + Jinja + HTMX, no SPA | 1 (minimal) · 3 (login) | ✅ |
| DEC-20 | Frameworks | No LangChain / LlamaIndex | 1 | ✅ |
| DEC-21 | Secrets | `.env` locally, Secret Manager in cloud | 1 · 2 | ✅ |
| DEC-22 | Recordings | Transcripts only; audio recording off by default | 1 · 3 (opt-in) | 🔄 (R16) |
| DEC-23 | Docs layout | Flat `plan/` folder | — | ✅ |
| DEC-24 | Post-call work | Summaries run inside a request (Twilio status callback / end-of-chat), not background tasks | 1 | ✅ |
| DEC-25 | Cloud Run settings for calls | Request-based billing; explicit low `--concurrency` from load test; `--max-instances` cap | 3 | 🔄 (load test) |
| DEC-26 | Number of Cloud Run services | One API service (webhook + voice + chat + dashboard) for now | 2 → 3 | 🔄 |
| DEC-27 | Project structure | Three learning stages: local → cloud (one business) → scale | all | ✅ |
| DEC-28 | Stage-1 runtime | Only Postgres in Docker; app, crawler, LM Studio native; containerize at stage-1 exit | 1 | ✅ |
| DEC-29 | Local LLM runtime | LM Studio on the 64 GB Mac; 20–32B-class instruct models; fast MoE for voice | 1 | ✅ |
| DEC-31 | Stage-1 pilot business | Bathhouse Williamsburg (abathhouse.com) — spa/sauna, appointment+membership-based | 1 | ✅ |
| DEC-30 | Models for voice, aligned with cloud | Two voice modes, shared tools (RAG = `search_business_info` tool); local model pick **superseded by DEC-32**; cloud Gemini Flash (pipeline) vs Gemini Live, decided in stage 2 | 1 · 2 | 🔄 (OPEN-08) |
| DEC-32 | Local models split by role | `google/gemma-4-12b` for chat/voice; `qwen/qwen3.6-35b-a3b` (thinking on) for code-drafting delegation — both fit in memory together | 1 | ✅ |

---

## 3. Decisions with options

### DEC-31 — Stage-1 pilot business (resolves OPEN-01)

| Option | Pros | Cons | Verdict |
|---|---|---|---|
| **Bathhouse Williamsburg** (abathhouse.com, NYC spa/sauna) | Real, well-formed site; `robots.txt` explicitly permits crawling (lists `ClaudeBot`/`anthropic-ai` in the same allowed group as everyone else); `sitemap.xml` present; content is server-rendered (plain `httpx` fetch works, no headless browser needed yet) | Appointment/membership model, not strict 1:1 with either OPEN-01 option; no actual business relationship (see caveat) | ✅ |
| Restaurant with table booking | Matches `04-actions.md`'s first booking backend example | No candidate site picked | ❌ for now |

Reason: unblocks building the crawler now instead of waiting on a real client relationship.

**Caveat (R10, R15):** this is a real third party the project has no relationship with.
Fine as a **stage-1 technical fixture** — local crawling for learning, data stays in the
local Postgres, nothing published, no calls or bookings placed against the real
business. Before any public deployment, Twilio number, or live booking test
(stage 1 steps 3–4 onward) against a real business, get the owner's written consent
(R10) — swap in a consenting pilot business at that point if this one hasn't given it.

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
| Database per business on scale-to-zero Postgres (e.g. Neon project per business) | Idle databases cost ~nothing; strong isolation; easy per-business delete/export | Each low-traffic DB is idle more often → the wake-up (R3) hits many more first calls; migrations × N; a connection pool per business DB in every Cloud Run instance; cost-per-business and admin queries need fan-out; onboarding = creating infrastructure (breaks D6, W7) | ❌ |
| Shared by default + a full **cell** (Cloud Run + DB) per region, only when needed | Meets data residency / contract demands without per-business infra; inside a cell nothing changes | Second deployment to operate; routing phone number/business → cell | 🔄 stage 3, OPEN-16 |

Implementation: see R18 in [ARCHITECTURE_DRIVERS.md](ARCHITECTURE_DRIVERS.md).

Re-checked 2026-10-03 (after the foundation was built): the shared database is confirmed.
At our scale (hundreds–thousands of chunks per business) a shared Postgres has no
performance reason to split. Isolation is enforced in layers: `tenant_session` (stage 1),
composite `(business_id, id)` foreign keys (stage 1, so a chunk or message can't point at
another business's row), and RLS (stage 3). Separate **environments** (dev / staging /
prod, stage 3) are a different topic: they separate code versions, not clients.
The only real reasons for a separate database are region and contracts → handled as a
cell per region, not a database per business (OPEN-16). `tenant_session` is the single
entry point to the database, so routing to another cell can be added there later
without a code rewrite. Nothing is pre-built for it.

Timing (DEC-27): `business_id` on every table and the `tenant_session` helper from
stage 1 (cheap, avoids a painful data migration later); RLS policies + `app_user` role
switched on in stage 3 as a hard gate before the second business — a migration, not a
code rewrite, because all queries already go through `tenant_session`.

### DEC-05 — LLM interface

| Option | Pros | Cons | Verdict |
|---|---|---|---|
| **`openai` client, OpenAI-compatible endpoints** | LM Studio, Ollama and Gemini all support it (chat, embeddings, tools); switch = env vars | Doesn't cover Speech / Gemini Live (R5) | ✅ |
| LangChain / LlamaIndex | Many integrations | Heavy, hides what happens (bad for learning), version churn | ❌ |
| Own provider classes per vendor | Full control | Custom code to maintain | ❌ |
| LiteLLM proxy | Many providers | Extra layer not needed for 2 providers | ❌ |

### DEC-06 — Local LLM runtime — **Superseded by DEC-29**

Original decision (kept for history):

| Option | Pros | Cons | Verdict |
|---|---|---|---|
| **Ollama, native on Mac** | Uses Apple GPU; OpenAI-compatible API | Not in Docker (R7) | ✅ |
| Ollama in Docker on Mac | Everything in Compose | CPU only, very slow | ❌ |
| LM Studio / llama.cpp server | Also OpenAI-compatible | No advantage for us | — (fallback) |

Model: general instruct model with tool calling (e.g. `llama3.1:8b`, Qwen instruct).
**Not** a coding model like `qwen2.5-coder`.

### DEC-29 — Local LLM runtime (supersedes DEC-06)

Context: development Mac has **64 GB unified memory** and **LM Studio** installed.

| Option | Pros | Cons | Verdict |
|---|---|---|---|
| **LM Studio (native)** | Already installed; OpenAI-compatible server (chat, embeddings, tools, JSON-schema output); MLX + GGUF; model browser; `lms` CLI | GUI-first app (CLI available) | ✅ |
| Ollama (native) | Simple CLI | Second runtime to install; no advantage here | ❌ (still compatible — only env vars differ) |
| llama.cpp server / MLX server directly | Maximum control | More setup | ❌ |

Models with 64 GB (compare in OPEN-06):
- **Fast profile (voice):** mixture-of-experts with few active parameters — high tokens/s.
- **Quality profile:** dense 24–32B — reference for answer quality and tool calls.
- 70B at 4-bit fits (~40 GB) but is too slow for voice — optional quality ceiling.
- Not coding models. 4–6-bit quantizations; MLX builds usually fastest.

Effect on other decisions: R1 softened — tools can be developed locally; Gemini is used
for final validation. Runtime choice is invisible to the code (DEC-05): switching LM
Studio ↔ Ollama ↔ Gemini is env vars only.

### DEC-30 — Models for voice, aligned with the cloud target

Context: stage 2 moves to Google Cloud and the product needs **voice in and out**. The
local model should prepare for the cloud setup, not be optimized on its own.

Voice architecture:

| Option | Pros | Cons | Verdict |
|---|---|---|---|
| **Pipeline mode (STT → LLM → TTS) everywhere + Gemini Live spike in stage 2; shared tools** | Learn every stage locally; Live evaluated with real numbers; business logic identical in both modes | Two voice code paths to maintain | ✅ |
| Gemini Live only | Lowest latency, fewest parts | No local development of voice (R21); less visibility for learning | ❌ as only mode |
| Pipeline only | One path | May be slower/more expensive than Live at scale | ❌ without comparison |
| Local omni/native-audio models (e.g. Qwen-Omni) | Local speech-to-speech | Not supported by LM Studio; local-only tech; nothing like it to deploy cheaply | ❌ |
| Self-host Gemma on Cloud Run GPU | Same model local and cloud | GPU cost, cold starts, ops — against D4/D5 | ❌ |

Key design: **RAG is a tool** (`search_business_info`) alongside the action tools, and the
business profile is always in the system instruction → the same definitions serve
pipeline mode (OpenAI-compatible tools) and Live mode (function calling).

Local model (stands in for Gemini Flash in pipeline mode) — kept for history;
**superseded by DEC-32**, which replaces this pick after an eval. The voice
architecture above (pipeline mode, RAG as a shared tool) is unaffected and still stands.

| Model | Why | Verdict |
|---|---|---|
| Qwen3-30B-A3B (or current successor), thinking off | MoE → fast first token; strong native tool calling; multilingual | ❌ superseded by DEC-32 |
| Gemma 3 27B | Gemini's open relative — answer style closest to production | ❌ superseded by DEC-32 |
| gpt-oss-20b, low reasoning effort | Fast, good tools | ❌ superseded by DEC-32 |
| Dense 70B (4-bit) | Quality ceiling | ❌ too slow for voice |

Cloud model: Gemini Flash with thinking off for pipeline mode; Gemini Live native-audio
model for Live mode (current model names at deploy time). Final choice of mode: OPEN-08.

### DEC-32 — Local models split by role (supersedes DEC-30's local-model pick, resolves OPEN-06)

Context: DEC-30 picked Qwen3-30B-A3B as the primary local chat/voice model, pending an
eval (OPEN-06). Separately, `qwen/qwen3.6-35b-a3b` (thinking on) was already chosen for
whole-file code-drafting delegation, validated head-to-head against `qwen3-coder-next`
(CLAUDE.md). A later >20B-model coding benchmark (known traps: lstrip bug, header
attribution, `select().delete()`, missing `from_attributes`, async handlers) confirmed
qwen3.6-35b-a3b remains the strongest local coder but still fails real traps that
Claude (Sonnet 5 / Opus 5.5) passes cleanly — local models stay the coding delegate for
cost/learning reasons (DEC-29), not because they match Claude's correctness.

That left chat/voice's model choice still open. `google/gemma-4-12b` was evaluated
against the stage-1 pilot business (`tests/eval/bathhouse.yaml`, 7 questions):

| Option | Pros | Cons | Verdict |
|---|---|---|---|
| **gemma-4-12b** | 9.76 GB resident — fits in memory alongside qwen3.6-35b-a3b (29.09 GB) + the embedding model (0.08 GB) ≈ 38.9 GB total, no unload/reload switching roles; 7/7 on the bathhouse eval, correctly refuses out-of-scope questions | Per-turn latency ~10–17s warm, not yet voice-ready; weaker tool-calling than Qwen3 per DEC-30's original note (untested here — stage 1 doesn't exercise tool calls yet) | ✅ chat/voice |
| qwen3.6-35b-a3b for both roles | One model, nothing to switch | Also 7/7 on the same eval but no faster (9.7–42s/question, noisy); would force unload/reload between an app request and a code-drafting delegation | ❌ |
| Qwen3-30B-A3B (DEC-30 original primary) | Native tool calling, MoE speed | Never head-to-head eval'd for chat specifically; superseded in `.env` already by the 3.6-35b-a3b family | ❌ superseded |
| Gemma 3 27B (DEC-30 comparison) | Closest to Gemini's style | Larger, no eval run; gemma-4-12b is the newer/smaller model in the same family | ❌ superseded |

Decision: **`google/gemma-4-12b` for chat/voice** (`.env`'s `LLM_MODEL`); **`qwen/qwen3.6-35b-a3b` (thinking on) stays the code-drafting delegate** (`.claude/tools/delegate_code.py`'s own default, independent of `.env`). Both loaded together on the 64 GB Mac, ~38.9 GB combined — well under the GPU memory budget noted in DEC-29.

Not solved by this decision: per-turn latency for *both* models is 10–20s, far from
natural phone-call pacing (~2–3s). That's a separate problem for the voice step
(`04-actions.md`) — prompt size, context length, and streaming TTS overlap — not fixed
by picking a smaller chat model.

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
| **Local `nomic-embed-text-v1.5` (LM Studio), cloud `gemini-embedding-001`, both 768 dims** | Fully local learning; same column size | Re-index on migration (R6) | ✅ current |
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
| **Website + owner-confirmed profile as stored facts; Places = `place_id` + live lookup** | Compliant; facts owned by the business; no per-call Places cost | Owner must confirm/edit profile at onboarding | ✅ |
| Store Places data with a < 30-day `expires_at` cache (Gemini suggestion) | Simple | The 30-day allowance is for lat/lng only, not general content — likely non-compliant | ❌ |
| Fetch Places live on every call | Always fresh | Latency + cost in the call path | ❌ (only for rare questions) |
| Scraping Google Maps | "Free" | Violates ToS; breaks often | ❌ |

Still open (OPEN-04): verify current terms, and whether Places data may pre-fill the
owner's profile form at onboarding.

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
| Gemini Live API (audio in/out) | Fewer stages, lower latency | Cloud only (R21); less control per stage | 🔄 compared in stage 2 (DEC-30, OPEN-08) |

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

Stage 2: `min-instances=0` — measure cold starts on real calls (learning goal).
Stage 3: `min-instances=1` for the service handling calls (few $/month). Reason: R2 —
caller experience ranks above cost once real customers depend on it.

### DEC-17 — Database wake-up on call

| Option | Verdict |
|---|---|
| **Business lookup inside the Twilio webhook (caller hears ringing), pre-generated greeting audio per business** | ✅ |
| Generic "please wait while I load the assistant" message (Gemini suggestion) | ❌ worsens every call to hide a delay the caller doesn't hear |
| Always-on Cloud SQL | 🔄 if measured latency is still a problem |

### DEC-18 — Actions

- LLM **tool calling** (same `tools=[...]` for LM Studio and Gemini).
- Confirmation before any booking is enforced **in code**, not only in the prompt.
- Caller phone from caller ID; availability re-checked in a DB transaction (R17).
- Booking backends in order: internal Postgres table → Google Calendar → Cal.com → OpenTable etc.
- Tools developed with 20–32B local models (DEC-29); final booking tests against Gemini (R1).

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
`CLAUDE.md` at the repo root explains the files and how to record discussions.

### DEC-24 — Post-call work (summaries)

| Option | Pros | Cons | Verdict |
|---|---|---|---|
| **Inside a request: Twilio status callback (calls) / end-of-chat request (chat)** | CPU allocated while it runs; no extra service | Callback waits for the LLM (fine — Twilio doesn't need a fast reply here) | ✅ |
| Fire-and-forget background task after the call | Simple code | Cloud Run throttles CPU after the response → work stalls or is lost | ❌ |
| Cloud Tasks queue → internal endpoint | Retries, decoupled | Extra service | 🔄 if summaries need retries |

Reason: R19.

### DEC-25 — Cloud Run settings for calls

| Option | Pros | Cons | Verdict |
|---|---|---|---|
| **Request-based billing, explicit `--concurrency` (start ≈10–20 per vCPU, tune by load test), `--max-instances` cap, timeout 3600 s** | Cheapest; CPU is allocated while the call's WebSocket is open | Needs a load test | ✅ |
| "CPU always allocated" (instance-based billing) to avoid stutter (Gemini suggestion) | Needed only for background work | Doesn't affect streaming — CPU isn't throttled during an open request; costs more | ❌ |
| Default concurrency 80 | No config | Too many audio streams per instance → latency spikes | ❌ |

Reason: R2, R19, R20.

### DEC-26 — Number of Cloud Run services

| Option | Pros | Cons | Verdict |
|---|---|---|---|
| **One API service** (Twilio webhook, voice WebSocket, chat, dashboard) | Simplest; one deploy | `min-instances=1` keeps the whole app warm; dashboard traffic shares instances with calls | ✅ now |
| Split: voice service (webhook + WebSocket) vs web service (chat, dashboard) | Voice tuned separately (concurrency, min-instances); dashboard scales to zero | Two deploys from the same image | 🔄 when call volume or dashboard load justifies it |
| Separate webhook and voice services (Gemini diagram) | — | Extra hop in the call path; no benefit | ❌ |

The split needs no code changes — same image, different entry routes/settings.



### DEC-27 — Three learning stages

| Option | Pros | Cons | Verdict |
|---|---|---|---|
| **Stage 1 local → stage 2 cloud (one business) → stage 3 scale** | Each stage has one learning goal; hard problems solved when real; nothing built "just in case" | Some stage-3 work (RLS, onboarding) comes later | ✅ |
| Build production-ready multi-tenant cloud system from the start | No later changes | Learning buried under infra; solving scale problems before they exist | ❌ |
| Local only until everything is "finished" | Cheapest | Cloud surprises (cold starts, WebSockets, IAM) found too late | ❌ |

What stays constant across stages so transitions are cheap: Postgres + pgvector,
OpenAI-compatible LLM API, env-var configuration, `business_id` data model.

### DEC-28 — Stage-1 runtime

| Option | Pros | Cons | Verdict |
|---|---|---|---|
| **Only Postgres in Docker; app, crawler, LM Studio native (`uv run`)** | Fastest edit/debug loop; breakpoints; LM Studio uses the Mac GPU | Not the final runtime | ✅ |
| Everything in Docker Compose from day one | Same as cloud | Slower debug loop; rebuilds; no GPU for a local LLM in Docker (R7) | ❌ for learning |

At stage-1 exit the app and crawler are containerized and run via Compose with the same
env vars — that is the proof of stage-2 readiness (D2 applies from then on).
---

## 4. Still considering (open questions)

Grouped by the stage in which the answer is needed.

| ID | Stage | Question | Options | Depends on / trigger |
|---|---|---|---|---|
| OPEN-03 | 1 | Languages for voice | English only · + local language(s) | OPEN-02 |
| OPEN-04 | 1 | Places API terms details | Confirm: only `place_id` stored; may Places pre-fill the owner's profile form? | Read current Places terms (R9) |
| OPEN-05 | 1 | Embeddings locally | Local `nomic` + re-index · Gemini embeddings everywhere | Offline learning vs index parity |
| OPEN-02 | 2 | Target country / region | EU (`europe-west1`) · US (`us-central1`) · other | Where the pilot business is (R16) |
| OPEN-07 | 2 | Gemini API vs Vertex AI | API key (simple) · Vertex (IAM, region) | OPEN-02, data residency needs |
| OPEN-11 | 2 | Code licence | Private, no licence · MIT · Apache 2.0 · AGPL | Before making the repo public |
| OPEN-08 | 2 | Voice mode in cloud | Pipeline (Google STT → Gemini Flash → Google TTS) · Gemini Live | Stage-2 comparison: latency, quality, cost per minute, session limits |
| OPEN-09 | 3 | Telephony provider | Twilio · Telnyx · Vonage · Plivo | OPEN-02 (price, number availability) |
| OPEN-10 | 3 | Database after Neon free tier | Neon paid · Cloud SQL | Free-tier limits, measured wake-up latency, GCP-native needs |
| OPEN-12 | 3 | Owner login for dashboard | Google sign-in · magic link | Before the second business |
| OPEN-13 | 3 | CI/CD + Terraform timing | When deploys get frequent/risky or >1 person deploys | Stage 2 `deploy.sh` experience |
| OPEN-14 | 3 | Call recordings | Never · opt-in per business | OPEN-02, legal review |
| OPEN-15 | 3 | Voice concurrency per instance | 10 · 20 · 40 … | Load test with real call audio (DEC-25) |
| OPEN-16 | 3 | Dedicated database for some businesses? | No — everything in one shared DB · a full cell (Cloud Run + DB) per region · dedicated DB for a business that requires it by contract | Clients in a second region (OPEN-02, R16); a future clinic/health client with stricter data rules; a business whose traffic slows others |
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
| Cloud Run CPU | With request-based billing CPU is throttled when no request is active; an open WebSocket counts as active — so "CPU always allocated" doesn't fix streaming | DEC-16, DEC-24, DEC-25 |
| Cloud Run concurrency | Default 80 requests per instance (max 1000, not 250); voice needs a lower, load-tested value | DEC-25 |
| Neon pooler | Separate `-pooler` hostname, PgBouncer transaction mode; `SET LOCAL` works, session `SET` doesn't; migrations use the direct connection | R20 |
| One database per business | A Postgres database lives in one region; data residency forces a split **per region**, not per business. Scale-to-zero makes idle per-business DBs cheap but adds a wake-up (R3) to more first calls | DEC-04, OPEN-16 |
| Health data | Clinic appointments can reveal health information → special category under GDPR Art. 9 (HIPAA in the US) → stricter contracts may demand a dedicated database or region | OPEN-16 |
| Places "30-day cache" | Google's 30-day allowance applies to lat/lng, not to Places content in general; only `place_id` may be stored indefinitely | DEC-11 |
| Async DB lookup in the webhook (Gemini) | Unneeded — the lookup runs while the caller hears ringing (DEC-17) | DEC-17 |
| Neon wake time | Doesn't depend on data size (tenant pattern doesn't change it) | DEC-04, DEC-17 |
| RLS pitfall | RLS doesn't apply to the table owner unless `FORCE ROW LEVEL SECURITY`; app must use a non-owner role | DEC-04 |
| Supabase free tier | Projects pause after ~1 week idle | DEC-03 |
| Spot VMs | Can be stopped at any time — unsuitable for a live database | DEC-03 |
| Local LLM in Docker on Mac | No GPU access → CPU only | DEC-28, DEC-29 |
| 64 GB Mac memory | GPU can use ~70–75% of unified memory by default (~45–48 GB) for weights + KV cache | DEC-29 |
| MoE vs dense locally | MoE models with few active parameters give much higher tokens/s at similar size — key for voice latency | DEC-29 |
| LM Studio and audio | Serves text/vision models; no speech in/out | DEC-30, R21 |
| Gemma 3 tool calling | No dedicated tool-call format — prompt-based, less reliable than Qwen3/gpt-oss | DEC-30 |
| Thinking modes | Reasoning/thinking adds seconds of latency — off for chat/voice (Qwen3, Gemini Flash thinking budget 0, gpt-oss low effort) | DEC-30 |
| Gemini Live audio | 16 kHz PCM in, 24 kHz PCM out; Twilio is 8 kHz μ-law → resample; sessions have duration limits (check current) | DEC-30 |
| `nomic-embed-text` prefixes | Needs `search_document: ` / `search_query: ` prefixes for best retrieval | DEC-08 |
| Piper TTS | Maintained fork (`piper1-gpl`) is GPL-3.0; voices have separate licences | DEC-13 |
| Llama licence | Commercial use allowed with conditions ("Built with Llama", acceptable use policy) | OPEN-06 |
| Places API | Scraping Google Maps violates ToS; API data has storage limits | DEC-11 |
| Gemini free tier | Submitted data may be used to improve Google products | DEC-07 |
| Cost at scale | Telephony + speech minutes dominate, not the database | DEC-03, DEC-14 |

Free-tier limits, model names and pricing change — re-check before each decision that
depends on them.
