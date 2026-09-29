# Architecture Drivers, Wants and Restrictions

How to read this file:
1. **Drivers** — the principles every technical decision is checked against, in priority order.
2. **Wants** — what we want the system to achieve.
3. **Restrictions** — hard limits (physics, law, vendor terms, cost, quality) that we
   cannot ignore. Each one forces a technology or design that breaks a driver. The
   break is accepted **on purpose** and recorded here, so it isn't "fixed" back later.

When a new decision contradicts a driver, add a restriction entry instead of silently
bending the rule.

---

## 1. Architecture drivers

| ID | Driver | Meaning |
|---|---|---|
| D1 | **Local first** | Everything can run and be tested by hand on the laptop, at ~zero cost, before cloud. |
| D2 | **Same tech local and cloud** | Only technology that exists both locally and as a managed Google Cloud (or equivalent managed) service. Migration = env vars. |
| D3 | **Standard interfaces** | Postgres/SQL for data, OpenAI-compatible API for LLM calls, Docker for runtime. No custom abstraction layers, no heavy frameworks. |
| D4 | **Managed and simple** | No VMs, no Kubernetes, no servers we patch. As few services as possible. |
| D5 | **Scale to zero / pay per use** | Near $0 when idle; cost grows with usage, not with number of businesses. |
| D6 | **One deployment, many businesses** | A business = rows with `business_id` + a phone number, never new infrastructure. |
| D7 | **One database** | Vectors, full-text, business data, bookings, conversations in one Postgres. |

Priority when drivers conflict: **correctness & legal > caller experience > D6 > D2 > D4 > D5 > D1**.

### Driver emphasis per stage (DEC-27)

| Driver | Stage 1 — Local | Stage 2 — Cloud | Stage 3 — Scale |
|---|---|---|---|
| D1 Local first | **primary** | — | — |
| D2 Same tech local/cloud | prepared (standard interfaces), images at exit | **primary** | holds |
| D3 Standard interfaces | **primary** | holds | holds |
| D4 Managed & simple | simplest dev loop (app native, DEC-28) | **primary** (one service, manual deploy) | bent where scale needs it (R18, R20) |
| D5 Scale to zero | $0 | **~$0 idle**, measure cold starts | bent for caller experience (R2) |
| D6 One deployment, many businesses | data model only (`business_id`) | data model only | **primary**, enforced (RLS) |
| D7 One database | holds | holds | holds |

## 2. Wants

| ID | Want |
|---|---|
| W1 | Build a business's knowledge base automatically from its website and Google listing. |
| W2 | Answer only from the business's data; say "I don't know" instead of guessing. |
| W3 | Text chat and phone calls with the same brain. |
| W4 | Natural voice conversation: reply within ~1.5 s, caller can interrupt. |
| W5 | Actions: book tables, make appointments, take messages, transfer to a human. |
| W6 | Summary of every conversation + owner dashboard. |
| W7 | Onboard a new business without code changes. |
| W8 | Understand every step (inspect chunks, vectors, prompts) — learning project first. |
| W9 | Base infrastructure cost ≈ $0–20/month. |

---

## 3. Restrictions that force deviations

Status: **Accepted** = deviation is in the plan · **Watch** = acceptable now, revisit ·
**Open** = decision still needed.

### Quality and performance

**R1 — Local LLMs are weaker than Gemini at answers and tool calling**
- Hardware: 64 GB Mac with LM Studio → 20–32B-class models run well; they are much
  better than 8B models, so the whole flow incl. tool calling can be developed locally.
- Why still unavoidable: the local model is not the production model — quality, tool-call
  format and speed differ. Booking mistakes are real-world errors.
- Breaks: D1 (fully local) — only for final validation.
- Forced choice: develop everything (incl. tools) locally; run the eval and booking tests
  against Gemini before stage 2 (stage-1 step 6) and after prompt changes.
- Status: **Accepted**.

**R2 — Voice latency budget (~1.5 s end-to-end)**
- Why unavoidable: longer pauses feel broken on a phone call; callers hang up.
- Breaks: D5 (scale to zero) — a Cloud Run cold start (seconds) during a call is unacceptable.
- Forced choice: `min-instances=1` for the service handling calls (a few $/month);
  streaming everywhere; possibly Gemini Live to cut stages.
- Status: **Accepted** for stage 3; in stage 2 cold starts are tolerated and measured.

**R3 — Serverless database wake-up time**
- Why unavoidable: Neon (scale-to-zero Postgres) needs ~0.5 s to wake after idle; the first
  query of a call would eat a third of the latency budget.
- Breaks: D5 if solved by always-on DB.
- Key fact: while our Twilio webhook is running, the caller still hears **ringing**, not
  silence (Twilio waits up to 15 s for the TwiML). A 0.5 s wake-up = a slightly longer
  ring, not dead air. Dead air is only possible after the audio stream is connected.
