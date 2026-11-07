# Layer docs

How the code on `main` is built today, one file per layer. These describe the code, not the
plans; the plans are in [`plan/`](../plan/) (see the [root README](../README.md)). Where a
file sits in a folder of one layer but does the job of another, the docs follow the job
(by role, not by folder) and say so.

| # | Layer | Doc | What it is |
|---|---|---|---|
| 1 | Crawler | [crawler.md](crawler.md) | Gets a business's website: discover, fetch, clean, store raw pages |
| 2 | Data | [data.md](data.md) | Organizes what was crawled and serves it back: organize, chunks, embeddings, facts, profile, summary, retrieval, DB schema, insights |
| 3 | Voice | [voice.md](voice.md) | The conversation, by phone, browser or chat: turns, VAD, barge-in, greeting, prompts, the LLM tool loop |
| 4 | Actions | [actions.md](actions.md) | What the app does: book, join a group session, make a call (transfer to a person), take a message, write the call summary |
| 5 | Integrations | [integrations.md](integrations.md) | What lets the app work with outside services: thin clients for Twilio, Google Speech, Google Calendar, Google Places, the LLM endpoint, SMS. Called by other layers, never calls back |
| 6 | API | [api.md](api.md) | The HTTP, WebSocket and SSE edge: REST routes, Twilio webhooks, voice sockets, live call feed, app startup |
| 7 | Web | [web.md](web.md) | The front end: React + TypeScript voice app and debug pages in `web/`, design spec in `design/` |

Cross-layer topic: [bookings.md](bookings.md) — how a booking conversation runs, who owns the data, what extra metadata is saved, how it is tested (`tests/test_no_invented_sessions.py`: unknown sessions are refused, the bot cannot create a session; `tests/test_session_offer.py`: a refusal is followed by the real open sessions, an end time gets its AM/PM).

`app/config.py` (settings), `tests/`, `scripts/` and the plan files are shared and belong to no layer.

## Who calls whom

```
  people ──▶ Web ──HTTP / WebSocket / SSE──▶ API ◀── Twilio (webhooks, media stream)
                                              │
                   ┌──────────────────────────┼──────────────────┐
                   ▼                          ▼                  ▼
                 Voice ─────────────────▶  Actions               Data ◀──── Crawler (CLI only)
                   │ └──── retrieve ───────────┼─────────────▶    ▲
                   │                           │                  │
                   ▼                           ▼                  ▼
                 ──────────────────── Integrations ────────────────────
                                              │
                                              ▼
                     LLM endpoint, Google STT / TTS / Calendar / Places, Twilio, SMS
```

The crawler also calls the Voice layer once, at the end of a crawl, to pre-generate the
greeting audio. Crawled pages reach the website only through the crawler's own `httpx`
fetcher, not through Integrations.

