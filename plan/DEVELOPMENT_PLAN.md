# Development Plan — Local First, Then Google Cloud (Cheap)

Principles:
1. **Build and learn locally** at zero cost; every component runs in Docker.
2. **Same container image** runs locally and on Google Cloud — only environment
   variables change.
3. **Scale to zero / pay per use** in the cloud; avoid anything that bills 24/7 unless it
   is tiny.
4. **One deployment serves all businesses** (multi-tenant) — not one stack per business.
   Per-business isolation is done with data (collection or `business_id` filter), config,
   and phone number mapping. This is what keeps cost per business near zero.

---

## Phase 1 — Local, cloud-compatible

Details: [02-local-rag.md](02-local-rag.md)

### Stack

| Component | Local | Google Cloud equivalent |
|---|---|---|
| API / app | FastAPI in Docker | Cloud Run (same image) |
| Vector DB | Qdrant in Docker | Qdrant on small VM / Qdrant Cloud, or pgvector on Cloud SQL |
| Relational data (businesses, bookings, conversations) | Postgres in Docker | Cloud SQL Postgres (or same Postgres with pgvector) |
| Embeddings | Ollama `nomic-embed-text` or sentence-transformers | Vertex AI `text-embedding` / `gemini-embedding` |
| LLM | Ollama (general instruct model, e.g. Llama 3.1 8B / Qwen instruct) | Gemini Flash on Vertex AI |
| STT / TTS | faster-whisper / Piper | Google Speech-to-Text / Text-to-Speech, or Gemini Live |
| Crawler jobs | Python script / container | Cloud Run Jobs + Cloud Scheduler |
| Files (raw pages, recordings) | local folder | Cloud Storage |
| Secrets | `.env` | Secret Manager |
| Phone webhooks | Twilio → ngrok tunnel | Twilio → Cloud Run URL |

Note: use a general chat/instruct model locally, not a coding model (e.g. `qwen2.5-coder`)
— a customer-facing assistant needs conversational quality, not code generation.

### Code structure (provider abstraction)

Every external dependency sits behind a small interface, chosen by env var:

```
app/
  api/            FastAPI routes: /chat, /voice/ws, /twilio/*, /admin/*
  ingest/         crawler, cleaner, chunker, places_client
  rag/            retriever, prompt builder, answer pipeline
  providers/
    llm.py        LLMProvider      -> OllamaLLM | GeminiLLM
    embeddings.py EmbedProvider    -> OllamaEmbed | VertexEmbed
    vectordb.py   VectorStore      -> QdrantStore | PgVectorStore
    speech.py     STT / TTS        -> Whisper/Piper | Google
  actions/        tools: book_table, make_appointment, summarize, transfer
  models/         DB schema (SQLAlchemy)
docker-compose.yml   api, qdrant, postgres, ollama
Dockerfile
.env.example
```

```bash
# .env (local)
LLM_PROVIDER=ollama
EMBED_PROVIDER=ollama
VECTOR_DB=qdrant
VECTOR_DB_URL=http://qdrant:6333
DATABASE_URL=postgresql://app:app@postgres:5432/app

# .env (cloud) — same code, different values
LLM_PROVIDER=vertex
EMBED_PROVIDER=vertex
VECTOR_DB_URL=https://<qdrant-host>:6333
VECTOR_DB_API_KEY=<from Secret Manager>
```

Using LangChain/LlamaIndex for these interfaces is optional; thin hand-written wrappers
are easier to understand while learning and have fewer dependencies.

**Important:** the embedding model must be the same for ingestion and queries. Switching
local → Vertex embeddings means **re-embedding** the data (different vector dimensions),
not just copying the Qdrant snapshot. Store raw chunks (Markdown) so re-embedding is a
one-command job.

### Local steps

