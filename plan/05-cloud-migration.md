# Part 5 — Move to Google Cloud

**Goal:** run the same system on Google Cloud as a service for many businesses, with
managed services only, scaling to near zero when idle.

**Done when:** the Docker image built locally runs on Cloud Run against Cloud SQL and
Gemini, answers chat and phone calls for two businesses, and the base infrastructure
costs around $10–20/month.

---

## Principles

1. **Same Docker image** locally and in cloud — only environment variables differ.
2. **Managed services only:** Cloud Run, Cloud SQL, Gemini, Speech APIs, Secret Manager.
   No VMs, no Kubernetes, no separate vector database to operate.
3. **Scale to zero** wherever possible (Cloud Run, Cloud Run Jobs, pay-per-use APIs).
4. **One deployment for all businesses.** Adding a business = database rows + a phone
   number, not new infrastructure.
5. **Budget alerts from day one.**

## Local → cloud mapping

| Component | Local | Google Cloud | Change needed |
|---|---|---|---|
| API + voice + dashboard | FastAPI container | **Cloud Run** service | none (same image) |
| Crawler / re-crawl | CLI | **Cloud Run Job** + **Cloud Scheduler** | none (same image, different command) |
| Vectors + app data | Postgres + pgvector container | **Cloud SQL for PostgreSQL** + pgvector | `DATABASE_URL` |
| LLM | Ollama (OpenAI-compatible) | **Gemini** (OpenAI-compatible) | `LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL` |
| Embeddings | Ollama `nomic-embed-text` | **Gemini embeddings** (768 dims) | `EMBED_*` vars + **re-index** |
| STT / TTS | Google Speech APIs | same | none |
| Secrets | `.env` | **Secret Manager** | mounted as env vars |
| Webhooks | ngrok | Cloud Run HTTPS URL | update Twilio number config |

## Why Cloud SQL + pgvector (and not a vector DB service)

- Same Postgres + pgvector as local — schema, migrations and SQL queries unchanged.
- Fully managed: backups, patches, high availability available when needed.
- One database for vectors, full-text search, businesses, bookings, conversations.
- Scale fits easily: small-business sites are hundreds to a few thousand chunks each.
- Vertex AI Vector Search / Qdrant would add a second service, a second bill and data
  syncing — not needed at this size. Revisit only at millions of chunks.

Sizing: start with the smallest shared-core instance (~$10/month), single zone. Grow the
instance size or enable HA when there are paying customers.

## Cost notes

- **Cloud SQL** is the one always-on cost (~$10/month smallest tier).
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
3. **Cloud SQL:** create PostgreSQL instance + database + user; `CREATE EXTENSION vector`
   via Alembic migration.
4. **Data:** either re-run ingestion in cloud, or `pg_dump` the local database →
   import into Cloud SQL, then run the indexer to re-embed chunks with Gemini embeddings.
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
- [ ] Cloud SQL instance + migrations
- [ ] Data load + re-embed with Gemini embeddings
- [ ] Artifact Registry + Cloud Run deploy (API)
- [ ] Cloud Run Job + Scheduler (crawler)
- [ ] Twilio cutover + test calls
- [ ] Logging, latency metrics, alerts
- [ ] CI/CD pipeline
