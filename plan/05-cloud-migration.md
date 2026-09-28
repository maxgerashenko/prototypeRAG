# Part 5 — Move to Google Cloud (cheap, multi-business)

**Goal:** run the same system on Google Cloud as a service for many businesses, paying
as little as possible — close to $0 when idle.

**Done when:** the Docker image built locally runs on Cloud Run, answers chat and phone
calls for two businesses, and the monthly bill for the base infrastructure is under ~$20.

---

## Principles

1. **Same Docker image** locally and in cloud — only environment variables differ.
2. **Scale to zero** wherever possible (Cloud Run, Cloud Run Jobs, pay-per-token APIs).
3. **One deployment for all businesses.** Businesses are rows in the database, a
   `business_id` filter in the vector DB, and a phone number mapping — not separate
   deployments. Adding a business costs ~nothing in infrastructure.
4. **Budget alerts from day one.**

## Local → cloud mapping

| Component | Local | Google Cloud | Change needed |
|---|---|---|---|
| API + voice | FastAPI container | **Cloud Run** service | none (same image) |
| Crawler / re-crawl | CLI | **Cloud Run Job** + **Cloud Scheduler** | none (same image, different command) |
| LLM | Ollama | **Gemini Flash on Vertex AI** | `LLM_PROVIDER=vertex` |
| Embeddings | `nomic-embed-text` | **Vertex AI embeddings** | `EMBED_PROVIDER=vertex` + **re-index** |
| Vector DB | Qdrant container | Qdrant Cloud free tier / Qdrant on VM / pgvector | URL + API key |
| Relational DB | Postgres container | Cloud SQL Postgres or Postgres on the same VM | `DATABASE_URL` |
| Files | `data/` folder | **Cloud Storage** bucket | storage provider switch |
| STT / TTS | Whisper / Piper | Google Speech-to-Text / TTS or Gemini Live | provider switch |
| Secrets | `.env` | **Secret Manager** | mounted as env vars |
| Webhooks | ngrok | Cloud Run HTTPS URL | update Twilio number config |

## Database options (the main cost decision)

| Option | Approx. monthly | Pros | Cons |
|---|---|---|---|
| **A. Qdrant Cloud free tier** (1 GB, on GCP) + Cloud SQL smallest | $0 + ~$10 | Same Qdrant API as local, no ops | Free tier limits; Cloud SQL is always-on |
| **B. One `e2-small` VM running Qdrant + Postgres in Docker** | ~$7–15 (less with Spot) | Cheapest for both DBs, identical to local compose | We do backups/updates; Spot VMs can be preempted |
| **C. pgvector on Cloud SQL** (vectors + relational in one DB) | ~$10–30 | One managed DB, automatic backups | Different vector store impl than local Qdrant (switchable via interface) |
| Vertex AI Vector Search | $$$ (always-on endpoint) | Massive scale | Far too expensive at this size — avoid |

**Recommendation for start:** option B (one small VM with Docker Compose for Qdrant +
Postgres, daily snapshots to Cloud Storage) or option A if we prefer zero ops. A small
business's site is a few hundred to a few thousand chunks — hundreds of businesses fit
in 1–2 GB.

## Cost notes

- **Cloud Run:** free tier covers a lot of low traffic. Chat/admin: `min-instances=0`.
- **Voice latency vs cost:** a cold start (a few seconds) during a phone call is bad UX.
  Options: `min-instances=1` for the voice service only (costs a few $/month), or accept
  cold start for the prototype. Cloud Run supports WebSockets; set request timeout to
  60 min so calls aren't cut.
- **Gemini Flash:** pay per token; keep context small (top-k 3–5 chunks, short history),
  cache business profile prompt.
- **Biggest real cost at scale is telephony + STT/TTS minutes**, not the database.
  Track cost per call minute per business.
- Keep everything in **one region** (e.g. `europe-west1` / `us-central1`) to avoid
  egress costs and latency.

## Deployment steps

1. **Project setup:** create GCP project, enable APIs (Cloud Run, Artifact Registry,
   Vertex AI, Secret Manager, Cloud Scheduler, Cloud Storage, Speech/TTS), set a
   **budget alert**, create service account with minimal roles.
2. **Implement cloud providers** locally first: `GeminiLLM`, `VertexEmbed`,
   `GCSStorage`, `GoogleSTT/TTS` — test them from the laptop with a service account.
3. **Databases:** provision option A/B/C; run Alembic migrations.
4. **Re-index:** run ingestion/indexing with Vertex embeddings into the cloud vector DB
   (from `chunks.jsonl` — no re-crawl needed).
5. **Build & push image:** `gcloud builds submit --tag <region>-docker.pkg.dev/<project>/app/api`.
6. **Deploy API:** `gcloud run deploy api --image ... --set-secrets ... --timeout 3600`.
7. **Deploy crawler job:** `gcloud run jobs deploy ingest --image ... --command python -m app.ingest.run`
   + Cloud Scheduler trigger (e.g. weekly).
8. **Twilio:** point numbers' voice webhooks to the Cloud Run URL; test a call.
9. **Observability:** structured logs in Cloud Logging, latency per voice stage,
   error alerts, cost dashboard.

## Later improvements

- CI/CD: GitHub Actions → tests → build → deploy on merge to `main`
  (Workload Identity Federation, no JSON keys).
- Infrastructure as code: Terraform for project, Cloud Run, jobs, secrets, buckets.
- Separate `staging` and `prod` environments (same project with prefixes is fine at first).
- Self-service onboarding: owner enters URL → job crawls → number assigned → live.

## Onboarding a new business in cloud

1. Create business row (name, website, timezone, settings).
2. Trigger ingest job for its URL (+ Google Places ID).
3. Buy/assign a Twilio number, map it to `business_id`.
4. Owner reviews test answers, adds custom replies, connects calendar, enables tools.

No new infrastructure per business.

## Tasks

- [ ] GCP project + budget alert + service account
- [ ] Cloud provider implementations + tests from laptop
- [ ] Choose and provision DB option
- [ ] Re-index script for embedding model switch
- [ ] Artifact Registry + Cloud Run deploy (API)
- [ ] Cloud Run Job + Scheduler (crawler)
- [ ] Secret Manager wiring
- [ ] Twilio cutover + test calls
- [ ] Logging, latency metrics, alerts
- [ ] CI/CD pipeline
