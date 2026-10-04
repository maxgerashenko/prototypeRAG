# Development Plan — Three Learning Stages

The project is built in three stages. Each stage has its own **learning goal**, and we
choose the **simplest solution that reaches that goal** — not the solution needed for
the final product. Harder problems are deliberately deferred to the stage where they
become real.

| Stage | Goal | Question it answers | Businesses | Infra cost |
|---|---|---|---|---|
| **1. Local** | Understand the principles | How do crawling, RAG, voice and tool calling actually work? | 1 (test) | ~$0 (+ cents for Google Speech / Gemini tests) |
| **2. Cloud** | Understand cloud infrastructure | How does it run on Google Cloud — images, services, jobs, secrets, IAM, logs? | 1 (pilot) | $0 fixed + usage only (DEC-33) |
| **3. Scale** | Solve multi-client scale and price problems | How do we serve many businesses safely, reliably and profitably? | many | usage only + each business's number; known cost per business |

Related: principles and restrictions → [ARCHITECTURE_DRIVERS.md](ARCHITECTURE_DRIVERS.md) ·
decisions and open questions → [DECISIONS.md](DECISIONS.md) (DEC-27, DEC-28).

## Principles across all stages

1. **Standard interfaces from day one** — Postgres + pgvector for all data, the
   OpenAI-compatible API for all LLM calls, env vars for configuration. This is what
   makes each stage transition cheap.
2. **Multi-tenant data model from day one, enforcement later** — every table has
   `business_id` and every query goes through `tenant_session(business_id)` from stage 1;
   Row-Level Security policies are switched on in stage 3 (a migration, no code rewrite).
3. **Only technology that exists locally and as a managed service** — nothing to throw
   away between stages.
4. **Defer, don't pre-build** — each stage lists what is deliberately *not* done yet.
5. **One deployment for all businesses** (D6) — even in stage 3 a new business is rows +
   a phone number, not new infrastructure.

## Stack by stage

| Layer | Stage 1 — Local | Stage 2 — Cloud | Stage 3 — Scale |
|---|---|---|---|
| App runtime | FastAPI **natively** (`uv run`, hot reload, debugger) → Docker image at stage exit | **Cloud Run**, one service, `min-instances=0` | `min-instances=0` (cold start during ringing, DEC-35), tuned concurrency, split voice vs web if needed |
| Crawler | Python CLI natively | Separate crawler image → **Cloud Run Job** + Scheduler | same, per-business schedules |
| Database | Postgres 16 + pgvector in **Docker** (the only container) | **Neon** free tier (AWS region in the same city — Neon has no GCP regions) | **Neon Launch** (usage-based, DEC-33), pooling, backups, HNSW if needed |
| Tenant isolation | `business_id` + `tenant_session` helper | same | **RLS policies + `app_user` role + isolation tests** |
| LLM | **LM Studio** native, split by role (DEC-32): `gemma-4-12b` for chat/voice, `qwen3.6-35b-a3b` for code delegation | **Gemini Flash** paid key (OpenAI-compatible), thinking off for voice | + cost controls |
| Embeddings | `nomic-embed-text-v1.5` in LM Studio (or Gemini, OPEN-05) | Gemini embeddings, one re-index | same |
| Voice | **Pipeline mode**: Google STT → local LLM → Google TTS | Pipeline mode on Cloud Run, then **Gemini Live spike** → decide (OPEN-08, DEC-30) | chosen mode, tuned for cost |
| Telephony | Browser mic first, then Twilio **Voice SDK browser calls** (no number, DEC-34) + **ngrok** | Voice SDK → Cloud Run URL; real number once the pilot pays for it | provider per country/price (OPEN-09) |
| Secrets | `.env` | **Secret Manager** | same + rotation |
| Deploy | — | Manual `gcloud` → `deploy.sh` | **CI/CD** (GitHub Actions + WIF), **Terraform**, staging/prod |
| Observability | logs in terminal, debug endpoints | Cloud Logging, budget alert, latency numbers | dashboards, alerts, **cost per business** |

---

## Stage 1 — Local: understand the principles

**Learning goals**
- Crawling and cleaning a real site; what good chunks look like.
- Embeddings and vector search — inspect vectors and similarity scores in `psql`.
- Hybrid retrieval (vector + full-text) and why it beats vector-only.
- Prompt building, grounding, "I don't know" behaviour, hallucinations.
- Tool calling: how the model decides to book; why confirmation must be in code.
- Voice pipeline: STT → LLM → TTS, where latency comes from, barge-in.

**Optimal setup for learning** (DEC-28)
- Only Postgres + pgvector runs in Docker; the app, crawler and LM Studio run natively —
  fastest edit/debug loop, breakpoints, print statements.
