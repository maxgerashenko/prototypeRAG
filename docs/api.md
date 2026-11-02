# API layer

The HTTP and WebSocket edge of the app: one FastAPI process (`app/main.py`) that serves
the REST routes, the Twilio webhooks, the two voice WebSockets, the live call feed (SSE),
and the built web pages. Described as it is on `main` (2026-11-02, stage 1).

Layer docs: [crawler.md](crawler.md) · [data.md](data.md) ·
[voice.md](voice.md) · [actions.md](actions.md) ·
[integrations.md](integrations.md) · **api.md** · [web.md](web.md)

## 1. Purpose and boundaries

The API layer turns HTTP requests, WebSocket frames and Twilio webhooks into calls on the
layers below, and turns their results back into JSON, TwiML, SSE or audio frames. It owns:

- request parsing and validation (path/query params, Pydantic bodies),
- which business a request is for (`business_id` in the path/query/body, or Twilio's `To`
  number / custom parameter),
- transport adapters: Twilio Media Streams (μ-law 8 kHz ↔ PCM16) and the browser socket
  (PCM16 16 kHz),
- the trust checks that exist today (Twilio signature, stream HMAC, loopback-only token),
- app startup and shutdown (`lifespan`).

It does **not** hold business rules. Answering is in the Voice layer, [voice.md](voice.md)
(`app/rag/answer.py`, `app/voice/session.py`), what the app does (bookings, sessions, transferring a call,
SMS, summary) in the Actions layer, [actions.md](actions.md), retrieval and the data model in
the Data layer, [data.md](data.md).
Most handlers are a thin mapping; the exceptions (logic that lives in the route file
itself) are listed in §9.

**Files in this layer**

| File | Holds |
|---|---|
| `app/main.py` | App object, router registration, `lifespan`, `/`, `/health`, `/web` static mount, logging setup |
| `app/api/chat.py` | `/chat`, `/chat/stream`, `/debug/retrieve`, `/debug/prompt` |
| `app/api/custom_replies.py` | Owner Q&A overrides, CRUD + re-indexing their one chunk |
| `app/api/businesses.py` | Business list, main-number assign/unassign |
| `app/api/business_settings.py` | Greeting, top questions, number picker assign |
| `app/api/conversations.py` | Conversation list and transcript (read-only) |
| `app/api/bookings.py` | Bookings day/week view |
| `app/api/sessions.py` | Group sessions create/update/cancel, remove attendee |
| `app/api/dev_reload.py` | Dev-only live reload SSE (only when `DEV_RELOAD=true`) |
| `app/voice/twilio_routes.py` | Twilio webhooks (`/twilio/*`), Voice SDK token, stream token helpers |
| `app/voice/ws.py` | WebSocket `/voice/ws`, Twilio Media Streams adapter |
| `app/voice/browser_ws.py` | WebSocket `/voice/browser`, browser adapter + V19 orphan sweep |
| `app/voice/call_feed.py` | `GET /voice/feed` SSE and the in-memory `LiveCalls` hub (DEC-46) |
| `app/voice/twilio_numbers.py` (route part only) | `GET /twilio/numbers`; the Twilio REST client part belongs to [integrations.md](integrations.md) |

Note the split: four API files live in `app/voice/`, not `app/api/`. They are API by role
(transport and webhooks), voice by topic.

## 2. Route table

All routes are registered in `app/main.py`. None needs a login (stage 1, OPEN-19).
"Tenant" = how the business is chosen; "DB access" = `tenant_session(business_id)`
(sets `app.business_id` for RLS) or `SessionLocal()` (no tenant, reads across businesses).

### Pages and health

| Method | Path | Handler | Returns | Notes |
|---|---|---|---|---|
| GET | `/` | `main.root` | 307 → `/web/` | DEC-42 |
| GET | `/web/mic-test.html` | `main.old_voice_page` | 307 → `/web/` | Old URL; registered before the mount so it wins |
| GET | `/web/*` | `StaticFiles("web/dist", html=True, check_dir=False)` | Built React pages | See [web.md](web.md). Missing `web/dist` is not an error (API and tests start without a build) |
| GET | `/health` | `main.health` | `{"status":"ok"}` | Runs `SELECT 1`; a DB outage is a 500 |
| GET | `/dev/reload` | `dev_reload.dev_reload` | SSE `hello`, `reload` | Only when `DEV_RELOAD=true`; watches `web/dist` |

