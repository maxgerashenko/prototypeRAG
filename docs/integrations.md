# Integrations layer

**Integrations are what let the app work with the outside world**: Twilio and the other
external APIs (LLM endpoints, Google Speech, Google Calendar, Google Places, SMS). Each is a
thin client. The [actions](actions.md) layer is what the app *does* (book a session, make or
transfer a call, send a summary); it uses these clients to reach the outside services.

One of the 7 layers (crawler, data, voice, actions, integrations, api, web); see the
sibling files in `docs/`.

Written from the code on `main` at `75d3bfd` (2026-11-02). File paths and line numbers are
as of that commit.

## 1. Purpose and boundaries

**Purpose.** Hide each outside service's wire format, auth and quirks behind a small Python
surface, so the rest of the app calls `embed()`, `synthesize()` or `insert_event()` and does
not know whether LM Studio or Gemini, a token file or a service account, sits behind it.
Swapping local for cloud is a change of env vars only (DEC-05, DEC-20).

**Rule: other layers call integrations; integrations never call back.** An integration
module may import `app.config` (settings) and its own SDK, and nothing else from `app/`.
It holds no business rules, does not read or write the app's tables, and does not define
HTTP routes.

**In the layer** (by role, not by folder: the files are spread over four folders today):

| Service | File(s) | Folder today |
|---|---|---|
| LLM chat + embeddings (LM Studio / Gemini / Anthropic) | `app/llm.py` | `app/` |
| Google Speech-to-Text | `app/voice/stt.py` | `app/voice/` |
| Google Text-to-Speech | `app/voice/tts.py` | `app/voice/` |
| Google credentials check (ADC) | `app/voice/google_auth.py` | `app/voice/` |
| Twilio REST (account numbers) | `app/voice/twilio_numbers.py` (`account_numbers`, `safe_error`) | `app/voice/` |
| Twilio webhooks, tokens, TwiML | none yet, see V-3 (`app/voice/twilio_routes.py` is the api layer) | — |
| Google Calendar REST | `app/actions/booking/google_client.py` | `app/actions/booking/` |
| Google Places (New) | `app/ingest/places.py` | `app/ingest/` |
| SMS (mock) | `app/actions/sms.py` | `app/actions/` |
| Twilio Voice SDK (browser) | `@twilio/voice-sdk` in `web/src/call/CallApp.tsx` | `web/` (see [web.md](web.md)) |

**Not in the layer**, though they touch an outside service:

- `app/ingest/fetch.py` (httpx/Playwright page fetching): the crawler's own job, see [crawler.md](crawler.md).
- `app/actions/booking/google_calendar.py`, `calendar_sync.py`, `session_events.py`, `mirror.py`:
  booking rules, DB mirror and sync loop built *on* the calendar client; see [actions.md](actions.md).
- `app/voice/ws.py`, `browser_ws.py`, `call_feed.py`: socket endpoints; see [api.md](api.md).
  `app/voice/audio.py` (μ-law ↔ PCM16, resampling) is a pure codec helper with no outside
  call; it is used by the Twilio socket and the voice session.
- Postgres (`app/db/`): app data, part of [data.md](data.md), not an outside service here.

## 2. One section per outside service

### 2.1 LLM: LM Studio / Gemini / Anthropic (`app/llm.py`)

**What it offers.**

| Function | Used for |
|---|---|
| `get_chat_client()` (sync `OpenAI`) | RAG chat answers (`rag/answer.py`), `chat()` / `chat_json()` |
| `get_async_chat_client()` (`AsyncOpenAI`) | the voice call loop: an async stream can be cancelled on barge-in |
| `get_embed_client()`, `embed(texts)` | chunk and query embeddings; checks the vector length against `EMBED_DIM` |
| `get_anthropic_client()` | Claude through Anthropic's OpenAI-compatible endpoint, fact extraction's "haiku" choice (D21-C) |
| `chat(messages)` | one plain answer; `None` content becomes `""` |
| `chat_json(messages, schema, ...)` | structured extraction via `response_format=json_schema`, or `prompted=True` (schema in the system message, first JSON object cut out of the answer) for thinking models and Anthropic |
| `instrument(client)` | wraps `create` on every client so each call logs one INFO line (model, sizes, tokens, ttft, total ms); DEBUG adds prompt and response |

All clients are `lru_cache`d singletons; one `openai` package for every provider (DEC-05),
no provider classes (DEC-20).