- One test business, but with the full `business_id` data model.
- Debug views: retrieved chunks with scores, the full prompt, LLM call logs.
- Voice in two steps: browser mic (no call costs) → Twilio Voice SDK browser call via ngrok
  (per minute, no phone number — DEC-34).
- Gemini used only for comparison (quality of answers and tool calls).

**Steps** — details in the part files
1. ✅ Foundation: `docker-compose.yml` with Postgres/pgvector, Alembic migrations
   (`vector`, base tables with `business_id`), FastAPI skeleton, `tenant_session` helper.
2. ✅ Crawler → one real business in Postgres — [01-crawler.md](01-crawler.md).
3. ✅ Index, retrieval, `/chat`, eval questions, debug views — [02-local-rag.md](02-local-rag.md).
4. Voice: mic test mode, then Twilio Voice SDK browser calls + ngrok — [03-voice-channel.md](03-voice-channel.md).
5. Actions: tool calling, internal bookings, summaries, minimal dashboard (no login) —
   [04-actions.md](04-actions.md).
6. Gemini comparison: switch `LLM_*` / `EMBED_*` env vars, re-run eval + booking tests.
7. Containerize: API `Dockerfile` (+ `Dockerfile.crawler` only if Crawl4AI was added,
   DEC-36); run everything via Compose with the same env vars — proves stage 2 readiness.

**Stage 1 tuning — after step 7, before stage 2.** Fixes found while building, done as
one block so stage 2 starts from a tuned pipeline:
- Voice T1–T4b ([03-voice-channel.md](03-voice-channel.md) → "Stage 1 tuning"): "working"
  sound during searches, shorter tool results, LLM warm-up during the greeting,
  pre-generated greeting, sentence splitter abbreviations.
- Crawler T3 ([01-crawler.md](01-crawler.md)): business name and timezone in the profile.
- Voice bugs B1–B6 from the code review of the call loop ([03-voice-channel.md](03-voice-channel.md)
  → "Stage 1 tuning"): hang-up cleanup, heard vs sent text, barge-in during STT, chunk
  clipping, repeated tool-round text, timezone error.
- Open items in the "Known problems" list of [03-voice-channel.md](03-voice-channel.md)
  that are testable locally.

**Deliberately not in stage 1:** RLS policies, owner login, calendar OAuth, onboarding
flow, Places beyond `place_id`, CI/CD, any cloud hosting.

**Exit criteria**
- Eval questions pass; retrieval and prompts understood and inspectable.
- A Twilio call (Voice SDK, through ngrok) answers questions and books an appointment
  (the pilot is a spa, DEC-31; consent question OPEN-18).
- App and crawler run from Docker images with env-var config only.
- Stage 1 tuning block done; voice latency re-measured and recorded.

---

## Stage 2 — Cloud: understand the infrastructure

Details: [05-cloud-migration.md](05-cloud-migration.md)

**Learning goals**
- GCP project, billing, budget alerts, IAM and service accounts.
- Container images in Artifact Registry.
- Cloud Run: revisions, env vars, secrets, timeouts, cold starts, WebSockets, logs.
- Cloud Run Jobs + Cloud Scheduler for batch work (crawler).
- Secret Manager; connecting to a managed Postgres; Gemini in production.

**Optimal setup for learning**
- Deploy **by hand with `gcloud`** first so every piece is understood; capture the
  commands in `deploy.sh` afterwards. No Terraform/CI yet.
- **Neon free tier** — same Postgres, $0, nothing new to learn about the DB itself.
- One Cloud Run service, `min-instances=0`, default concurrency — **measure** cold start
  and call latency instead of optimizing blindly.
- One pilot business, paid Gemini key (real customer data, R12); a real phone number
  only once the pilot pays for it — until then Voice SDK calls (DEC-34).

**Must be in stage 2 before going public:** access control on owner/debug routes and
Twilio signature checks (OPEN-19).

**Deliberately not in stage 2:** load tests, service split,
Terraform, CI/CD, staging/prod, RLS enforcement (only one business).

**Exit criteria**
- Pilot business answers calls from Cloud Run (Voice SDK, or its own number once paid for); crawler job runs on schedule.
- Redeploy from `deploy.sh` in minutes; logs and costs visible.
- Measured numbers: cold start time, per-turn latency, cost per call minute.

---

## Stage 3 — Scale: many clients, price and reliability

Details: [06-scale.md](06-scale.md)

**Goals**
- Serve many businesses safely (no data leaks), reliably (no dropped/laggy calls) and
  with a known, acceptable **cost per business**.