### Chat and debug (`app/api/chat.py`)

| Method | Path | Input | Returns | Tenant / DB |
|---|---|---|---|---|
| POST | `/chat` | body `{business_id, question, conversation_id?}` | `{answer, conversation_id, sources[], prompt[]}` | body · tenant |
| GET | `/chat/stream` | query `business_id, question, conversation_id?` | SSE: `event: conversation_id`, then text pieces, then `[DONE]` | query · tenant |
| GET | `/debug/retrieve` | query `business_id, q, top_n=5` | `{chunks[]}` incl. full chunk text | query · tenant |
| GET | `/debug/prompt` | query `business_id, q, conversation_id?, top_n=5` | `{messages}` (the prompt `/chat` would send, no LLM call) | query · tenant |

### Business data (`app/api/*`)

| Method | Path | Handler file | Returns | Tenant / DB |
|---|---|---|---|---|
| GET | `/businesses` | `businesses.py` | `BusinessOut[]` (display name, domain, conversation count, default location, numbers) | none · **cross-tenant** |
| POST | `/businesses/{id}/main-number` | `businesses.py` | `{business_id, number}`; moves `TWILIO_PHONE_NUMBER` to this business | path · **cross-tenant write** |
| DELETE | `/businesses/{id}/main-number` | `businesses.py` | `{business_id, number: null}` | path · `SessionLocal` |
| GET / PUT | `/businesses/{id}/greeting` | `business_settings.py` | Greeting state (`text, is_default, cached, cached_at, max_length, full_text`); PUT also `cache_error` | path · tenant |
| GET | `/businesses/{id}/top-questions` | `business_settings.py` | `{total_questions, conversations, days, questions[]}`; query `limit 1–20`, `days 1–365` | path · tenant |
| PUT | `/businesses/{id}/number` | `business_settings.py` | `{business_id, phone_numbers, moved_from}`; body `{number}` (E.164, must be on the Twilio account) | path · **cross-tenant write** |
| GET / POST | `/businesses/{id}/custom-replies` | `custom_replies.py` | `CustomReplyOut[]` / `CustomReplyOut` | path · tenant |
| PUT / DELETE | `/businesses/{id}/custom-replies/{reply_id}` | `custom_replies.py` | `CustomReplyOut` / `{deleted: true}` | path · tenant |
| GET | `/businesses/{id}/conversations` | `conversations.py` | `ConversationSummary[]` (query `limit 1–200`), only conversations with a user turn | path · tenant |
| GET | `/businesses/{id}/conversations/{cid}` | `conversations.py` | `ConversationDetail` with messages and `at_s` offsets | path · tenant |
| GET | `/businesses/{id}/bookings` | `bookings.py` | `{timezone, hours, bookings[], sessions[], busy: [], stale}`; query `start, end` (≤ 62 days), `sync` | path · tenant |
| POST | `/businesses/{id}/sessions` | `sessions.py` | 201 session item; body `{date, hour, name, max_attendees, private?}` | path · in `actions/booking/sessions.py` |
| PATCH | `/businesses/{id}/sessions/{sid}` | `sessions.py` | session item; body `{name?, max_attendees?, private?}` | path · in actions |
| DELETE | `/businesses/{id}/sessions/{sid}` | `sessions.py` | `{cancelled_bookings, texted, no_phone}` (cancels + SMS) | path · in actions |
| DELETE | `/businesses/{id}/sessions/{sid}/attendees/{booking_id}` | `sessions.py` | session item + `texted` | path · in actions |

### Twilio (`app/voice/twilio_routes.py`, `twilio_numbers.py`)