- Forced choice: the `/twilio/voice` webhook does the phone-number → business lookup
  itself; that query wakes Neon, so the DB is warm for the rest of the call. Greeting
  audio per business is pre-generated (TTS at onboarding) so the first words play
  instantly. No generic "please wait while I load the assistant" message.
  Move to always-on Cloud SQL (~$10/month) if measured latency is still a problem.
- Note: Cloud Run's own cold start (seconds) is the bigger risk — see R2.
- Status: **Watch** — measure on real calls.

**R4 — Phone audio is 8 kHz μ-law**
- Why unavoidable: that's what the telephone network and Twilio deliver.
- Breaks: D1 — local speech models (Whisper, Piper) degrade on phone audio and are
  local-only tech (breaks D2); Piper's maintained fork is GPL.
- Forced choice: Google Speech-to-Text / Text-to-Speech, **also during local
  development** (cloud dependency + small cost while testing).
- Status: **Accepted**.

### Interfaces and portability

**R5 — OpenAI-compatible API doesn't cover everything**
- Why unavoidable: Google Speech and Gemini Live (streaming audio) only have native SDKs.
- Breaks: D3 (one standard interface).
- Forced choice: native Google SDKs in `app/voice/` only; everything else stays on the
  OpenAI-compatible client.
- Status: **Accepted**.

**R6 — Embedding models are not interchangeable**
- Why unavoidable: vectors from `nomic-embed-text` and Gemini embeddings live in different
  spaces; mixing them returns garbage.
- Breaks: D2 ("migration = env vars only").
- Forced choice: one re-index step on migration (chunk text is in Postgres; indexer
  re-embeds rows where `embed_model` differs). Alternative: use Gemini embeddings locally too.
- Status: **Accepted**.

**R7 — Local LLM in Docker on macOS has no GPU access**
- Why unavoidable: Docker Desktop on Mac can't pass through the Apple GPU → CPU-only, very slow.
- Breaks: "everything in Docker" (D3/D1).
- Forced choice: LM Studio runs natively on the Mac (DEC-29); the container reaches it via
  `host.docker.internal:1234`.
- Status: **Accepted** (local only; irrelevant in cloud).

**R8 — Crawler needs a headless browser**
- Why unavoidable: many small-business sites are JavaScript-rendered; plain HTTP returns
  empty pages.
- Breaks: "same image for everything" / small fast image (D4, R2 cold starts).
- Forced choice: separate Docker build target (or image) for the crawler job with
  Crawl4AI/Playwright; the API image stays small.
- Status: **Accepted**.

### Vendor terms and external services

**R9 — Google Places API storage terms**
- Why unavoidable: Places terms forbid storing most Places content permanently (only
  `place_id` may be kept indefinitely; other data has caching limits).
- Breaks: W1 / "all knowledge in RAG".
- Forced choice (DEC-11): **sources of truth for stored facts are the business website
  and the owner** (confirmed in the dashboard). From Places we store only `place_id`;
  Places fields are used as a live lookup, not stored. Reviews are not stored.
  Note: the "30 days" caching allowance in Google's terms is for latitude/longitude —
  it is not a general permission to cache any Places content for 30 days.
- Status: **Accepted** for the approach; exact terms still to verify (OPEN-04), incl.
  whether Places may pre-fill the owner's profile form at onboarding.

**R10 — Website terms, robots.txt, bot protection**
- Why unavoidable: legal/ethical limits; Cloudflare and similar block crawlers.
- Breaks: W1 (fully automatic knowledge base).
- Forced choice: respect robots.txt; get the business owner's written consent (they are
  the customer); manual upload path in the dashboard as fallback.
- Status: **Accepted**.

**R11 — Telephony needs public HTTPS/WSS endpoints and a real provider**
- Why unavoidable: Twilio must reach our server from the internet; phone numbers are
  a paid, regulated resource (some countries require business registration/ID).
- Breaks: D1 (local, zero cost).
- Forced choice: ngrok tunnel locally; paid Twilio number and per-minute costs during
  testing; number availability may dictate provider per country.
- Status: **Accepted**.

**R12 — Gemini free tier may use submitted data**
- Why unavoidable: customer conversations must not be used for model training.
- Breaks: D1/D5 (zero cost).
- Forced choice: paid Gemini API key (or Vertex AI) for anything with real customer data.
- Status: **Accepted**.

**R13 — Free database tiers have pause rules and limits**
- Why unavoidable: Supabase free projects pause after ~1 week idle; free tiers cap storage.
- Breaks: D5 when we upgrade.
- Forced choice: Neon free tier for prototype; Cloud SQL or paid tier once a business is
  live. Database outside GCP means a second vendor/bill until then (breaks D4 slightly).
- Status: **Watch**.

**R14 — Calendar/booking integrations need per-business OAuth**
- Why unavoidable: Google Calendar / Cal.com access is granted per business account.
- Breaks: D4 (simple) — adds token storage, refresh and encryption.
- Forced choice: store encrypted refresh tokens in Postgres (key in Secret Manager);
  internal booking table first, integrations later.
- Status: **Accepted** — stage 3.

### Legal and data protection

