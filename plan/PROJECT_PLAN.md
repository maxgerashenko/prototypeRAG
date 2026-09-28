# Project Plan — Business Voice/Chat Assistant (RAG)

## Goal

Given a business (restaurant, clinic, salon, …), automatically collect its public
information, store it in a vector database, and put an AI assistant in front of it that
answers customer questions by **chat** and by **phone call**, and can take **actions**
such as booking a table or an appointment.

One codebase serves many businesses (multi-tenant): each business gets its own
knowledge base, phone number, and settings.

Detailed plan per part:
[1 Crawler](01-crawler.md) ·
[2 Local RAG](02-local-rag.md) ·
[3 Voice channel](03-voice-channel.md) ·
[4 Actions](04-actions.md) ·
[5 Move to cloud](05-cloud-migration.md)

```
            ┌────────────── Part 1: Ingestion ──────────────┐
 Website ─┐ │                                               │
 Google  ─┼─► Crawl / API ─► Clean to Markdown ─► Chunk ─► Embed ─► Vector DB
 Places  ─┘ │                                               │        (per business)
            └───────────────────────────────────────────────┘            │
                                                                         ▼
 Phone call ─► Telephony ─► Speech-to-Text ─┐                   ┌── Part 2: RAG ──┐
                                            ├─► Assistant API ─►│ retrieve + LLM  │
 Web chat  ─────────────────────────────────┘        ▲          └────────┬────────┘
                                                     │                   │ tool calls
 Caller  ◄── Text-to-Speech ◄── answer ──────────────┘          ┌── Part 4: Actions ─┐
                                                                │ book / summarize   │
                                                                └────────────────────┘
```

---

## Part 1 — Collect public business data

Details: [01-crawler.md](01-crawler.md)

**Goal:** turn a business URL (and its Google listing) into clean, searchable knowledge.

Sources:
- **Company website** — crawl all internal pages (menu, prices, services, hours, FAQ,
  contacts, about).
- **Google business listing** — hours, address, phone, rating, reviews, categories.
  Use the **Google Places API** (not scraping Google Maps pages — that breaks Google's
  ToS and is fragile).
- Optional later: PDFs on the site (menus, price lists), social pages, manual uploads
  by the owner.

Pipeline:
1. **Crawl** — Crawl4AI (or Playwright + BeautifulSoup for JS-heavy sites). Respect
   `robots.txt`, limit depth/page count, dedupe URLs.
2. **Clean** — strip nav, footer, cookie banners; convert to Markdown so headings
   (e.g. "Pricing", "Opening hours") are preserved.
3. **Chunk** — split by Markdown headers, then ~500–1000 tokens with overlap.
4. **Metadata** — every chunk gets `business_id`, `source_url`, `section`, `scraped_at`.
5. **Structured facts** — extract key fields (hours, address, phone, booking policy)
   into a separate structured record; these are too important to leave to vector search.
6. **Embed + store** in the vector DB, one collection (or tenant filter) per business.
7. **Refresh** — re-crawl on a schedule; replace changed chunks.

Deliverable: `ingest <url>` command that produces a populated knowledge base for one business.

---

## Part 2 — RAG engine

Details: [02-local-rag.md](02-local-rag.md)

**Goal:** answer questions accurately using only the business's own data.

- **Retrieval:** hybrid search (vector + keyword/BM25) filtered by `business_id`,
  top-k chunks, optional re-ranking.
- **Prompt:** system prompt with business profile (structured facts) + retrieved chunks +
  conversation history. Instruct the model to say "I don't know, let me connect you /
  take a message" instead of guessing.
- **Storage:** Postgres + pgvector for vectors and all data (Docker locally, Cloud SQL in cloud).
- **LLM:** one OpenAI-compatible client — Ollama locally, Gemini in cloud; switch by env vars.
- **Custom replies:** per-business overrides stored in the DB (greeting, tone, answers
  the owner wants phrased a specific way, forbidden topics). These are retrieved with
  priority over scraped content.
- **API:** FastAPI — `POST /chat` (text), plus a streaming endpoint for voice.
- **Evaluation:** a small set of test questions per business with expected answers, to
  catch regressions when prompts/chunking change.

Deliverable: working text chat against an ingested business.

---

## Part 3 — Voice & phone calls

Details: [03-voice-channel.md](03-voice-channel.md)

**Goal:** a customer calls a phone number and talks to the assistant.

- **Phone number + call handling:** Twilio (or Telnyx / Vonage). Each business is mapped
  to a number; the incoming number tells us which `business_id` to use.
- **Audio streaming:** Twilio Media Streams → WebSocket → our voice service.
- **Speech-to-Text / Text-to-Speech:** Google Speech APIs, both locally and in cloud.
- **Alternative:** Gemini Live API (native audio in/out) to replace STT + LLM + TTS with
  one streaming call — lower latency, fewer moving parts. Evaluate once text RAG works.
- **Latency target:** < ~1.5 s from end of caller speech to start of reply. Use
  streaming everywhere, voice-activity detection, and barge-in (caller can interrupt).
- **Fallbacks:** transfer to a human number or take a voicemail when the bot can't help.
- **Legal:** announce that the call is handled by an AI and may be recorded.

Deliverable: call a test number, ask about hours/menu, get a spoken answer.

---

## Part 4 — Actions

Details: [04-actions.md](04-actions.md)

**Goal:** the assistant does things, not just answers. Implemented as LLM **tool calls**.

| Action | Description | Integration |
|---|---|---|
| Conversation summary | After each call/chat: summary, intent, caller name/phone, outcome | Stored in DB, shown in owner dashboard, optional email/SMS |
| Book a table | Check availability, create reservation, confirm by SMS | Own bookings table first; later OpenTable / Google Calendar |
| Make an appointment | Find free slot, book, confirm | Google Calendar API / Calendly / Cal.com |
| Take a message | Record request for callback | DB + notification to owner |
| Transfer call | Hand off to a human | Twilio call transfer |

Rules: confirm details back to the caller before committing an action; every action is
logged with the conversation it came from.

Owner dashboard (simple web UI): list of conversations with summaries and transcripts,
bookings, knowledge-base status, "re-scrape" button, custom replies editor.

---

## Milestones

| # | Milestone | Done when |
|---|---|---|
| M1 | Ingestion | `ingest <url>` fills vector DB for one real business |
| M2 | Text RAG | Chat answers test questions correctly, says "don't know" otherwise |
| M3 | Voice (local) | Talk to the bot through the mic locally |
| M4 | Phone | Real phone call answered via Twilio (ngrok tunnel locally) |
| M5 | Actions | Booking + conversation summary working end-to-end |
| M6 | Cloud | Deployed on Google Cloud, see [DEVELOPMENT_PLAN.md](DEVELOPMENT_PLAN.md) |
| M7 | Multi-business | Second business onboarded with no code changes |