| Method | Path | Called by | Returns | Check |
|---|---|---|---|---|
| POST | `/twilio/voice` | Twilio, voice URL of a number or the TwiML App | TwiML `<Connect><Stream url=wss://…/voice/ws>` + `<Redirect>/twilio/transfer`, or "not in service" + `<Hangup>` | `X-Twilio-Signature` |
| POST | `/twilio/transfer` | Twilio, after the stream closes | `<Dial>` the transfer number (25 s, action `/twilio/transfer-result`) or `<Hangup>` | signature |
| POST | `/twilio/transfer-result` | Twilio, `<Dial action>` | Unanswered → voicemail prompt + `<Record>` (120 s, action `/twilio/voicemail`); then `<Hangup>` | signature |
| POST | `/twilio/voicemail` | Twilio, `<Record action>` | Stores a `MessageForOwner` with the recording URL; "Thank you. Goodbye." | signature |
| POST | `/twilio/status` | Twilio, status callback | 204; on a final status sets `ended_at` and runs the call summary (DEC-24) | signature |
| GET | `/twilio/token` | `web/call.html` (mode B) | `{token, identity}` Voice SDK JWT, 1 h | Loopback client **and** no `X-Forwarded-For` |
| GET | `/twilio/number` | voice app | `{number}` = `TWILIO_PHONE_NUMBER` or null | none |
| GET | `/twilio/numbers` | Settings number picker | account numbers + which business holds each | none (calls the Twilio REST API) |

### WebSockets and live feed

| Kind | Path | Client | Frames | Business from |
|---|---|---|---|---|
| WS | `/voice/ws` | Twilio Media Streams (modes A, B) | JSON text: in `connected/start/media/mark/dtmf/stop`; out `media` (160-byte μ-law frames, base64), `clear`, `mark` | `start.customParameters.business_id` + HMAC `token` |
| WS | `/voice/browser` | `/web/` (ptt), `voice-debug.html` (vad) via `web/src/voice/voiceCall.ts` | Binary PCM16 16 kHz both ways; JSON text events out (`conversation_id, transcript, reply, latency, clear, no_speech, error, hangup`), in `ptt_start / ptt_end` | query `business_id`, `mode=vad\|ptt`, `continue_from?` |
| SSE | `/voice/feed` | `/web/` (watch-only view of Twilio calls) | `data:` JSON: `snapshot`, `call_started`, `call_event`, `call_ended`; `: keepalive` every 15 s | none: every business's calls |

## 3. Request flows

### Chat (`POST /chat`)

1. `_prepare_conversation_and_history`: create a `channel='chat'` conversation if no id
   was given; otherwise check it belongs to the business (404 **before** the LLM call, not
   a 500 from the composite FK at save time). Load prior user/assistant turns.
2. `answer_question(...)` ([voice.md](voice.md)) retrieves, builds the
   prompt and calls the LLM.
3. `_save_turn` writes the user and assistant messages in one tenant transaction.
4. Response includes `sources` and the exact `prompt` sent (debug aid).

`/chat/stream` does step 1 synchronously, then streams; the turn is saved only after the
last piece, so a client that disconnects mid-answer leaves no half-saved turn. `_sse`
splits newlines into several `data:` lines so multi-line answers survive.

### Phone / Voice SDK call (modes A and B)

```
Twilio ──POST /twilio/voice──► twilio_form (signature) ─► find_business (business_id param | To number)
        ◄── TwiML <Connect><Stream wss://host/voice/ws> params: business_id, caller, token=HMAC(business|CallSid)
              <Redirect /twilio/transfer>
Twilio ══WS /voice/ws══► start: check_stream_token ─► CallSession(..., 8 kHz, playback_marks) .start()
                        media ─► ulaw→PCM16 ─► session.feed      session ─► send_audio (PCM16→ulaw, 20 ms frames)
                        mark  ─► session.on_mark                  session events ─► CallFeed ─► /voice/feed watchers
                        stop / disconnect / hang_up() ─► session.close(), feed.end()
Twilio ──POST /twilio/transfer──► transfer requested? <Dial number action=/twilio/transfer-result> : <Hangup>
          └─ unanswered ─► /twilio/transfer-result ─► <Record action=/twilio/voicemail> ─► MessageForOwner
Twilio ──POST /twilio/status (final)──► ended_at (if the WS didn't close cleanly, V19) ─► summarize_conversation
```

