# Project Plan — Business Voice/Chat Assistant (RAG)

## Goal

Given a business (restaurant, clinic, salon, …), automatically collect its public
information, store it in Postgres + pgvector (vectors and all data), and put an AI assistant in front of it that
answers customer questions by **chat** and by **phone call**, and can take **actions**
such as booking a table or an appointment.

One codebase serves many businesses (multi-tenant): each business gets its own
knowledge base, phone number, and settings.

Decisions, priorities and open questions: [DECISIONS.md](DECISIONS.md) ·
restrictions: [ARCHITECTURE_DRIVERS.md](ARCHITECTURE_DRIVERS.md)

Detailed plan per part:
[1 Crawler](01-crawler.md) ·
[2 Local RAG](02-local-rag.md) ·
[3 Voice channel](03-voice-channel.md) ·
[4 Actions](04-actions.md) ·
[5 Cloud — stage 2](05-cloud-migration.md) ·
[6 Scale — stage 3](06-scale.md)

Built in three learning stages — **1 Local** (understand the principles) → **2 Cloud**
(understand the infrastructure, one business) → **3 Scale** (many businesses, price and
reliability). See [DEVELOPMENT_PLAN.md](DEVELOPMENT_PLAN.md).

```
            ┌────────────── Part 1: Ingestion ──────────────┐
 Website ─┐ │                                               │
 Google  ─┼─► Crawl / API ─► Clean to Markdown ─► Chunk ─► Embed ─► Postgres
 Places  ─┘ │                                               │    + pgvector (business_id)
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
- **Google business listing** — only `place_id` is stored; hours, address, phone are a
  live lookup for display (R9, DEC-11); ratings and reviews are not used. Use the **Google Places API** (not scraping Google Maps pages — that breaks Google's
  ToS and is fragile).
- Optional later: PDFs on the site (menus, price lists), social pages, manual uploads
  by the owner.

Pipeline:
1. **Crawl** — `httpx` + BeautifulSoup first; Crawl4AI (renders JS) when a JS-rendered site needs it (DEC-33). Respect
   `robots.txt`, limit depth/page count, dedupe URLs.
2. **Clean** — strip nav, footer, cookie banners; convert to Markdown so headings
   (e.g. "Pricing", "Opening hours") are preserved.
3. **Chunk** — split by Markdown headers, then ~500–800 tokens with ~10–15% overlap.
4. **Metadata** — every chunk gets `business_id`, `source_url`, `section`, `scraped_at`.
5. **Structured facts** — extract key fields (hours, address, phone, booking policy)
   into a separate structured record; these are too important to leave to vector search.
   From Places only `place_id` is stored; open details in OPEN-04 (R9).
6. **Store + embed** — pages, profile and chunks in Postgres with `business_id`
   (isolation by `tenant_session` now, Row-Level Security from stage 3); the indexer embeds chunks into pgvector.
7. **Refresh** — re-crawl on a schedule; replace changed chunks.

Deliverable: `ingest <url>` command that produces a populated knowledge base for one business.

---

## Part 2 — RAG engine

Details: [02-local-rag.md](02-local-rag.md)

**Goal:** answer questions accurately using only the business's own data.

- **Retrieval:** hybrid search (pgvector + Postgres full-text) filtered by `business_id`,
  merged by reciprocal rank fusion, top 5 chunks.
- **Prompt:** system prompt with business profile (structured facts) + retrieved chunks +
  conversation history. Instruct the model to say "I don't know, let me connect you /
  take a message" instead of guessing.
- **Storage:** Postgres + pgvector for vectors and all data (Docker locally; Neon free tier, later Cloud SQL, in cloud).
- **LLM:** one OpenAI-compatible client — LM Studio locally, Gemini in cloud; switch by env vars.
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
- **Two voice modes, same tools:** pipeline mode (STT → LLM → TTS, local + cloud) and
  Gemini Live (native audio in/out in one streaming session, cloud only) — compared in
  stage 2 on latency, quality and cost (DEC-30). RAG is a tool (`search_business_info`)
  shared by both modes.
- **Latency target:** < ~1.5 s from end of caller speech to start of reply. Use
  streaming everywhere, voice-activity detection, and barge-in (caller can interrupt).
- **Fallbacks:** transfer to a human number or take a message when the bot can't help.
- **Legal:** announce that the call is handled by an AI; mention recording only if the
  business enabled it (off by default, DEC-22).

Deliverable: call a test number, ask about hours/menu, get a spoken answer.

---

## Part 4 — Actions

Details: [04-actions.md](04-actions.md)

**Goal:** the assistant does things, not just answers. Implemented as LLM **tool calls**.

| Action | Description | Integration |
|---|---|---|
| Conversation summary | After each call/chat: summary, intent, caller name/phone, outcome | Stored in DB, shown in owner dashboard, optional email/SMS |
| Book a table | Check availability, create reservation, confirm by SMS | Own bookings table first; later OpenTable / Google Calendar |
| Make an appointment | Find free slot, book, confirm | Own bookings table first; later Google Calendar / Cal.com |
| Take a message | Record request for callback | DB + notification to owner |
| Transfer call | Hand off to a human | Twilio call transfer |

Rules: confirm details back to the caller before committing an action; every action is
logged with the conversation it came from.

Owner dashboard (simple web UI): list of conversations with summaries and transcripts,
bookings, knowledge-base status, "re-scrape" button, custom replies editor.

---

## Milestones

Order follows the build order in [DECISIONS.md](DECISIONS.md) section 1.

| # | Stage | Milestone | Done when |
|---|---|---|---|
| M0 | 1 | Foundation | Postgres/pgvector in Docker, migrations, base tables with `business_id`, `tenant_session` |
| M1 | 1 | Ingestion | `ingest <url>` fills Postgres (pages, profile, chunks) for one real business |
| M2 | 1 | Text RAG | Chat answers test questions correctly, says "don't know" otherwise |
| M3 | 1 | Voice (local) | Talk to the bot through the mic locally |
| M4 | 1 | Phone | Real phone call answered via Twilio (ngrok tunnel locally) |
| M5 | 1 | Actions | Booking + conversation summary working end-to-end |
| M5b | 1 | Gemini check | Eval + booking tests pass with Gemini (env vars only), still local |
| M5c | 1 | Containerized | App + crawler run from Docker images with env-var config only |
| M6 | 2 | Cloud | Pilot business live on Cloud Run + Neon + Gemini, `deploy.sh`, latency/cost measured — [05](05-cloud-migration.md) |
| M7 | 3 | Multi-business | RLS enforced; second business onboarded with no code changes — [06](06-scale.md) |
| M8 | 3 | Profitable scale | Cost per business measured and below price; latency holds under load test |
