# Part 5 — Move to Google Cloud

**Goal:** run the same system on Google Cloud as a service for many businesses, with
managed services only, scaling to near zero when idle.

**Done when:** the Docker image built locally runs on Cloud Run against cloud Postgres and
Gemini, answers chat and phone calls for two businesses, and the base infrastructure
costs around $10–20/month.

---

## Principles

1. **Same Docker images** locally and in cloud — only environment variables differ
   (one API image, one crawler image — see ARCHITECTURE_DRIVERS R8).
2. **Managed services only:** Cloud Run, managed Postgres, Gemini, Speech APIs, Secret Manager.
   No VMs, no Kubernetes, no separate vector database to operate.
3. **Scale to zero** wherever possible (Cloud Run, Cloud Run Jobs, pay-per-use APIs).
4. **One deployment for all businesses.** Adding a business = database rows + a phone
   number, not new infrastructure.
5. **Budget alerts from day one.**

## Local → cloud mapping

| Component | Local | Google Cloud | Change needed |
|---|---|---|---|
| API + voice + dashboard | FastAPI container | **Cloud Run** service | none (same image) |
| Crawler / re-crawl | CLI | **Cloud Run Job** + **Cloud Scheduler** | none (same crawler image as local) |
| Vectors + app data | Postgres + pgvector container | Managed Postgres + pgvector: **Neon/Supabase** free tier → **Cloud SQL** | `DATABASE_URL` |
| LLM | Ollama (OpenAI-compatible) | **Gemini** (OpenAI-compatible) | `LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL` |
| Embeddings | Ollama `nomic-embed-text` | **Gemini embeddings** (768 dims) | `EMBED_*` vars + **re-index** |
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

Because the app only sees a standard `DATABASE_URL`, the host can be chosen (and
changed later with `pg_dump`/restore) without code changes.

| Host | Cost | Pros | Cons |
|---|---|---|---|
| **Neon** (serverless Postgres, pgvector) | Free tier, then usage-based | Scales compute to zero; branching for dev/test | Wakes from idle in ~0.5 s (first query after idle); not in GCP billing |
| **Supabase** (Postgres, pgvector) | Free tier, then ~$25/month | Generous free tier, dashboard, backups | Free projects **pause after ~1 week of inactivity**; not in GCP billing |
| **Cloud SQL for PostgreSQL** (pgvector) | ~$10/month smallest instance | Inside GCP: IAM, private connection from Cloud Run, one bill, backups/HA | Always-on cost even with zero traffic |

**Decision:** prototype on **Neon or Supabase free tier** (≈ $0). Move to **Cloud SQL**
when there are paying businesses or when everything should live in one GCP project —
a `pg_dump` + restore and a new `DATABASE_URL`. Check current free-tier limits before
choosing; they change.

Considered and rejected:
- **Firestore (metadata) + Qdrant (vectors)** — two databases with different data models
  to keep in sync, bookings without SQL transactions/joins, and no single-query hybrid
  (vector + keyword) search. Firestore scales to zero, but so does serverless Postgres.
- **Qdrant on a Compute Engine Spot VM** — cheap, but self-managed, and Spot VMs can be
  stopped at any time: the database disappears mid-phone-call. Not suitable for a live
  service.
- **Qdrant Cloud free tier** — fine technically, but only solves vectors; app data would
  still need a second database.

## Cost notes

- **Database:** $0 on Neon/Supabase free tier; ~$10/month once moved to Cloud SQL (the only always-on cost).
- **Cloud Run:** free tier covers a lot of low traffic; chat/dashboard `min-instances=0`.
- **Voice latency vs cost:** a cold start during a phone call is bad UX. Options:
  `min-instances=1` for the service handling calls (a few $/month with CPU only allocated
  during requests), or accept cold starts for the prototype. Set request timeout to
  3600 s so WebSocket calls aren't cut.
- **Gemini:** pay per token; keep context small (top 5 chunks, short history).
- **Biggest cost at scale is telephony + speech minutes**, not the database. Track cost
  per call minute per business.
- Keep everything in **one region** (e.g. `europe-west1` or `us-central1`).

## Deployment steps

1. **Project setup:** create GCP project, enable APIs (Cloud Run, Cloud SQL Admin,
   Artifact Registry, Secret Manager, Cloud Scheduler, Speech-to-Text, Text-to-Speech),
   set a **budget alert**, create a service account for the app with minimal roles
   (Cloud SQL Client, Secret Accessor, Speech user).
2. **Gemini key:** create a paid Gemini API key (or use Vertex AI with the service
   account); store it in Secret Manager. Test locally first by pointing `LLM_*` at Gemini.
3. **Database:** create the Postgres database (Neon/Supabase free tier, or Cloud SQL
   instance + user); `CREATE EXTENSION vector` via Alembic migration. Store the
   connection string in Secret Manager.
4. **Data:** either re-run ingestion in cloud, or `pg_dump` the local database →
   import into the cloud database, then run the indexer to re-embed chunks with Gemini embeddings.
5. **Build & push image:** Artifact Registry repo +
   `gcloud builds submit --tag <region>-docker.pkg.dev/<project>/app/api`.
6. **Deploy API:**
   `gcloud run deploy api --image ... --add-cloudsql-instances <conn-name> --set-secrets ... --timeout 3600`
7. **Deploy crawler job:**
   `gcloud run jobs deploy ingest --image ... --command python --args -m,app.ingest.run`
   + Cloud Scheduler trigger (e.g. weekly re-crawl).
8. **Twilio:** point the numbers' voice webhooks to the Cloud Run URL; test a call.
9. **Observability:** structured logs in Cloud Logging, latency per voice stage, error
   alerts, cost per business.

## Later improvements

- CI/CD: GitHub Actions → tests → build → deploy on merge to `main`
  (Workload Identity Federation, no JSON keys).
- Infrastructure as code (Terraform) once the setup is stable.
- Separate `staging` and `prod`.
- Self-service onboarding: owner enters URL → job crawls → number assigned → live.

## Onboarding a new business

1. Create business row (name, website, timezone, settings).
2. Trigger ingest job for its URL (+ Google Places ID).
3. Buy/assign a Twilio number, map it to `business_id`.
4. Owner reviews test answers, adds custom replies, connects calendar, enables tools.

## Tasks

- [ ] GCP project + budget alert + service account
- [ ] Gemini API key in Secret Manager; local run against Gemini
- [ ] Cloud Postgres (Neon/Supabase free tier → Cloud SQL later) + migrations
- [ ] Data load + re-embed with Gemini embeddings
- [ ] Artifact Registry + Cloud Run deploy (API)
- [ ] Cloud Run Job + Scheduler (crawler)
- [ ] Twilio cutover + test calls
- [ ] Logging, latency metrics, alerts
- [ ] CI/CD pipeline
