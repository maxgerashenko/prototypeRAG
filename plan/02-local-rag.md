# Part 2 — Local RAG (local LLM + local vector database)

**Goal:** answer questions about a business using only its own data, running on the
local machine at zero cost, with the same technology that runs on Google Cloud.

**Done when:** `POST /chat` answers a set of test questions for an ingested business
correctly, says "I don't know" for questions not covered by the data, and every step
(chunks, vectors, search results, prompt) can be inspected by hand.

---

## Local stack

| Service | How it runs | Purpose | Cloud equivalent |
|---|---|---|---|
| `api` | Docker (our image) | FastAPI: RAG, later voice + actions | Cloud Run |
| `postgres` | Docker `pgvector/pgvector:pg16` | Vectors **and** all app data | Managed Postgres + pgvector (Neon → Cloud SQL) |
| Ollama | **Natively on the Mac** (uses GPU) | LLM + embeddings via OpenAI-compatible API | Gemini via OpenAI-compatible API |

The container reaches Ollama at `http://host.docker.internal:11434/v1`.

Models (via Ollama):
- **Embeddings:** `nomic-embed-text` (768 dims).
- **LLM:** a general chat/instruct model sized to the hardware with tool-calling support,
  e.g. `llama3.1:8b` or a Qwen instruct model. Not a coding model.

## Data model (Postgres)

```sql
CREATE EXTENSION IF NOT EXISTS vector;

businesses       (id, name, website, timezone, phone_numbers, settings jsonb)
business_profile (business_id, name, address, phone, opening_hours jsonb, place_id, ...)  -- from Part 1
pages            (id, business_id, url, title, markdown, content_hash, scraped_at)
chunks           (id, business_id, page_id, kind, section_heading, text,
                  embedding vector(768), embed_model, tsv tsvector GENERATED, content_hash)
custom_replies   (id, business_id, question, answer)          -- owner overrides, also chunked (kind='custom_reply')
conversations    (id, business_id, channel, started_at, ...)
messages         (id, business_id, conversation_id, role, content, created_at)
```

- `kind`: `scraped | custom_reply` (no reviews — Places terms, R9).
- Every table has `business_id`; isolation is enforced by Postgres Row-Level Security
  (see [ARCHITECTURE_DRIVERS.md](ARCHITECTURE_DRIVERS.md) R18).
- Every query filters by `business_id`. A business has hundreds to a few thousand chunks,
  so exact vector search within one business is fast — no ANN index needed at first.
  Add an HNSW index later if data grows.
- `tsv` (generated full-text column + GIN index) enables keyword search for hybrid retrieval.
- `embed_model` records which model produced the vector, so a re-index is detectable.
- Roles: migrations run as the table owner (`ADMIN_DATABASE_URL`); the app connects as
  `app_user` (`DATABASE_URL`) and sets `SET LOCAL app.business_id` per transaction.
  A single `tenant_session(business_id)` helper in `app/db/` opens the transaction and
  sets it — no query runs without it. Only `SET LOCAL`, never `SET` (pooler, R20).

## Answer pipeline

```
question ─► embed (OpenAI-compatible /embeddings)
             ▼
   SQL: vector search   ORDER BY embedding <=> :q     WHERE business_id = :b   (top 10)
   SQL: keyword search  ts_rank(tsv, query)           WHERE business_id = :b   (top 10)
             ▼
   merge with reciprocal rank fusion → top 5, custom replies boosted first
             ▼
   prompt = system rules + business profile + chunks + recent history + question
             ▼
   LLM (OpenAI-compatible /chat/completions, streaming, tools) ─► answer
```

Prompt rules:
- Answer only from the provided profile and context.
- If the answer isn't there: say so and offer to take a message / transfer (Part 4).
- Keep answers short (they will also be spoken in Part 3).
- Answer in the caller's language.

## LLM client — one client for local and cloud

```python
from openai import OpenAI

chat_client  = OpenAI(base_url=settings.llm_base_url,   api_key=settings.llm_api_key)
embed_client = OpenAI(base_url=settings.embed_base_url, api_key=settings.embed_api_key)

def embed(texts: list[str]) -> list[list[float]]:
    r = embed_client.embeddings.create(model=settings.embed_model, input=texts)
    return [d.embedding for d in r.data]
```

No provider classes, no LangChain. Switching to Gemini = changing `LLM_*` / `EMBED_*`
env vars. Tool calling uses the standard `tools=[...]` parameter from the start — Part 4
needs it.

## Code layout

```
app/
  main.py               FastAPI app
  config.py             settings from env (pydantic-settings)
  llm.py                OpenAI-compatible chat + embed helpers
  db/                   SQLAlchemy models, Alembic migrations (enables pgvector)
  api/chat.py           POST /chat, GET /chat/stream (SSE), GET /debug/retrieve
  rag/
    index.py            chunks without embedding (or wrong embed_model) → embed → UPDATE
    retrieve.py         vector + keyword SQL, rank fusion, custom reply boost
    prompt.py           prompt builder
    answer.py           full pipeline
web/chat.html           minimal test chat page (shows retrieved chunks next to the answer)
tests/eval/<business>.yaml  question → expected facts
docker-compose.yml, Dockerfile, .env.example
```

## Manual testing — see how it works

- `psql` into the database: look at `pages`, `chunks`, vectors, run similarity queries by hand.
- `GET /debug/retrieve?business_id=..&q=..` returns the retrieved chunks with scores.
- Chat page shows the answer **and** the chunks and prompt it was built from.
- Log every LLM call (prompt, response, tokens, latency) during development.

## Tasks

- [ ] `docker-compose.yml` (api + pgvector Postgres) + Ollama install notes
- [ ] Config + `llm.py` (OpenAI-compatible client)
- [ ] Schema + Alembic migrations (`vector` extension, `tsv` column + GIN index, RLS policies, `app_user` role)
- [ ] Tenant isolation tests (business A never sees business B)
- [ ] Indexer: embed chunks missing an embedding or with a different `embed_model`
- [ ] Retrieval: vector search → add keyword search → rank fusion
- [ ] Prompt builder + answer pipeline with streaming
- [ ] `/chat` endpoint with conversation history
- [ ] Debug retrieval endpoint + chat page showing sources
- [ ] Custom replies CRUD + indexing
- [ ] Eval script: run test questions, check expected facts appear, report score
- [ ] Run the eval with Gemini (only env vars changed) and compare to the local model

## Notes

- Measure retrieval separately from generation: for each eval question, check that the
  right chunk is in the top 5 before tuning prompts.
- Changing the embedding model means re-embedding all chunks; the indexer handles it
  via `embed_model`. Keep `EMBED_DIM=768` in both environments so the column type stays.
