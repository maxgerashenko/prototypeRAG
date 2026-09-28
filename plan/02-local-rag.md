# Part 2 — Local RAG (local LLM + local vector database)

**Goal:** answer questions about a business using only its own data, running entirely on
the local machine at zero cost, with code that later switches to Google Cloud by config.

**Done when:** `POST /chat` answers a set of test questions for an ingested business
correctly, and says "I don't know" for questions not covered by the data.

---

## Local stack (Docker Compose)

| Service | Image | Purpose |
|---|---|---|
| `api` | our FastAPI image | RAG endpoints, later voice + actions |
| `qdrant` | `qdrant/qdrant` | Vector database (ports 6333/6334, volume for storage) |
| `postgres` | `postgres:16` | Businesses, custom replies, conversations, bookings |
| `ollama` | `ollama/ollama` | Local LLM + embedding model |

Models (via Ollama):
- **Embeddings:** `nomic-embed-text` (768 dims) — small, fast, good quality.
- **LLM:** a general chat/instruct model sized to the hardware, e.g. `llama3.1:8b` or a
  Qwen instruct model. Not a coding model — a customer-facing assistant needs
  conversational quality.

On a Mac, run Ollama natively (uses Apple GPU) and point the container at
`http://host.docker.internal:11434`; Ollama inside Docker on macOS is CPU-only and slow.

## Data model

**Qdrant:** one collection `business_chunks`, every point has `business_id` in the
payload (indexed) and every search filters by it. Simpler than a collection per business
and scales to many businesses. Payload also stores `text, source_url, section_heading,
kind` where `kind` is `scraped | custom_reply | review`.

**Postgres:**
- `businesses` — id, name, website, phone number(s), settings (tone, language, greeting)
- `business_profile` — structured facts from Part 1
- `custom_replies` — owner-defined Q/A overrides (also embedded into Qdrant with `kind=custom_reply`)
- `conversations`, `messages` — history (used by Part 4 summaries)

## Answer pipeline

```
question ─► embed ─► Qdrant search (filter business_id, top-k 5)
                         │  + keyword/BM25 (hybrid, Qdrant sparse vectors)
                         ▼
             custom replies boosted to the top
                         ▼
 prompt = system rules + business profile + retrieved chunks + recent history + question
                         ▼
                   LLM (streaming) ─► answer
```

Prompt rules:
- Answer only from the provided profile and context.
- If the answer isn't there: say so and offer to take a message / transfer (Part 4).
- Keep answers short (they will also be spoken in Part 3).
- Answer in the caller's language.

## Provider abstraction (key for cloud migration)

```python
class LLMProvider(Protocol):
    def chat(self, messages: list[dict], tools: list | None = None, stream: bool = False): ...

class EmbedProvider(Protocol):
    dim: int
    def embed(self, texts: list[str]) -> list[list[float]]: ...

class VectorStore(Protocol):
    def upsert(self, business_id: str, chunks: list[Chunk]) -> None: ...
    def search(self, business_id: str, vector: list[float], k: int) -> list[Hit]: ...
    def delete_by_source(self, business_id: str, source_url: str) -> None: ...
```

Implementations selected by env vars: `LLM_PROVIDER=ollama|vertex`,
`EMBED_PROVIDER=ollama|vertex`, `VECTOR_DB=qdrant|pgvector`. Nothing outside
`app/providers/` imports Ollama, Qdrant, or Vertex directly.

The LLM interface supports **tool calling** from the start — Part 4 needs it.

## Code layout

```
app/
  main.py               FastAPI app
  config.py             settings from env (pydantic-settings)
  api/chat.py           POST /chat, GET /chat/stream (SSE)
  rag/
    index.py            load chunks.jsonl → embed → upsert
    retrieve.py         hybrid search + custom reply boost
    prompt.py           prompt builder
    answer.py           full pipeline
  providers/            llm.py, embeddings.py, vectordb.py
  db/                   SQLAlchemy models, Alembic migrations
web/chat.html           minimal test chat page
tests/eval/<business>.yaml  question → expected facts
docker-compose.yml, Dockerfile, .env.example
```

## Tasks

- [ ] `docker-compose.yml` (api, qdrant, postgres) + Ollama setup notes
- [ ] Config + provider interfaces + Ollama/Qdrant implementations
- [ ] Postgres schema + migrations
- [ ] Indexer: `chunks.jsonl` → Qdrant (idempotent upserts by content hash)
- [ ] Retrieval (vector first, then add hybrid sparse search)
- [ ] Prompt builder + answer pipeline with streaming
- [ ] `/chat` endpoint with conversation history
- [ ] Custom replies CRUD + embedding
- [ ] Simple chat web page
- [ ] Eval script: run test questions, check expected facts appear, report score

## Notes

- Measure retrieval separately from generation: for each eval question, check that the
  right chunk is in top-k before tuning prompts.
- Small local models hallucinate more than Gemini — good for learning, but judge final
  quality with the cloud model too.
- Embedding model is part of the index: changing it (e.g. to Vertex) means re-indexing
  from `chunks.jsonl`.
