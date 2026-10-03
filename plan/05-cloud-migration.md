# Part 5 — Stage 2: Move to Google Cloud (one business)

**Goal:** understand cloud infrastructure by running the stage-1 system on Google Cloud
for **one pilot business**, with managed services only and near-zero idle cost.

**Starts when:** stage 1 exit criteria are met — app and crawler run from Docker images
with env-var config only ([DEVELOPMENT_PLAN.md](DEVELOPMENT_PLAN.md)).

**Done when:**
- The pilot business answers real calls and chat from Cloud Run against Neon and Gemini.
- The crawler runs as a scheduled Cloud Run Job.
- Redeploy from `deploy.sh` takes minutes; logs, budget alert and costs are visible.
- Measured numbers exist: cold start time, per-turn latency, cost per call minute.

Scaling to many businesses, min-instances, load tests, Cloud SQL, CI/CD and Terraform
are **stage 3** → [06-scale.md](06-scale.md).

---

## What to learn in this stage

| Area | What to understand | Where |
|---|---|---|
| Project & billing | Projects, APIs, budget alerts | Step 1 |
| IAM | Service accounts, least-privilege roles | Step 1 |
| Images | Artifact Registry, building with Cloud Build | Step 5 |
| Cloud Run | Revisions, env vars, secrets, timeout, cold starts, WebSockets, logs | Step 6 |
| Batch | Cloud Run Jobs + Cloud Scheduler | Step 7 |
| Secrets | Secret Manager → env vars | Steps 2–3 |
| Managed Postgres | Connection strings, pooling, SSL | Step 3 |
| Observability | Cloud Logging, latency per stage, cost reports | Step 9 |
| Voice models | Gemini Flash in pipeline mode vs Gemini Live (native audio) | Step 11 |

**Approach:** run every step **by hand with `gcloud`** first so each piece is understood;
then capture the commands in `deploy.sh`. No Terraform or CI/CD yet.

## Local → cloud mapping

| Component | Stage 1 (local) | Stage 2 (cloud) | Change needed |
|---|---|---|---|
| API + voice + dashboard | API Docker image | **Cloud Run** service (one service, DEC-26) | none (same image) |
| Crawler | crawler Docker image (or the API image while httpx-only, DEC-33) | **Cloud Run Job** + **Cloud Scheduler** | none (same image) |
| Vectors + app data | Postgres + pgvector in Docker | **Neon** free tier + pgvector (DEC-03) | `DATABASE_URL` |
| LLM | LM Studio (OpenAI-compatible) | **Gemini** paid key (OpenAI-compatible) | `LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL` |
| Embeddings | `nomic-embed-text-v1.5` (LM Studio) | **Gemini embeddings** (768 dims) | `EMBED_*` vars + **re-index** |
| STT / TTS | Google Speech APIs | same | none |
| Secrets | `.env` | **Secret Manager** | mounted as env vars |
| Webhooks | ngrok | Cloud Run HTTPS URL | update Twilio number config |

## Why one Postgres + pgvector (and not a vector DB service)

- Same Postgres + pgvector as local — schema, migrations and SQL queries unchanged.
- One database for vectors, full-text search, businesses, bookings, conversations.
- Bookings need transactions and time-range queries (no double booking) — SQL does this well.
- Scale fits easily: small-business sites are hundreds to a few thousand chunks each.
- Vertex AI Vector Search / Qdrant would add a second service, a second bill and data
  syncing — not needed at this size. Revisit only at millions of chunks.

## Where to host Postgres

The app only sees a standard `DATABASE_URL`, so the host can be changed later with
`pg_dump`/restore without code changes.

| Host | Cost | Pros | Cons |
|---|---|---|---|
| **Neon** (serverless Postgres, pgvector) | Free tier, then usage-based | Scales compute to zero; branching for dev/test | Wakes from idle in ~0.5 s (first query after idle); not in GCP billing |
| Supabase (Postgres, pgvector) | Free tier, then ~$25/month | Generous free tier, dashboard, backups | Free projects **pause after ~1 week of inactivity**; not in GCP billing |
| **Cloud SQL for PostgreSQL** (pgvector) | ~$10/month smallest instance | Inside GCP: IAM, private connection from Cloud Run, one bill, backups/HA | Always-on cost even with zero traffic |