The webhook runs while the caller hears ringing, which is also when a cold DB wakes up
(DEC-17, DEC-35). Hanging up from our side = closing the WebSocket with 1000; no TwiML
verb follows `<Connect>` except the redirect, which hangs up when no transfer was asked.

### Browser call (mode C, `/voice/browser`)

`business_id` from the query → `CallSession(..., 16 kHz, turn_detection="manual" for ptt
else "vad", continue_from)` → `start()` (unknown business → close 4404) → send
`conversation_id` event → loop: bytes to `session.feed`, `ptt_start` / `ptt_end` to
`start_turn` / `end_turn`. On exit `session.close()` and then a synchronous `_mark_ended`
in `finally`, so `ended_at` is written even if the close is cancelled at shutdown (V19).

### Live feed (`/voice/feed`, DEC-46)

`ws.py` creates a `CallFeed` per Twilio stream; nothing is published until the stream
token and business check out (`feed.start`). `CallFeed.audio_sent` turns "audio sent so
far" into `playback` on/off events (Twilio plays in real time); a `clear` (barge-in)
resets it. `LiveCalls` keeps calls in progress (≤ 1000 events each) and one bounded
queue (500) per watcher; a watcher that falls behind is emptied and sent a fresh
`snapshot`. Events only, never audio.

### Startup (`lifespan` in `app/main.py`)

1. `end_orphaned_browser_conversations()` in a thread: closes `channel='voice'`
   conversations with no `call_sid` and no `ended_at` (a killed process), `ended_at` = last
   message or start. A failure is logged, startup continues.