**Env settings.** `LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL` (default `google/gemma-4-12b`,
DEC-32); `EMBED_BASE_URL`, `EMBED_API_KEY`, `EMBED_MODEL`, `EMBED_DIM` (768);
`ANTHROPIC_API_KEY`, `ANTHROPIC_BASE_URL`; the facts settings (`FACTS_LLM_MODEL`,
`FACTS_MODEL_CHOICE`, `FACTS_HAIKU_MODEL`, `FACTS_QWEN_MODEL`, `FACTS_MAX_TOKENS`,
`FACTS_THINKING_MAX_TOKENS`, `FACTS_PARALLEL`); `VOICE_REASONING_EFFORT`,
`CHAT_REASONING_EFFORT` (passed by the callers, not by `llm.py`); `LOG_LEVEL`.

**Failure modes.**

| Failure | What happens |
|---|---|
| LM Studio not running / model not loaded | `openai.APIConnectionError` / 404 from the SDK, after its default 2 retries; the caller decides (chat returns an error, voice says a fallback line) |
| Embedding model returns another size than `EMBED_DIM` (e.g. a cloud default) | `ValueError` from `embed()` with both sizes, instead of a pgvector error later |
| Thinking model spends its output on reasoning | `chat()` returns `""`; `chat_json()` falls back to `reasoning_content` |
| Thinking model under a JSON grammar answers an empty list (P17) | callers pass `prompted=True` |
| Model loops inside the JSON | `max_tokens` / `timeout` bound it; with a `timeout` there is no retry |
| No JSON object in a prompted answer | `ValueError` from `_first_json_object` |
| `ANTHROPIC_API_KEY` unset when the haiku choice is picked | `LookupError` |
| Odd response shape | logging never fails the call (`_Call.result` catches it) |

**Tests here:** `tests/test_llm.py` (call logging, JSON cut-out), `tests/test_embed_dim.py`.
**Mac only:** anything that needs a live model (LM Studio runs only on the 64 GB Mac);
Anthropic calls need the key, which the cloud box does not have.

### 2.2 Google Speech-to-Text (`app/voice/stt.py`)

**What it offers.** `TurnTranscriber(sample_rate)`: one streaming recognition per caller
turn. The voice session's VAD starts it on speech start, `push(pcm)` feeds PCM16 while the
caller speaks, `finish()` returns the final text, `cancel()` drops the turn. A background
thread reads the gRPC stream. `alternative_language_codes()` parses the extra languages.
Native `google-cloud-speech` v1 SDK (DEC-13: native Google SDKs only in the voice module).

**Env settings.** `VOICE_LANGUAGE` (`en-US`), `STT_MODEL` (`latest_short`; `phone_call` is
the 8 kHz option), `STT_ALTERNATIVE_LANGUAGES` (comma list, at most 3 kept). Auth is ADC,
no key setting.

**Failure modes.**

| Failure | What happens |
|---|---|
| No ADC | `require_credentials()` raises at once with the `gcloud auth application-default login` hint (V7) |
| Stream error inside the thread | stored, re-raised from `finish()` as `RuntimeError("speech-to-text failed")` |
| Final result not in after 2 s (`FINISH_TIMEOUT_S`) | logs a warning and returns what arrived, including the last interim text |
| More than 3 alternative languages | extra ones dropped silently |

**Tests here:** `tests/test_voice.py`, `tests/test_voice_session.py` (with fakes).
**Mac only:** real recognition quality, timing (final result 90–180 ms after the audio,
live check 2026-10-03), and the alternative-language detection; needs Google ADC.

### 2.3 Google Text-to-Speech (`app/voice/tts.py`)

**What it offers.** `synthesize(text, sample_rate) -> bytes` (raw mono PCM16, WAV header
parsed off), `warm_up()` (one short request at startup, T4), `pcm16_duration_s()`.
Native `google-cloud-texttospeech` SDK, ADC auth.

**Env settings.** `VOICE_LANGUAGE`, `TTS_VOICE` (`en-US-Neural2-F`).

**Failure modes.**

| Failure | What happens |
|---|---|
| Empty text | returns `b""`, no request |
| No ADC | `RuntimeError` from `require_credentials()` |
| Response not mono 16-bit, or other sample rate than asked | `ValueError` |
| Warm-up fails (tests, CI, no creds) | logged; only the first greeting is slower (≈700 ms vs ≈250 ms warm) |