1. `docker-compose.yml` with Qdrant, Postgres, Ollama; FastAPI skeleton + health check.
2. Ingestion pipeline (Part 1) → one real business ingested.
3. RAG `/chat` endpoint (Part 2) + a tiny web chat page + eval questions.
4. Local voice loop (mic → whisper → RAG → Piper) (Part 3).
5. Twilio number → ngrok → `/twilio` webhook + media stream (Part 3).
6. Tools: summary, booking, appointment (Part 4) + minimal owner dashboard.
7. Multi-tenant: `businesses` table, phone-number → business mapping, per-business config.

---

## Phase 2 — Google Cloud, as cheap as possible

Details: [05-cloud-migration.md](05-cloud-migration.md)

### Target architecture

```
Twilio ──► Cloud Run: api (FastAPI, WebSockets) ──► Vertex AI Gemini (LLM, embeddings)
                 │            │
                 │            └──► Vector DB (Qdrant VM / Qdrant Cloud / pgvector)
                 └──► Cloud SQL Postgres (or Postgres on the same VM)
Cloud Scheduler ──► Cloud Run Job: ingest/re-crawl ──► Cloud Storage (raw pages)
```

### Vector DB options (cost)

| Option | Approx. cost | Notes |
|---|---|---|
| Qdrant Cloud free tier (1 GB, runs on GCP) | $0 | Enough for many small businesses to start; zero migration effort |
| Qdrant on `e2-small`/`e2-medium` VM (optionally Spot) | ~$7–25/mo | Same Docker image as local; Spot can be preempted — keep snapshots in Cloud Storage |
| pgvector on Cloud SQL (smallest tier) | ~$10–30/mo | One DB for vectors + bookings + conversations; simplest ops |
| Vertex AI Vector Search | $$ (always-on endpoint) | Overkill and expensive at this scale — avoid for now |

**Recommendation:** start with Qdrant Cloud free tier (or pgvector if we want one
database for everything). Move to a VM only when free tier is outgrown.

Website data for a small business is small (hundreds to a few thousand chunks), so even
many businesses fit in a small instance.

### Compute and cost notes

- **Cloud Run**, min instances = 0 for chat/admin → pay only per request.
- **Voice latency:** a cold start during a phone call is noticeable. Options: min
  instances = 1 for the voice service only (~few $/mo with CPU throttling off only when
  needed), or accept cold start for the prototype. Cloud Run supports WebSockets with
  request timeout up to 60 min — enough for calls.
- **LLM:** Gemini Flash (pay per token, cheap). Cache business profile in the prompt;
  keep retrieved context small (top-k 3–5) to control token cost.
- **Ingestion** runs as a Cloud Run Job on a schedule (e.g. weekly), not a live service.
- **Budget alerts** in Cloud Billing from day one.
- Biggest real cost at scale will be **telephony minutes + STT/TTS**, not the vector DB.

### Deployment steps

1. Create GCP project, enable Cloud Run, Artifact Registry, Vertex AI, Secret Manager,
   Cloud Scheduler; set budget alert.
2. Build image → push to Artifact Registry (`gcloud builds submit`).
3. Provision vector DB (Qdrant Cloud free tier) and Postgres.
4. Re-run ingestion with Vertex embeddings to populate cloud vector DB.
5. Deploy `api` to Cloud Run with cloud `.env` values from Secret Manager.
6. Point Twilio webhook to the Cloud Run URL; test a call.
7. Deploy ingestion as Cloud Run Job + Cloud Scheduler trigger.
8. Later: CI/CD (GitHub Actions → build → deploy), Terraform for infra.

### Onboarding a new business (target flow)

1. Owner enters website URL (+ Google listing) in the dashboard.
2. Ingestion job runs → knowledge base ready.
3. Buy/assign a Twilio number, map it to `business_id`.
4. Owner reviews answers, adds custom replies, connects calendar.

No new deployment per business — just rows in the database and a phone number.

---

## Open questions

- Which business type to start with (restaurant vs appointment-based)? Drives which
  booking integration comes first.
- Languages needed for voice (affects STT/TTS/model choice)?
- Twilio vs other telephony provider (pricing in target country)?
- Store call recordings or only transcripts (privacy / GDPR)?