2. `google_auth.warm_up()`: missing Google credentials show at startup, not mid-call (V7).
3. `tts.warm_up` in a daemon thread (T4), startup does not wait for it.
4. If `BOOKING_BACKEND=google_calendar`: `calendar_sync.run_sync_loop` in a daemon thread
   (#165); stopped via an `Event` on shutdown.

Before `lifespan`, module import sets up a handler on the `app` logger at `LOG_LEVEL`
(uvicorn configures only its own loggers).

## 4. Auth and security

Stage 1 has **no user login**. Every non-Twilio route trusts the `business_id` it is
given. What is checked today (DEC-43):

| Surface | Check | Where |
|---|---|---|
| Twilio webhooks (`/twilio/voice, transfer, transfer-result, voicemail, status`) | `X-Twilio-Signature` via `RequestValidator` over `public_url(request)` + form; 403 if wrong, or if validation is on and `TWILIO_AUTH_TOKEN` is empty | `twilio_form` |
| `/voice/ws` | Custom parameter `token` must equal `HMAC-SHA256(TWILIO_AUTH_TOKEN, "business_id\|CallSid")` (constant-time compare), so only streams started by our own `/twilio/voice` run; else close 4403 | `check_stream_token` |
| `/twilio/token` | Only for a loopback client with no `X-Forwarded-For` (ngrok requests are loopback but carry it); a token can start billed calls | `twilio_token` |
| Tenant scoping in SQL | `tenant_session(business_id)` sets `app.business_id` (transaction-local, R20) **and** every query also filters by `business_id`. RLS policies are not enabled yet (stage 3), so today the filter is the real guard | `app/db/session.py` |

`TWILIO_VALIDATE_SIGNATURE=false` turns both Twilio checks off (signature and stream HMAC).
It is only for replaying requests by hand; never with a public URL (ngrok counts).

Signature correctness depends on `PUBLIC_BASE_URL` matching the URL Twilio called; when it
is empty the URL is rebuilt from `Host` and `X-Forwarded-Proto`. The stream URL
(`wss://…/voice/ws`) is derived the same way.

**Open to anyone who can reach the server** (OPEN-19, needed before the public Cloud Run
URL; per-owner scoping OPEN-12, stage 3):

- `/businesses` lists every business; `/twilio/numbers` lists every number and holder.
- `POST/DELETE …/main-number`, `PUT …/number` move phone numbers between businesses
  (cross-tenant writes through `SessionLocal`).
- `…/custom-replies` lets anyone change what the bot says for a business.
- `/debug/retrieve` returns raw chunk text; `/debug/prompt` returns the full system prompt.
- `/voice/feed` streams every business's live calls (transcripts, caller number).
- `/voice/browser` starts a call (Google STT/TTS + LLM cost) for any `business_id`.
- `…/sessions` DELETE cancels bookings and sends SMS to attendees.
- `…/conversations` returns transcripts and caller data.

No CORS middleware is installed (same-origin only from the browser's side; it doesn't stop
server-to-server calls). No rate limiting. No request size limits beyond Starlette's
defaults.

## 5. Key functions

| Function | File | Why it matters |
|---|---|---|
| `lifespan` | `app/main.py` | Startup sweep, warm-ups, calendar sync thread |
| `_prepare_conversation_and_history` | `app/api/chat.py` | Conversation ownership check before the LLM call; shared by `/chat`, `/chat/stream`, `/debug/prompt` |
| `_sse` | `app/api/chat.py` | Multi-line-safe SSE frame |
| `_reindex_custom_reply` | `app/api/custom_replies.py` | Replaces a reply's single `kind='custom_reply'` chunk with a fresh embedding in the same transaction |
| `display_name` | `app/api/businesses.py` | Profile name unless it is a placeholder; also used by `twilio_numbers.py` |
| `put_number` | `app/api/business_settings.py` | Validates E.164 + "on the account", then gives the business exactly that number and removes it from every other business |
| `_greeting_state`, `put_greeting` | `app/api/business_settings.py` | Saves the owner text in `businesses.settings`, pre-generates greeting audio, prunes the old cache; audio failure is reported, not raised |
| `list_bookings`, `open_hours` | `app/api/bookings.py` | Converts bookings to business-local minutes since midnight; optional Google sync with `stale` fallback |
| `public_url`, `stream_url` | `app/voice/twilio_routes.py` | The URL the signature covers; the `wss://` stream URL |
| `stream_token`, `check_stream_token` | `app/voice/twilio_routes.py` | HMAC that ties `/voice/ws` to `/twilio/voice` |
| `twilio_form` | `app/voice/twilio_routes.py` | Form parse + signature check, shared by every webhook |
| `find_business` | `app/voice/twilio_routes.py` | Mode B custom param, else mode A `To` ∈ `phone_numbers` (oldest business wins if duplicated) |
| `find_call` | `app/voice/twilio_routes.py` | `CallSid` → (business, conversation), across tenants |
| `twilio_media_stream` | `app/voice/ws.py` | Twilio frame loop, μ-law ↔ PCM16, 20 ms output frames, marks, hang-up |
| `browser_voice`, `end_orphaned_browser_conversations` | `app/voice/browser_ws.py` | Browser socket loop; V19 `ended_at` guarantees |
| `LiveCalls`, `CallFeed`, `sse` | `app/voice/call_feed.py` | Fan-out hub, playback tracking, keepalive SSE |

## 6. Config

Read through `get_settings()` (`app/config.py`, env vars or `.env`). The ones the API
layer itself uses:

| Env var | Used by | Effect |
|---|---|---|
| `LOG_LEVEL` | `main.py` | Level of the `app.*` loggers |
| `DEV_RELOAD` | `main.py` | Registers `/dev/reload` |
| `BOOKING_BACKEND` | `main.py` | `google_calendar` starts the calendar sync thread |
| `TWILIO_AUTH_TOKEN` | `twilio_routes.py`, `twilio_numbers.py` | Signature check, stream HMAC key, REST auth |
| `TWILIO_VALIDATE_SIGNATURE` | `twilio_routes.py` | Off = no signature and no stream HMAC check |
| `PUBLIC_BASE_URL` | `twilio_routes.py` | URL for signature checks and the stream URL |
| `TWILIO_ACCOUNT_SID`, `TWILIO_API_KEY_SID`, `TWILIO_API_KEY_SECRET`, `TWILIO_TWIML_APP_SID` | `/twilio/token`, `/twilio/numbers` | Voice SDK token; missing → 503 naming the missing vars |
| `TWILIO_PHONE_NUMBER` | `businesses.py`, `/twilio/number` | The main number; missing → 503 on main-number routes |
| `EMBED_MODEL` | `custom_replies.py` | Stored on the custom-reply chunk |

Fixed constants in code: `MAX_RANGE_DAYS=62` (bookings), `EMBED_BATCH=64` (top
questions), `TRANSFER_RING_S=25`, `VOICEMAIL_MAX_S=120`, `TOKEN_TTL_S=3600`,
`TOKEN_IDENTITY="web-tester"`, `FRAME_BYTES=160`, `BROWSER_SAMPLE_RATE=16000`,
`KEEPALIVE_S=15`, `QUEUE_SIZE=500`, `MAX_EVENTS=1000`. Everything else (LLM, STT/TTS,
DB URL, calendar) is read by the lower layers.

## 7. Error handling

No global exception handler: errors use FastAPI's default `{"detail": …}` body, and an
unexpected exception is a 500.

| Situation | Response |
|---|---|
| Bad UUID, missing query, Pydantic body error, out-of-range `limit`/`days` | 422 (FastAPI validation) |
| Bad date or range in `/bookings` | 422 with an example (`2026-11-05`) |
| Greeting too long, bad E.164, number not on the account | 422 with a message |
| Unknown business / conversation / custom reply | 404 |
| `SessionError` from the sessions action | Its own `status` + `message` (`_fail`) |
| Embedding server down in `/top-questions` | 503 "embedding server unavailable" |
| Twilio REST unreachable in `PUT /number` | 503 with `twilio_numbers.safe_error` text (no secrets) |
| Missing Twilio config (`/twilio/token`, main number) | 503 naming the env vars |
| Google Calendar unreachable on `/bookings?sync=1` | 200 with `stale: true` (mirror shown) |
| Greeting audio generation fails | Text saved, 200 with `cache_error` |
| Bad Twilio signature | 403 |
| Unknown business in `/twilio/voice` | 200 TwiML "not in service" + hang-up (Twilio needs valid TwiML) |
| Bad stream token / unknown business on a socket | WS close 4403 / 4404 |
| Malformed JSON frame on either socket | Ignored |
| Call summary fails in `/twilio/status` | Logged inside `summarize_conversation`, still 204 |
| LLM or DB error inside `/chat`, custom-reply embed, `/chat/stream` mid-stream | 500 (stream: connection just ends, no turn saved) |

## 8. Tests

`tests/test_twilio.py`, `test_voice.py`, `test_browser_ws.py`, `test_call_feed.py`,
`test_conversations.py`, `test_custom_replies.py`, `test_business_settings.py`,
`test_bookings_api.py`, `test_sessions_api.py`, `test_top_questions.py`, `test_rag.py`
(chat), `test_foundation.py` (health/startup). They use FastAPI's `TestClient` against the
dev Postgres; no CI runs them (cloud box has no LM Studio / Google / Twilio).

## 9. Known gaps

| # | Gap | Effect | Where / tracked |
|---|---|---|---|
| A1 | No auth on owner/dev routes (§4 list) | Anyone reaching the URL can read transcripts, rewrite replies, move numbers, cancel sessions | OPEN-19 (before public Cloud Run), OPEN-12 (stage 3) |
| A2 | Cross-tenant `SessionLocal` reads/writes: `/businesses`, number routes, `find_business`, `find_call`, `/twilio/status` lookup, orphan sweep | Will break or bypass RLS once policies are on | Needs an RLS-exempt path (DEC-43 consequences, stage 3) |
| A3 | Sync DB work inside `async def` handlers: `twilio_voice` (`find_business`), `twilio_voicemail` (`tenant_session`), `twilio_status` (both lookups) run on the event loop; `transfer` uses `run_in_threadpool`, these don't | A slow or cold DB stalls every live call's audio loop on the same process | Not tracked |
| A4 | `/voice/feed` state is in memory, per process | Several instances (Cloud Run scale-out) would each see only their own calls | DEC-46 (LISTEN/NOTIFY when needed) |
| A5 | `/voice/browser` has no check at all | Any page can start calls that spend Google Speech + LLM | OPEN-19 |
| A6 | Business logic in route files: `put_number`/`assign_main_number` number moves, `_reindex_custom_reply` (embedding + chunk write), `list_bookings` time maths, `get_top_questions` embedding loop | The phone/chat layers can't reuse them; tests must go through HTTP | Candidates to move to [actions.md](actions.md) / [data.md](data.md) |
| A7 | `PUT /number` checks the business in one session and writes in another, with a Twilio call between | A business deleted in between gets an UPDATE of 0 rows but a 200 | Low risk in stage 1 |
| A8 | `/twilio/status` runs the LLM summary inside the request (DEC-24, R19) | Twilio waits; its webhook timeout (~15 s) can mark the callback failed on a slow summary | DEC-24 (accepted) |
| A9 | Four API files live in `app/voice/`; `twilio_numbers.py` mixes a route with the Twilio REST client and imports `display_name` from `app/api/businesses.py` | Integrations code depends on the API layer, against the "integrations never call back" rule | Layering cleanup |
| A10 | No rate limiting, no CORS policy, no global error handler / request id | Errors are plain 500s without a correlation id in logs | Stage 2/3 |
| A11 | `/health` only checks the DB | LM Studio/Gemini, Google Speech or Twilio being down still reports ok | — |

## 10. Depends on / Used by

**Depends on**

| Layer doc | What the API layer calls |
|---|---|
| Voice layer, [voice.md](voice.md) (the conversation: call loop, answering) | `app/rag/answer.py` (`answer_question`, `stream_answer`, `prepare_prompt`), `app/voice/session.py` (`CallSession`), `app/voice/greeting_cache.py` (greeting text, audio cache) |
| Data layer, [data.md](data.md) (stored knowledge and app data) | `app/db` (`tenant_session`, `SessionLocal`, `engine`, models, `message_order`), `app/rag/retrieve.py`, `app/rag/location.py` (`default_location_id`), `app/insights/questions.py`, `app/ingest/profile.py` (`is_placeholder_name`) |
| Crawler layer, [crawler.md](crawler.md) | `app/ingest/clean.py` (`content_hash`) for the custom-reply chunk only; no other crawler calls |
| Actions layer, [actions.md](actions.md) (what the app does: book, cancel, transfer a call, text, summarize) | `app/actions/booking/` (`sessions`, `rules`, `session_events`, `internal._load_rules`, `calendar_sync`), `app/actions/summary.py`, `app/actions/transfer.py` |
| Integrations layer, [integrations.md](integrations.md) (thin clients that let the app work with outside services: Twilio, Google, LLM APIs) | `app/llm.py` (`embed`), `app/voice/google_auth.py` and `tts.py` (warm-up), `app/voice/audio.py` (μ-law ↔ PCM16), `app/voice/twilio_numbers.py` (`account_numbers`, `safe_error`), Twilio SDK (`RequestValidator`, TwiML, `AccessToken`), `booking/google_client.CalendarError` |

**Used by**

| Who | How |
|---|---|
| Web layer, [web.md](web.md) | All `/businesses/*`, `/chat*`, `/twilio/token`, `/twilio/number(s)`, `/voice/browser`, `/voice/feed`, `/dev/reload`; served from `/web` by this layer |
| Twilio (outside) | `/twilio/*` webhooks and `/voice/ws` |
| Integrations layer, [integrations.md](integrations.md) | ⚠️ `app/voice/twilio_numbers.py` imports `display_name` from `app/api/businesses.py` (gap A9) |

No other layer imports from `app/api/`; [crawler.md](crawler.md) runs as its own job and
image and has no HTTP surface here.