**R15 — AI disclosure and call-recording consent**
- Why unavoidable: required by law in many jurisdictions (one- vs two-party consent,
  EU AI Act transparency).
- Breaks: W4 slightly (longer greeting).
- Forced choice: fixed disclosure in every greeting; recording off by default,
  per-business setting.
- Status: **Accepted**.

**R16 — GDPR / personal data (transcripts, phone numbers)**
- Why unavoidable: callers' data is personal data.
- Breaks: D5/D7 partially — may require an EU region, retention jobs, data deletion on
  request; recordings (audio) would require object storage (Cloud Storage), i.e. a
  second storage service.
- Forced choice: pick region by target market (e.g. `europe-west1`) for Cloud Run,
  database and Gemini/Vertex; retention policy + scheduled cleanup; transcripts only
  (no audio) until needed.
- Status: **Open** — depends on target country.

### Correctness and multi-tenancy

**R17 — No double bookings**
- Why unavoidable: a double-booked table is a real-world failure.
- Breaks: nothing in the current plan — but rules out non-transactional stores
  (Firestore + separate vector DB was rejected for this reason).
- Forced choice: Postgres transaction re-checks availability inside `create_booking`.
- Status: **Accepted**.

**R18 — Tenant isolation**
- Why unavoidable: one shared database (D6/D7) means a missing `business_id` filter
  leaks another business's data to a caller.
- Breaks: simplicity (D4) — needs an enforcement layer.
- Decision: **shared tables with a `business_id` column + Postgres Row-Level Security**.
  Rejected: schema per business — every migration runs N times, catalog bloat with
  hundreds of schemas, and cross-business admin queries get harder.
- How RLS is applied:
  - App connects as a non-owner role (`app_user`); tables use `ENABLE` + `FORCE ROW LEVEL SECURITY`.
  - Policy: `business_id = current_setting('app.business_id')::uuid`.
  - Every request/call runs in a transaction that starts with
    `SET LOCAL app.business_id = '<id>'` (`SET LOCAL` is safe with transaction-mode poolers).
  - Admin/migrations/crawler-orchestration use a separate role that bypasses RLS.
  - Tests assert that business A can never read business B's rows.
- Breaks: D4 slightly (roles + policies), but removes a whole class of data-leak bugs.
- Status: **Accepted** — `business_id` + `tenant_session` from stage 1; RLS enforced in
  stage 3 before the second business.

**R19 — Cloud Run limits for calls**
- Why unavoidable: max request timeout 60 min; each call holds a WebSocket; with
  request-based billing, CPU is throttled as soon as no request is active.
- Breaks: D5 (instance busy for the whole call) and "fire-and-forget" background work.
- Forced choice: timeout 3600 s; an open WebSocket is an active request, so CPU stays
  allocated during the call without "CPU always allocated"; post-call work (summaries)
  runs inside its own request — the Twilio status callback (DEC-24); calls > 60 min are
  cut (acceptable).
- Concurrency (DEC-25): set `--concurrency` explicitly for the call-handling service,
  from a load test (per-call CPU: audio conversion, VAD, streaming). Start low (≈10–20
  calls per 1 vCPU instance), not Cloud Run's default of 80. Cap `--max-instances` (R20).
- Rejected: "CPU always allocated" to avoid stutter — CPU is not throttled while a
  WebSocket request is open, so it doesn't help streaming and costs more.
- Status: **Accepted**.

**R20 — Serverless connections to Postgres**
- Why unavoidable: many Cloud Run instances × connection pools can exhaust a small
  database's connection limit.
- Breaks: D4 slightly.
- Forced choice: small pool per instance; cap Cloud Run `--max-instances`;
  - **Neon:** app uses the **pooled** connection string (`-pooler` host, PgBouncer
    transaction mode); migrations (`ADMIN_DATABASE_URL`) use the **direct** connection.
  - Transaction mode rules: only `SET LOCAL` inside a transaction (never session `SET`),
    no session state (advisory locks, `LISTEN`, temp tables) across transactions;
    check prepared-statement support of the driver/pooler.
  - **Cloud SQL (later):** connector + small pools, or a pooler if instances grow.
- Status: **Accepted** (details verified when implementing the foundation).

---

## 4. Summary: where we deliberately bend the drivers

| Driver | Bent by | What we do instead |
|---|---|---|
| D1 Local first | R1, R4, R11, R12 | Gemini, Google Speech and Twilio are used during local development (small cost) |
| D2 Same tech local/cloud | R6, R7 | One re-index on migration; LM Studio native on Mac |
| D3 Standard interfaces | R5 | Native Google SDKs in the voice module only |
| D4 Managed & simple | R8, R14, R18, R20 | Separate crawler image, OAuth token storage, tenant guard, connection pooling |
| D5 Scale to zero | R2, R3, R13, R19 | Warm voice instance, possibly always-on DB once live |
| D7 One database | R16 (if audio recordings) | Cloud Storage only when recordings are required |

## 5. Open decisions

Tracked in [DECISIONS.md](DECISIONS.md) → "Still considering" (R9, R16, R3/R13 and others).
