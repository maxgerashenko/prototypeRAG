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
| `api` | **Natively** (`uv run`, hot reload) → Docker image at stage-1 exit (DEC-28) | FastAPI: RAG, later voice + actions | Cloud Run |
| `postgres` | Docker `pgvector/pgvector:pg16` | Vectors **and** all app data | Managed Postgres + pgvector (Neon free → Launch, DEC-33) |
| **LM Studio** | **Natively on the Mac** (Apple GPU, MLX/GGUF), 64 GB unified memory | LLM + embeddings via OpenAI-compatible API (`:1234/v1`) | Gemini via OpenAI-compatible API |

The app reaches LM Studio at `http://localhost:1234/v1` (natively) or
`http://host.docker.internal:1234/v1` (from Docker at stage-1 exit; enable "Serve on
Local Network" if the container can't connect). Start the server in the LM Studio app or
with the `lms` CLI (`lms server start`, `lms load <model>`); keep the chat model and the
embedding model loaded at the same time.

Models (via LM Studio, 64 GB Mac — DEC-29):
- **Embeddings:** `nomic-embed-text-v1.5` (768 dims). Uses task prefixes:
  `search_document: ` for chunks, `search_query: ` for questions — part of the indexer
  and retriever, and dropped when switching to Gemini embeddings.
- **LLM:** general instruct models with tool-calling support — not coding models.
  64 GB allows 20–32B-class models (much better than 8B at grounding and tool calls).
  Chosen with the cloud target in mind (DEC-30) — the local model stands in for **Gemini
  Flash in pipeline mode**, so it must be fast, good at native tool calling and follow
  short spoken-style instructions:
  - **Chat/voice: `google/gemma-4-12b`** (DEC-32) — small enough (9.76 GB) to stay
    loaded alongside the code-drafting model below; 7/7 on the bathhouse RAG eval.
    Tool-calling untested so far (stage 1 doesn't exercise tool calls yet) — recheck
    before relying on it for the action tools in `04-actions.md`.
  - Superseded picks (DEC-30's original eval list, kept for history — see DEC-32):
    Qwen3-30B-A3B (MoE, native tool calling), Gemma 3 27B (Gemini's open relative,
    weaker tool calling), gpt-oss-20b.
  - 70B at 4-bit fits (~40 GB) but is too slow for voice — not used.
  - Voice in/out stays with Google Speech locally; local native-audio models are not
    used (R21). Model names are examples; check the current LM Studio catalog.
- **Memory budget:** macOS lets the GPU use roughly 70–75% of unified memory by default
  (~45–48 GB) → model weights + context (KV cache) must fit there; Docker Postgres and
  the app need little. Prefer 4–6-bit quantizations; MLX builds are usually fastest on Mac.
- **Structured output:** LM Studio supports `response_format` with a JSON schema — used
  for business-profile extraction (Part 1).

## Data model (Postgres)

```sql
CREATE EXTENSION IF NOT EXISTS vector;

businesses       (id, name, website, timezone, phone_numbers, settings jsonb)
business_profile (business_id, name, address, phone, opening_hours jsonb, place_id, ...)  -- from Part 1
pages            (id, business_id, url, title, markdown, content_hash, scraped_at)
chunks           (id, business_id, page_id, custom_reply_id, kind, chunk_index, section_heading, text,
                  embedding vector(768), embed_model, tsv tsvector GENERATED, content_hash)
custom_replies   (id, business_id, question, answer)          -- owner overrides, also chunked (kind='custom_reply')
conversations    (id, business_id, channel, started_at, ...)
messages         (id, business_id, conversation_id, role, content, created_at)
```

- `kind`: `scraped | custom_reply` (no reviews — Places terms, R9). Planned: `fact` —
  one row per active fact from the facts library (DEC-38), retrieved in the same hybrid
  search with a boost between custom replies and scraped chunks; chunks and facts get an
  optional `location_id` (DEC-37) — [07-knowledge-quality.md](07-knowledge-quality.md) §5.
- Child rows reference parents by `(business_id, id)` composite foreign keys, so a chunk
  or message can never point at another business's row (DB-enforced already in stage 1).
- `tsv` uses the `simple` text-search config (no stemming): sites and callers may use any
  language. Revisit if keyword recall is weak for one language.
- Every table has `business_id`; isolation will be enforced by Postgres Row-Level Security
  from stage 3 (see [ARCHITECTURE_DRIVERS.md](ARCHITECTURE_DRIVERS.md) R18).
- Every query filters by `business_id`. A business has hundreds to a few thousand chunks,
  so exact vector search within one business is fast — no ANN index needed at first.
  Add an HNSW index later if data grows.
- `tsv` (generated full-text column + GIN index) enables keyword search for hybrid retrieval.
- `embed_model` records which model produced the vector, so a re-index is detectable.
- Stages 1–2: one DB role; every query still runs through `tenant_session(business_id)`,
  which sets `SET LOCAL app.business_id`.
- Stage 3 (before the 2nd business): RLS policies switched on; migrations run as the table
  owner (`ADMIN_DATABASE_URL`); the app connects as `app_user` (`DATABASE_URL`).
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
- Thinking/reasoning modes off for chat and voice (latency); allowed for offline jobs
  (profile extraction, summaries).
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

- [x] `docker-compose.yml` (pgvector Postgres)
- [ ] LM Studio setup notes (models, server, `lms` CLI)
- [x] Model comparison on the eval: gemma-4-12b vs qwen3.6-35b-a3b, both 7/7 on `tests/eval/bathhouse.yaml` (DEC-32); Gemini Flash comparison still open
- [x] Config + `llm.py` (OpenAI-compatible client, built in step 2; `chat`/`chat_json`/`embed`)
- [x] Schema + Alembic migrations (`vector` extension, `business_id` everywhere, `tsv` column + GIN index)
- [x] `tenant_session(business_id)` helper — all queries go through it
- (stage 3: RLS policies, `app_user` role, isolation tests — [06-scale.md](06-scale.md))
- [x] Indexer: embed chunks missing an embedding or with a different `embed_model` — `rag/index.py`
- [x] Retrieval: vector search → add keyword search → rank fusion — `rag/retrieve.py`
- [x] Prompt builder + answer pipeline with streaming — `rag/prompt.py`, `rag/answer.py`
- [x] `/chat` endpoint with conversation history — `api/chat.py`
- [x] Debug retrieval endpoint + chat page showing sources — `api/chat.py`'s `/debug/retrieve`, `web/chat.html`
- [x] Custom replies CRUD + indexing — `api/custom_replies.py`
- [x] Eval script: run test questions, check expected facts appear, report score — `rag/eval.py`
- [ ] Run the eval with Gemini (only env vars changed) and compare to the local model
- [ ] Show the full prompt in the chat page / a debug endpoint (only sources are shown today)
- [ ] Measure `/chat` with thinking on vs `reasoning_effort="none"` — gemma-4-12b thinks by default (~8 s, see [03-voice-channel.md](03-voice-channel.md) V24)
- [ ] Find out why "Saturday opening hours" isn't answered on the pilot — data missing or retrieval miss (V23). Cause found (2026-10-04): data missing — hours are in the site footer, which `clean.py` removes; fixed by the organize step ([07-knowledge-quality.md](07-knowledge-quality.md) §4)
- [ ] Knowledge quality (before step 5): location card + facts in prompt and retrieval, summary in the prompt, extended eval with `expected_in_context` — [07-knowledge-quality.md](07-knowledge-quality.md)
- [ ] Log every LLM call (prompt size, response, tokens, latency) — needed to explain
  DEC-32's 10–17 s turns: measure time-to-first-token with streaming, not total time
- [x] `/chat/stream` saves the turn when the stream ends; multi-line SSE framing fixed
- [x] Vector search only uses chunks embedded by the configured `EMBED_MODEL` (safe mid re-index, R6)

## Notes

- Stage-1 "done when" criteria met (2026-10-03): `POST /chat` answers eval questions
  correctly (7/7 on `tests/eval/bathhouse.yaml`), correctly refuses out-of-scope
  questions, and every step is inspectable (`/debug/retrieve`, `psql`, `web/chat.html`'s
  sources panel). Left open for this part: Gemini comparison, LM Studio setup notes,
  prompt view + LLM call log (below), `Dockerfile` at stage-1 exit.
- Measure retrieval separately from generation: for each eval question, check that the
  right chunk is in the top 5 before tuning prompts.
- Changing the embedding model means re-embedding all chunks; the indexer handles it
  via `embed_model`. Keep `EMBED_DIM=768` in both environments so the column type stays.