**Tests here:** `tests/test_voice.py`. **Mac only:** audio quality and latency, the
pre-generated greeting audio (V6, #74); needs ADC.

### 2.4 Google credentials (`app/voice/google_auth.py`)

**What it offers.** `require_credentials()` and `warm_up()`: one `google.auth.default()`
check per process, cached either way, so a missing ADC fails fast instead of paying the
~3 s GCE metadata probe on every turn (V7). `warm_up()` runs it in a thread at startup and
only logs.

**Failure modes.** The result is cached for the life of the process: after
`gcloud auth application-default login` the server must be restarted (the error text says
so). It covers STT/TTS only; the calendar client has its own auth (§2.6).

**Tests here:** `tests/test_google_auth.py`.

### 2.5 Twilio (`app/voice/twilio_numbers.py`, `app/voice/twilio_routes.py`, web Voice SDK)

**What it offers.** Only `twilio_numbers.py` is an integration file today; the rows from
`twilio_routes.py`, `ws.py` and `web/` are listed so the Twilio wire format is in one place,
but that code belongs to [api.md](api.md) and [web.md](web.md) until V-3 is done.

| Piece | Where | Does |
|---|---|---|
| `account_numbers()` | `twilio_numbers.py` | Twilio REST `incoming_phone_numbers.list(limit=100)` → number, name, voice/sms capability, `routed` (voice URL ends `/twilio/voice`) |
| `safe_error(exc)` | `twilio_numbers.py` | short message for the browser; `str(TwilioRestException)` carries the Account SID |
| `twilio_form()` | `twilio_routes.py` | `RequestValidator` check of `X-Twilio-Signature` against `public_url(request)` (DEC-43, OPEN-19) |
| `stream_token()` / `check_stream_token()` | `twilio_routes.py` | HMAC (auth token) over business id + call SID, passed as a Media Stream custom parameter |
| `AccessToken` + `VoiceGrant` | `twilio_routes.py`, `GET /twilio/token` | Voice SDK token for `web/call.html` (mode B, DEC-34); answers on loopback only |
| TwiML (`VoiceResponse`, `Connect`, `<Redirect>`, `<Dial>`, `<Record>`) | `twilio_routes.py` | answer, transfer (`app/actions/transfer.py` decides, no REST call), voicemail |
| Media Streams protocol | `app/voice/ws.py` + `audio.py` | JSON frames, base64 8 kHz μ-law, `clear` on barge-in |
| Voice SDK `Device` / `Call` | `web/src/call/CallApp.tsx` | browser test calls without a phone number (DEC-34) |

**Env settings.** `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN` (signatures + stream token),
`TWILIO_API_KEY_SID`, `TWILIO_API_KEY_SECRET` (Voice SDK tokens), `TWILIO_TWIML_APP_SID`,
`TWILIO_PHONE_NUMBER`, `PUBLIC_BASE_URL` (ngrok URL locally, Cloud Run URL later),
`TWILIO_VALIDATE_SIGNATURE` (never off when reachable from the internet).

**Failure modes.**

| Failure | What happens |
|---|---|
| `PUBLIC_BASE_URL` differs from the URL Twilio called (e.g. ngrok restarted) | every webhook 403 "bad Twilio signature", logged with a hint |
| `TWILIO_AUTH_TOKEN` unset with validation on | webhooks 403; stream tokens refused |
| Forged or missing stream token | `/voice/ws` closes the socket with 4403 |
| SID / token unset when listing numbers | `RuntimeError`; `GET /twilio/numbers` returns `{"numbers": [], "error": ...}`; `PUT .../number` returns 503 |
| Account has more than 100 numbers | the rest are not listed (fine for the demo) |
| Unknown `To` / `business_id` on `/twilio/voice` | logged; the caller hears "Sorry, this number is not in service." and the call hangs up |

**Tests here:** `tests/test_twilio.py` (signature, stream token, TwiML, numbers with a fake),
`tests/test_transfer.py`, `tests/test_business_settings.py`.
**Mac only:** everything live: a phone or Voice SDK call through ngrok, transfer `<Dial>`,
voicemail recording, the number picker against the real account. The cloud box has no
Twilio credentials; these checks sit in the "Test on Mac" list of
[03-voice-channel.md](../plan/03-voice-channel.md).

### 2.6 Google Calendar (`app/actions/booking/google_client.py`)

**What it offers.** Two protocols so the booking code is tested against a fake and only
this file knows Google's wire format (DEC-48):

- `CalendarClient`: `busy()` (`freeBusy`), `insert_event()`, `delete_event()`.
- `CalendarEventsClient`: `list_events()` (singleEvents, showDeleted, syncToken paging),
  `get_event()`, `patch_event()` (with `If-Match` etag).

`GoogleCalendarClient` implements both over `google.auth` `AuthorizedSession` REST calls,
timeout 10 s. Auth: an OAuth authorized-user token file from
`scripts/google_calendar_login.py` (preferred; the calendar needs no sharing), else a
service account key (the owner shares the calendar with its email). The token file wins.
Credentials load on first use. `client_from_settings()` builds the app's instance.

**Env settings.** `BOOKING_BACKEND` (`internal` | `google_calendar`), `GOOGLE_CALENDAR_ID`
(per business override in `businesses.settings.google_calendar_id`, read by the actions
layer), `GOOGLE_CALENDAR_TOKEN_FILE`, `GOOGLE_CALENDAR_CREDENTIALS_FILE`. Used by the
actions layer, not the client: `CALENDAR_SYNC_INTERVAL_SECONDS`, `APP_BASE_URL`,
`GOOGLE_CALENDAR_OWNER_BUSINESS_ID`.

**Failure modes.** Everything becomes a `CalendarError` (message for logs, never for
callers), with three subclasses the callers act on:

| Error | Cause | Caller does |
|---|---|---|
| `SyncTokenExpired` | 410 on `events.list` | drops the token, runs a full sync |
| `PreconditionFailed` | 412 on `events.patch` | re-reads the event |
| `EventExists` | 409 on `events.insert` with our own id | treats it as success (an earlier try got through) |
| `CalendarError` | network, token refresh, bad token/key file, `freeBusy` `notFound` (calendar not shared), any other status | books nothing, says "calendar can't be reached" |

Known limit: a service account on a Gmail calendar cannot add attendees (403
`forbiddenForServiceAccounts`), so nobody is invited; the caller is the conversation.
`busy()` (`freeBusy`) has no caller in `app/` any more: availability now reads the
`calendar_events` mirror (#165). It stays in the protocol; drop it or keep it on purpose.

**Tests here:** `tests/test_google_calendar.py`, `tests/test_calendar_sync.py`,
`tests/test_session_events.py` (fakes). **Mac only:** the one-time OAuth consent
(`scripts/google_calendar_login.py`, needs a browser) and any call against the real
shared demo calendar (#163).

### 2.7 Google Places (`app/ingest/places.py`)

**What it offers.** `PlacesClient(api_key)`: `find_place_id(text)` (`places:searchText`)
and `get_place(place_id)` with a fixed field mask; `place_to_live_fields()` maps the answer
for display. httpx, timeout 10 s. Terms: only `place_id` may be stored; everything else is a
live lookup, and reviews/ratings are never requested (DEC-11, R9).

**Env settings.** None. There is no Places key in `app/config.py`; the key is a constructor
argument.

**Failure modes.** Any HTTP error or non-200 returns `None`, silently (no log line). A
caller cannot tell "not found" from "key wrong" or "quota".

**State.** Not wired: no module in `app/` imports it; only `tests/test_places.py` uses it.
DEC-11 is still 🔄 (OPEN-04).
**Mac only:** a real lookup needs an API key on a billing-enabled Google project.

### 2.8 SMS (`app/actions/sms.py`)

**What it offers.** `send_sms(to, body)`: a **mock**. It logs `MOCK SMS to ...` and appends
to the in-memory `OUTBOX` (last 200). Twilio is never called. The real sender is suggestion
issue #138 (SMS confirmation was dropped from #52 on 2026-10-28), with the same signature.

**Used by.** `app/actions/booking/sessions.py` (`_text_callers`: tells callers a session was
cancelled). Booking confirmations do not text; the model reads the reference back.

**Failure modes.** Cannot fail. `OUTBOX` is lost on restart and is per process, so with more
than one instance (stage 3) it shows only that instance's messages.
**Env settings:** none yet (#138 would reuse the Twilio SID/token and a sender number).

## 3. Settings at a glance

All settings come from `app/config.py` (`Settings`, env vars or `.env`). Integrations read
`get_settings()` themselves; nothing passes settings in, except `PlacesClient` (key argument)
and `GoogleCalendarClient` (file paths, filled by `client_from_settings()`).

| Service | Auth | Keys in env? | Local vs cloud |
|---|---|---|---|
| LLM | API key header (dummy for LM Studio) | `LLM_API_KEY`, `EMBED_API_KEY`, `ANTHROPIC_API_KEY` | base URL + model change (DEC-05) |
| STT / TTS | ADC | no | `gcloud auth application-default login` locally; service identity on Cloud Run |
| Twilio | SID + auth token; API key + secret | yes | `PUBLIC_BASE_URL` = ngrok → Cloud Run URL |
| Calendar | OAuth token file or SA key file | file paths only | files outside the repo; Secret Manager in stage 3 |
| Places | API key | not in `Settings` yet | not wired |
| SMS | none (mock) | no | — |

## 4. Where the "never calls back" rule is broken today

| # | Where | What | Suggested fix |
|---|---|---|---|
| V-1 | `app/voice/twilio_numbers.py:13-16` | The Twilio client file imports `app.api.businesses.display_name` (api layer), `app.db.models` and `SessionLocal` (data), and `holders()` reads `businesses` across tenants | Keep `account_numbers()` and `safe_error()` here; move `holders()` and the join into `app/api/` |
| V-2 | `app/voice/twilio_numbers.py:19,73` | It defines a FastAPI router and the `GET /twilio/numbers` route, included by `app/main.py` | Move the route to `app/api/` (or `twilio_routes.py`), next to the other Twilio routes |
| V-3 | `app/voice/twilio_routes.py` | No thin Twilio client: signature validation, stream-token HMAC, access tokens and TwiML building sit inside the route file, mixed with DB lookups and calls into actions (`summary`, `transfer`). Not a call *back* (the routes are api layer), but the Twilio wire format leaks into the api layer | Extract a `twilio_client` module (validate, sign, token, TwiML helpers) that imports only config + `twilio` |
| V-4 | `app/actions/booking/google_client.py:200` | `client_from_settings()` imports `app.config` lazily; allowed, but the client file lives inside the actions package, so the layer boundary is only by convention | No code change needed; move the file next to the other clients when the layer gets its own folder |
| V-5 | `app/ingest/places.py`, `app/actions/sms.py`, `app/voice/stt.py`, `tts.py`, `google_auth.py` | Clean (import only config, their SDK and each other), but they sit inside the folders of the layers that use them | Optional: one `app/integrations/` package; a move only, no behaviour change |

`app/llm.py` is clean: it imports only `app.config` and `openai`. No integration module
imports `app.voice.session`, `app.rag`, `app.ingest` (other than its own file) or `app.actions`.

Checked with: `grep -n "^from app\|^import app\|^\s*from app" <file>` on every file in §1's table.

## 5. Depends on / Used by

**Depends on:** `app/config.py` (settings) only, plus the SDKs (`openai`,
`google-cloud-speech`, `google-cloud-texttospeech`, `google-auth`, `twilio`, `httpx`).
Exceptions listed in §4 (V-1, V-2: [data.md](data.md) for `app/db`, [api.md](api.md)
for `display_name`).

**Used by:**

| Layer (doc) | Calls |
|---|---|
| [data.md](data.md) | `embed()` in `rag/index.py`, `rag/retrieve.py`; `chat_json()` in `ingest/profile.py`, `summary.py`, `timezone.py`, `facts.py` (+ `get_anthropic_client()`); `PlacesClient` is meant for the identity/organize step but is not wired yet |
| [voice.md](voice.md) | `get_chat_client()` in `rag/answer.py`; `get_async_chat_client()`, `TurnTranscriber`, `tts.synthesize` in `voice/session.py`; `tts` in `voice/greeting_cache.py` |
| [actions.md](actions.md) | `chat_json()` in `actions/summary.py`; calendar client in `booking/google_calendar.py`, `calendar_sync.py`, `session_events.py`, `tools.py`, `sessions.py`; `send_sms()` in `booking/sessions.py` |
| [api.md](api.md) | `embed()` in `api/business_settings.py`, `api/custom_replies.py`; `twilio_numbers.account_numbers()` / `safe_error()` in `api/business_settings.py`; `CalendarError` in `api/bookings.py`; `google_auth.warm_up()` and `tts.warm_up()` in `app/main.py` lifespan; Twilio SDK in `voice/twilio_routes.py` (V-3) |
| [web.md](web.md) | no Python calls; uses the Twilio Voice SDK directly in `web/src/call/CallApp.tsx` with a token from `GET /twilio/token` |
| [crawler.md](crawler.md) | none (page fetching uses its own httpx/Playwright in `ingest/fetch.py`) |

Cross-reference check for the coordinator: every row above should appear, mirrored, in the
"Depends on" section of the named doc.

## 6. Open points

- `PlacesClient` is unwired and has no key setting; decide with OPEN-04 whether stage 1 needs it.
- `CalendarClient.busy()` (`freeBusy`) is unused since the mirror (#165).
- SMS is a mock until #138; the real sender needs a Twilio number that can send SMS (DEC-34:
  no number until a business pays).
- Fix V-1 / V-2 (and optionally V-3) before stage 3's RLS work: `holders()` reads across
  tenants and needs the RLS-exempt path noted in its own docstring.