**Optimal path** — introduce each item when its trigger appears, in this order:
1. **Tenant isolation enforced** — RLS + `app_user` + isolation tests. *Hard gate before
   the second business.*
2. **Onboarding without code** — owner login, profile confirmation, phone number mapping,
   greeting audio, enabled tools.
3. **Cost visibility** — cost per business (call minutes, speech, tokens) → pricing.
4. **Call reliability** — cold start during ringing (DEC-35), load test → `--concurrency`, `--max-instances`.
5. **Operations** — CI/CD, Terraform, staging/prod, alerts.
6. **Database growth** — Neon free → Launch (usage-based), pooling, backups, HNSW index.
7. **Cost reduction** — voice mode cost per minute (decided in stage 2), telephony provider, prompt size,
   caching.
8. **Compliance** — region, retention, recordings policy, calendar OAuth token security.

**Exit criteria**
- New businesses onboarded without code changes; isolation tests pass.
- Cost per business measured and below the price charged.
- Call latency within budget under load.

---

## Configuration (same code in every stage)

```bash
# Stage 1 — .env (copy of .env.example)
DATABASE_URL=postgresql+psycopg://app:app@localhost:5432/app
LLM_BASE_URL=http://localhost:1234/v1       # LM Studio server
LLM_API_KEY=lm-studio                        # any value; LM Studio doesn't check it
LLM_MODEL=google/gemma-4-12b                 # chat/voice (DEC-32); id as shown by `lms ps`
EMBED_BASE_URL=http://localhost:1234/v1
EMBED_API_KEY=lm-studio
EMBED_MODEL=text-embedding-nomic-embed-text-v1.5
EMBED_DIM=768
VOICE_LANGUAGE=en-US                         # Google Speech; auth = ADC
STT_MODEL=latest_short                       # `phone_call` for 8 kHz Twilio audio — compare
TTS_VOICE=en-US-Neural2-F                    # voice names change — re-check the catalog
VOICE_REASONING_EFFORT=none                  # thinking off for voice (DEC-29)
DEV_RELOAD=true                              # live reload of web/ pages; never set in cloud
# in Docker Compose (end of stage 1): use host.docker.internal / service names instead of localhost

# Stage 2 — Cloud Run env (secrets from Secret Manager)
DATABASE_URL=postgresql+psycopg://app:<secret>@<neon-pooler-host>/app?sslmode=require
LLM_BASE_URL=https://generativelanguage.googleapis.com/v1beta/openai/
LLM_API_KEY=<secret>
LLM_MODEL=gemini-2.5-flash        # use the current Flash model at deploy time
EMBED_BASE_URL=https://generativelanguage.googleapis.com/v1beta/openai/
EMBED_API_KEY=<secret>
EMBED_MODEL=gemini-embedding-001
EMBED_DIM=768                     # reduced output dimension, keeps the column size —
                                  # the request must ask for it (default is larger);
                                  # app/llm.py fails fast on a mismatch

# Stage 3 — adds
DATABASE_URL=postgresql+psycopg://app_user:<secret>@...   # non-owner role, RLS applies
ADMIN_DATABASE_URL=<secret>                               # owner role, migrations only (direct connection)
```

Gemini is reachable through the Gemini API (API key, simplest) or Vertex AI (IAM, regional
data residency); both are OpenAI-compatible (OPEN-07). Free-tier Gemini may use submitted
data — paid key from the first real customer data (R12). Native Google SDKs only for
Speech and Gemini Live (R5).

What we deliberately **don't** use in any stage: separate vector DB (Qdrant, Vertex AI
Vector Search), Firestore, LangChain/LlamaIndex, local speech models, file storage for
pages/chunks, VMs, Kubernetes. Reasons in [DECISIONS.md](DECISIONS.md).

## Code structure

```
app/
  main.py           FastAPI app
  config.py         settings from env (pydantic-settings)
  db/               SQLAlchemy models, Alembic migrations, tenant_session helper
  llm.py            OpenAI-compatible client: chat, embed
  ingest/           crawler, cleaner, chunker, Places client
  rag/              index, retrieve (SQL), prompt, answer
  voice/            Twilio routes, WebSocket session, Google STT/TTS
  actions/          tools: booking, appointment, summary, message, transfer
  dashboard/        owner UI (Jinja + HTMX)
docker-compose.yml  stage 1: postgres only · stage-1 exit: + api + crawler
Dockerfile          API image
Dockerfile.crawler  crawler image with Crawl4AI + headless browser (R8) — once needed (DEC-36)
.env.example
```

## Open questions

Tracked in [DECISIONS.md](DECISIONS.md) → section 4 "Still considering", grouped by stage.
