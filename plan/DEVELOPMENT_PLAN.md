# Development Plan — Local First, Then Google Cloud

## Principles

1. **Learn locally at zero cost.** Run the LLM and the vector database on the laptop and
   test everything by hand before paying for cloud.
2. **Only technology that exists both locally and as a managed Google Cloud service.**
   No local-only tools that must be replaced later, no self-managed servers in the cloud.
3. **Standard interfaces instead of custom abstractions.** Postgres for all data, the
   OpenAI-compatible API for all LLM calls. Moving to cloud = changing environment
   variables.
4. **Same Docker image** locally and on Cloud Run.
5. **Simple but scalable.** Managed, scale-to-zero services; no Kubernetes, no VMs, no
   extra vector database service.
6. **One deployment for all businesses** (multi-tenant): a business is rows in the
   database with a `business_id`, plus a phone number mapping.

## Stack

| Layer | Local | Google Cloud | Change on migration |
|---|---|---|---|
| App (API, voice, dashboard) | FastAPI in Docker | **Cloud Run** | none — same image |
| Crawler | CLI in the same image | **Cloud Run Job** + **Cloud Scheduler** | none |
| Vectors + all app data | **Postgres 16 + pgvector** (`pgvector/pgvector:pg16`) | **Cloud SQL for PostgreSQL + pgvector** | `DATABASE_URL` |
| LLM (chat + tool calling) | **Ollama** via OpenAI-compatible API | **Gemini** via OpenAI-compatible API | `LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL` |
| Embeddings | Ollama `nomic-embed-text` via OpenAI-compatible API | Gemini embeddings via OpenAI-compatible API | `EMBED_*` vars + one re-index |
| Speech-to-Text / Text-to-Speech | Google Speech APIs (called from laptop) | same | none |
| Telephony | Twilio → ngrok | Twilio → Cloud Run URL | webhook URL |
| Secrets | `.env` | **Secret Manager** → env vars | none in code |

What we deliberately **don't** use:
- **Separate vector DB** (Qdrant, Vertex AI Vector Search) — pgvector in Postgres covers
  our scale (hundreds to thousands of chunks per business) and removes a whole service.
- **LangChain / LlamaIndex** — the `openai` Python client + SQL is enough and easier to
  understand and debug.
- **Local speech models** (Whisper, Piper) — local-only tech; Google Speech costs cents
  while testing and behaves identically in cloud.
- **File storage** for pages/chunks — stored in Postgres, so no local-folder vs Cloud
  Storage switch.
- **VMs, Kubernetes** — Cloud Run + Cloud SQL are managed and scale on their own.

## Why these two interfaces make migration smooth

**Postgres + pgvector** — the same SQL, extension and migrations run in Docker and in
Cloud SQL. Vectors, full-text search, businesses, bookings and conversations live in one
database, so there is nothing to keep in sync.

**OpenAI-compatible API** — Ollama and Gemini both serve `/chat/completions` and
`/embeddings` with tool calling, so one client works for both:

```python
from openai import OpenAI
llm = OpenAI(base_url=settings.llm_base_url, api_key=settings.llm_api_key)
llm.chat.completions.create(model=settings.llm_model, messages=..., tools=...)
```

```bash
# .env.local
DATABASE_URL=postgresql+psycopg://app:app@postgres:5432/app
LLM_BASE_URL=http://host.docker.internal:11434/v1
LLM_API_KEY=ollama
LLM_MODEL=llama3.1:8b
EMBED_BASE_URL=http://host.docker.internal:11434/v1
EMBED_MODEL=nomic-embed-text
EMBED_DIM=768

# .env.cloud — same code, different values (secrets from Secret Manager)
DATABASE_URL=postgresql+psycopg://app:<secret>@/app?host=/cloudsql/<project>:<region>:<instance>
LLM_BASE_URL=https://generativelanguage.googleapis.com/v1beta/openai/
LLM_API_KEY=<secret>
LLM_MODEL=gemini-2.5-flash        # use the current Flash model at deploy time
EMBED_BASE_URL=https://generativelanguage.googleapis.com/v1beta/openai/
EMBED_MODEL=gemini-embedding-001
EMBED_DIM=768                     # reduced output dimension, keeps the column size
```

Gemini is reachable through the Gemini API (API key, simplest) or Vertex AI (IAM/service
account, regional data residency); both offer OpenAI-compatible endpoints. Start with the
Gemini API on a **paid** key — free-tier data may be used by Google to improve products,
which is not acceptable for customer data.

Native Google SDKs are used only where the OpenAI-compatible API can't do the job
(Speech APIs, Gemini Live for voice).

## Local ↔ cloud differences to keep in mind

- **Embeddings:** `nomic-embed-text` and Gemini produce different vectors → re-embed all
  chunks once when switching (one command; chunk text is in Postgres). Option: use Gemini
  embeddings locally too, so the tested index is the shipped index — costs almost nothing.
- **Answer quality:** small local models hallucinate more and are weaker at tool calling.
  Use them to learn the flow; validate real quality by pointing `LLM_BASE_URL` at Gemini
  before going live.
- **Ollama on Mac:** run it natively (uses the GPU), not in Docker.

## Code structure

```
app/
  main.py           FastAPI app
  config.py         settings from env (pydantic-settings)
  db/               SQLAlchemy models + Alembic migrations (incl. pgvector)
  llm.py            OpenAI-compatible client: chat, embed
  ingest/           crawler, cleaner, chunker, Places client
  rag/              index, retrieve (SQL), prompt, answer
  voice/            Twilio routes, WebSocket session, Google STT/TTS
  actions/          tools: booking, appointment, summary, message, transfer
  dashboard/        owner UI (Jinja + HTMX)
docker-compose.yml  api + postgres(pgvector)
Dockerfile
.env.example
```

## Phase 1 — Local

Details: [02-local-rag.md](02-local-rag.md)

1. `docker-compose.yml` (api + pgvector Postgres), Ollama installed natively, FastAPI
   health check, Alembic migration enabling `vector`.
2. Crawler → one real business stored in Postgres ([01-crawler.md](01-crawler.md)).
3. Index + retrieval + `/chat` + eval questions ([02-local-rag.md](02-local-rag.md)).
   Inspect chunks, vectors and search results directly with `psql`.
4. Voice via Twilio + ngrok with Google Speech ([03-voice-channel.md](03-voice-channel.md)).
5. Tools: summary, booking, appointment + minimal dashboard ([04-actions.md](04-actions.md)).
6. Second business onboarded with no code changes.
7. Switch `LLM_*` to Gemini while still local → compare quality.

## Phase 2 — Google Cloud

Details: [05-cloud-migration.md](05-cloud-migration.md)

```
Twilio ──► Cloud Run: api (FastAPI, WebSockets) ──► Gemini (chat, embeddings)
                 │                              ──► Google Speech-to-Text / Text-to-Speech
                 └──► Cloud SQL Postgres + pgvector (vectors + all app data)
Cloud Scheduler ──► Cloud Run Job: ingest / re-crawl ──► Cloud SQL
```

Base cost: Cloud SQL smallest instance ≈ $10/month; Cloud Run, Scheduler and Jobs are
mostly within free tier at low traffic; Gemini and Speech are pay-per-use. Main cost at
scale: telephony + speech minutes.

## Open questions

- Which business type first (restaurant vs appointment-based)? Drives which booking
  integration comes first.
- Languages needed for voice?
- Twilio vs other telephony provider (pricing in target country)?
- Store call recordings or only transcripts (privacy / GDPR)?