**Decision (DEC-03):** stage 2 uses the **Neon free tier** (≈ $0) — same Postgres,
nothing new to learn about the database itself, so the stage focuses on Cloud Run,
IAM and secrets. Supabase rejected because free projects pause when idle — bad for a
phone line. Neon paid vs Cloud SQL is a stage-3 decision (OPEN-10). Check current
free-tier limits; they change.

Considered and rejected:
- **Firestore (metadata) + Qdrant (vectors)** — two databases to keep in sync, bookings
  without SQL transactions/joins, no single-query hybrid search.
- **Qdrant on a Compute Engine Spot VM** — self-managed; Spot VMs can be stopped at any
  time, taking the database down mid-call.
- **Qdrant Cloud free tier** — only solves vectors; app data would still need a second DB.

## Stage-2 settings (keep simple, measure)

| Setting | Stage 2 value | Why | Stage 3 change |
|---|---|---|---|
| Services | one (`api`) | Simplest to learn | split voice/web if justified (DEC-26) |
| `min-instances` | 0 | $0 idle; **measure** cold starts on calls | 1 for the call-handling service (DEC-16) |
| `--concurrency` | default | One business, little traffic | from load test (DEC-25, OPEN-15) |
| `--timeout` | 3600 s | WebSocket calls must not be cut (R19) | same |
| Billing | request-based | CPU is allocated while a call's WebSocket is open | same (DEC-25) |
| DB role | single app role | One business; no RLS yet | `app_user` + RLS (DEC-04) |
| Region | one region for everything (OPEN-02) | Latency, no egress | same |

## Cost notes (stage 2)

- **Database:** $0 on Neon free tier.
- **Cloud Run, Jobs, Scheduler:** mostly within free tier at pilot traffic.
- **Gemini:** pay per token; keep context small (top 5 chunks, short history).
- **Twilio + speech:** per minute — the main cost even at pilot scale. Record cost per
  call minute as an input for stage 3 pricing.
- **Budget alert** from day one.

## Deployment steps (by hand first, then `deploy.sh`)

1. **Project setup:** create GCP project, enable APIs (Cloud Run, Artifact Registry,
   Secret Manager, Cloud Scheduler, Speech-to-Text, Text-to-Speech), set a **budget
   alert**, create a service account for the app with minimal roles (Secret Accessor,
   Speech user).
2. **Gemini key:** create a paid Gemini API key (or Vertex AI with the service account,
   OPEN-07); store it in Secret Manager. Already tested locally in stage 1 step 6.
3. **Database:** create the Neon project in the same region as Cloud Run; run Alembic
   migrations; store the pooled connection string (app) and the direct one (migrations)
   in Secret Manager (R20).
4. **Data:** re-run the crawler in cloud, or `pg_dump` the local database → restore into
   Neon; then run the indexer to re-embed chunks with Gemini embeddings (R6).
5. **Build & push images:** Artifact Registry repo; build the **API** image
   (`.../app/api`) and the **crawler** image (`.../app/crawler`) with `gcloud builds submit`.
6. **Deploy API:**
   `gcloud run deploy api --image .../app/api --set-secrets ... --timeout 3600`
7. **Deploy crawler job:**
   `gcloud run jobs deploy ingest --image .../app/crawler --command python --args -m,app.ingest.run`
   + Cloud Scheduler trigger (e.g. weekly re-crawl).
8. **Access control first (OPEN-17)**, then **Twilio:** point the pilot number's voice webhook to the Cloud Run URL; test calls;
   generate the pilot's greeting audio (DEC-17).
9. **Observability:** structured logs in Cloud Logging; log latency per voice stage and
   cold starts; check the billing report after the first week.
10. **Capture** all commands in `deploy.sh`; redeploy from it once to prove it works.
11. **Voice mode comparison:** enable Live mode (same tools, DEC-30) on the pilot
    number or a test number; measure latency, answer quality, cost per minute against
    pipeline mode; record the decision (OPEN-08).

## Tasks

- [ ] GCP project + budget alert + service account
- [ ] Gemini paid key in Secret Manager
- [ ] Neon database + migrations + secrets
- [ ] Data load + re-embed with Gemini embeddings
- [ ] Artifact Registry + both images
- [ ] Cloud Run deploy (API) + Cloud Run Job + Scheduler (crawler)
- [ ] Twilio cutover + test calls with the pilot business
- [ ] Measure cold starts, per-turn latency, cost per call minute
- [ ] Gemini Live spike (live mode, same tools) → compare with pipeline mode: latency, quality, cost per minute → decide OPEN-08
- [ ] `deploy.sh`