Arrows point from caller to callee. Every layer may use `app/db` (Data layer) for its own
tables. The rule for the bottom row: other layers call integrations, integrations never call
back. Where the code breaks that rule today is listed in
[integrations.md §4](integrations.md#4-where-the-never-calls-back-rule-is-broken-today).

## Cross-reference check

Run on 2026-11-02 against `main` at `7fdf806`, over all seven docs. What was checked:

- every "Depends on" row in one doc has a matching "Used by" row in the other, and back;
- every backticked file path and function, class or setting name exists in the code;
- every file under `app/` and `web/` is claimed by exactly one layer (or one part of a file
  by one layer, where a file is split);
- the "integrations never call back" rule and its violations are described the same way
  everywhere;
- the layers are named the same way everywhere (Crawler, Data, Voice, Actions, Integrations,
  API, Web).

**Result: all checks pass** after the fixes below, made in the layer doc branches before
merge. Name check: every path, function, class and setting named in the docs exists on
`main`, except names the docs mark as proposals (`app/integrations/`, `twilio_client`) or
outside the code (`web/dist`, a build output; `forbiddenForServiceAccounts`, a Google error
code).

| Doc | Found | Fixed |
|---|---|---|
| data.md, crawler.md | `app/ingest/store.py` claimed whole by Data; Crawler claims `upsert_page` and `delete_pages_not_in` | Data now claims only `replace_chunks` |
| data.md | Depends on Voice missed `app/rag/eval.py` → `answer_question` (Voice listed it) | Added |
| data.md | Used by missed Integrations' rule violation V-1 (`twilio_numbers.py` reads `app/db`) | Added |
| data.md, actions.md | `app/config.py` listed as Integrations in one doc, shared in another | Shared, not a layer, in both |
| api.md, integrations.md, voice.md | `app/voice/audio.py` codec listed as Integrations by API; Integrations said it is not one | Codec part is API (transport adapters), `working_sound` is Voice |
| api.md | Gap A9 and Integrations' V-1/V-2 described the same problem without pointing at each other | Linked |
| actions.md, integrations.md | `app/actions/sms.py` claimed by both | Integration living under `actions/`, like `google_client.py` |
| actions.md | Table `business_profiles` (is `business_profile`); `run_tool` said to be in Actions (it is in `app/voice/tools.py`) | Fixed |
| integrations.md, api.md | Parts of `app/voice/twilio_routes.py` claimed by Integrations; API claims the file | Integrations: "none yet, see V-3" |
| crawler.md | Depends on Data missed `organize.chrome_pseudo_url` (Data listed it) | Added |
| web.md | Title "Layer 7 — `web`" and a link to a file outside the repo | "Web layer", link to this index |

The "integrations never call back" rule reads the same in all seven docs. Its only
violations are in `app/voice/twilio_numbers.py` (V-1, V-2 in
[integrations.md](integrations.md), A9 in [api.md](api.md)); V-3 to V-5 are layering
leftovers, not calls back.

### Files split between layers

These files hold parts of two layers. Each part is claimed once.

| File | Parts |
|---|---|
| `app/ingest/run.py` | Crawl half (CLI, fetch loop, delete safety): Crawler. After the fetch loop it orchestrates Data-layer steps and the Voice greeting cache |
| `app/ingest/store.py` | `upsert_page`, `delete_pages_not_in`: Crawler. `replace_chunks`: Data |
| `app/rag/location.py` | Data (reads `locations`); `format_locations` writes a prompt block used by Voice |
| `app/voice/audio.py` | `working_sound`: Voice. μ-law ↔ PCM16 codec helpers: API (transport adapters) |
| `app/voice/twilio_numbers.py` | `account_numbers`, `safe_error`: Integrations. `GET /twilio/numbers` route and `holders()`: API (rule violations V-1, V-2) |
| `app/voice/twilio_routes.py` | API. Holds the Twilio wire format too, since there is no thin Twilio client yet (V-3) |

### Files in one folder, another layer

| File | Folder | Layer |
|---|---|---|
| `app/voice/ws.py`, `browser_ws.py`, `call_feed.py`, `twilio_routes.py` | `app/voice/` | API |
| `app/voice/stt.py`, `tts.py`, `google_auth.py` | `app/voice/` | Integrations |
| `app/actions/booking/google_client.py`, `app/actions/sms.py` | `app/actions/` | Integrations |
| `app/ingest/places.py` | `app/ingest/` | Integrations |
| `app/rag/answer.py`, `prompt.py` | `app/rag/` | Voice |
| `app/voice/tools.py` | `app/voice/` | Voice (also serves chat) |

### Differences from the first layer map

The first layer map (agreed 2026-11-02, before the docs were written) put
`app/actions/booking/google_calendar.py` under Integrations; the docs put it in Actions,
because it holds the booking rules for the Google backend and only calls the thin client in
`google_client.py`. It also named layers 2 and 3 "knowledge" and "conversation"; they are now
Data and Voice.
