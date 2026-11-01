# Part 4 — Actions (booking, calendar, summaries)

**Goal:** the assistant doesn't only answer — it books tables, makes appointments, takes
messages, and gives the business owner a summary of every conversation.

**Done when:** in one phone call, a caller books an appointment (the stage-1 pilot is a
spa, DEC-31 — table booking uses the same tools); the booking appears in the owner
dashboard (or Google Calendar), the caller gets an SMS confirmation, and the call has a
saved summary.

---

## How actions work: LLM tool calling

The LLM (local via LM Studio, or Gemini in cloud) gets a list of tools with JSON schemas.
When the caller asks to book, the model returns a tool call; our code executes it and
gives the result back to the model, which then speaks the answer.

```
caller: "Table for 4 tomorrow at 7?"
  LLM ─► check_availability(date=..., time=19:00, party_size=4)
  code ─► {available: false, alternatives: ["18:30", "20:00"]}
  LLM ─► "7 is full — I have 6:30 or 8. Which works?"
caller: "8 please, name is Anna"
  LLM ─► confirms details back to the caller
caller: "yes"
  LLM ─► create_booking(date=..., time=20:00, party_size=4, name="Anna", phone=<caller id>)
  code ─► booking saved, SMS sent
```

## Tools

| Tool | Parameters | Used by |
|---|---|---|
| `check_availability` | date, time, party_size / service, duration | restaurants, appointments |
| `create_booking` | date, time, party_size, name, phone, notes | restaurants |
| `create_appointment` | service, date, time, name, phone | salons, clinics, services |
| `cancel_or_change_booking` | phone, booking reference, new time | both |
| `take_message` | name, phone, message, urgency | fallback for anything |
| `transfer_to_human` | reason | voice channel (Twilio `<Dial>`) |
| `send_sms` | text | confirmations (internal, called after bookings) |

Rules enforced **in code**, not only in the prompt:
- Always read back details and get explicit "yes" before `create_*` (confirm step flag).
- Validate against opening hours from the business profile.
- Caller phone comes from caller ID, not from what the model guesses.
- Which tools are enabled is a per-business setting.

## Booking backends (pluggable)

```python
class BookingBackend(Protocol):
    def availability(self, business_id, request) -> list[Slot]: ...
    def create(self, business_id, booking) -> BookingResult: ...
    def cancel(self, business_id, ref) -> None: ...
```

| Backend | When | Notes |
|---|---|---|
| **Internal** (Postgres `bookings` table + capacity rules) | First — no external dependency | Tables/slots per time window, max party size |
| **Google Calendar API** (DEC-48) | Pilot, right after the internal backend | Free/busy query + create event; pilot uses a service account with the calendar shared to it, per-business OAuth in stage 3 |
| Cal.com / Calendly | Businesses using them | Cal.com is open-source with a good API |
| OpenTable / Resy etc. | Later | Partner APIs, access is restricted |

## Conversation summaries

At the end of each call/chat — run **inside a request** (Twilio status callback for calls,
end-of-chat request for chat), not as fire-and-forget background work, because Cloud Run
throttles CPU after a response is sent (DEC-24, R19):
- LLM generates: short summary, caller intent, outcome (`info_request | booked | message | transferred | unresolved`; the summary is one standardized line of at most 25 words with every concrete detail (service, time, name, number, unanswered question)),
  extracted contact details, follow-up needed (yes/no), sentiment.
- Stored in `conversations` table with full transcript.
- Owner notification: email/SMS/Telegram for bookings, messages, and unresolved calls.
- Unresolved questions are collected → owner can add a **custom reply** (Part 2) so the
  bot knows the answer next time. This is the main loop for improving quality.

## Owner dashboard (minimal web UI)

- Conversations list: date, channel, summary, outcome; open for transcript (recording only if enabled).
- Bookings/appointments list + calendar view.
- Unanswered questions → "add answer" (creates a custom reply).
- Knowledge base: pages scraped, last crawl, "re-crawl" button.
- Settings: greeting, enabled tools, transfer number, booking rules, calendar connection.

Built in the React + TypeScript web app (`web/`, DEC-41 — supersedes the earlier Jinja + HTMX plan, DEC-19),
calling JSON API routes, served by the same FastAPI service from the built `web/dist`.
Stages 1–2: minimal dashboard without login (local / single pilot, access restricted).
Stage 3: login (Google sign-in or magic link — OPEN-12) and onboarding flow before the
second business ([06-scale.md](06-scale.md)).

## Data model additions

- `bookings` — id, business_id, conversation_id, type, start, end, party_size/service,
  name, phone, status, external_ref
- `booking_rules` — business_id, capacity per slot, slot length, lead time, max party
- `messages_for_owner` — id, business_id, conversation_id, text, status
- `conversations` — + summary, outcome, intent, follow_up, recording_url
- `action_log` — business_id, conversation_id, tool, arguments, result, timestamp (debugging + audit)
- `calendar_connections` — business_id, provider, encrypted refresh token (key in Secret Manager, R14)

All tables carry `business_id` and are covered by Row-Level Security (R18).

## Code layout

```
app/actions/
  registry.py        tool schemas + per-business enabled tools
  executor.py        runs tool calls, confirmation guard, logging
  booking/           backend interface, internal.py, google_calendar.py
  messages.py        take_message, owner notifications
  sms.py             Twilio SMS
  summary.py         post-conversation summary job
app/api/dashboard.py  JSON routes for the dashboard screens
web/src/dashboard/   dashboard screens (React, DEC-41)
```

## Tasks

- [x] Tool registry + executor with confirmation guard and action log (#42; `action_log` for side-effect calls only, per-business enabled-tools setting comes with the first booking tool)
- [x] Tool-calling loop in the RAG pipeline (#51): text chat (`POST /chat`, `GET /chat/stream`) runs LLM → tools → LLM through the executor, max 3 rounds like voice, `conversation_id` passed so `action_log` rows link to the chat; voice already had the same loop. Unit tests use a fake LLM; live check with a real model on the Mac (see 03 "Test on Mac")
- [x] `take_message` and `transfer_to_human` (#50): messages saved to `messages_for_owner` (migration 0011; owner notification and dashboard list not done); transfer number is `businesses.settings.transfer_number` (no settings screen yet). Live check under 03 "Test on Mac"
- [x] Internal booking backend + rules (#52): `actions/booking/` (`rules.py` pure rules: hours per weekday, capacity, lead time, max party, service durations, alternatives; `internal.py` backend with a per-business advisory lock so the capacity check and insert can't race; `tools.py` registers `check_availability`, `create_booking`, `cancel_booking`), tables `booking_rules`, `bookings` (migration 0011). Tested with unit tests and a Postgres without pgvector (concurrent creates book exactly one). **Not wired into the live call yet**: `app/voice/tools.py` doesn't import `booking.tools`, and the per-business enabled-tools setting comes with that wiring ("Tool-calling loop" task). Owners set `booking_rules` by SQL until the dashboard exists; no row = 60-minute slots, one booking at a time, no hours check (the profile's `opening_hours` is free text, so code can't enforce it)
- [x] Google Calendar booking backend (#105, DEC-48): `actions/booking/google_calendar.py` (`GoogleCalendarBackend`) + `google_client.py` (REST client behind a `CalendarClient` protocol; service-account key, no OAuth). Availability = the same `rules.py` rules + the day's `bookings` rows + `freeBusy` (the owner's own calendar entries block slots); `create` takes the advisory lock, re-checks `freeBusy`, then `events.insert`, and stores the event id in `bookings.external_ref` (event deleted again if the row can't be written; a Google failure books nothing); `cancel` deletes the event by `external_ref` first and keeps the booking confirmed if Google fails. Event: name, phone, service, notes in the description; `business_id`, `conversation_id`, `booking_id` as private extended properties; nobody invited. `BOOKING_BACKEND=google_calendar` + `GOOGLE_CALENDAR_ID` (or `businesses.settings.google_calendar_id`) + `GOOGLE_CALENDAR_CREDENTIALS_FILE` select it in `tools.make_backend()`. Tested against a fake Google client and a fake HTTP session (`tests/test_google_calendar.py`). Limits: `freeBusy` merges overlapping events, so with capacity > 1 only `bookings` rows are counted; no change/reschedule tool exists yet (only cancel). **Live check with a real service account: "Test on Mac" in [03-voice-channel.md](03-voice-channel.md)**
- [ ] ~~SMS confirmation~~ — dropped from #52 (2026-10-28, Max); kept as suggestion issue #138 outside the milestones. The code was written and removed again; no SMS is sent, the model reads the booking reference back to the caller
- [x] Post-conversation summary job (#43): `actions/summary.py`, run from `POST /twilio/status`; stores a one-line summary, intent, outcome (info_request, booked, message, transferred, unresolved), follow_up on `conversations` (migration 0006). Sentiment, contact details and owner notification are not done yet. Needs a live check on the Mac (see 03 "Test on Mac")
- [ ] Owner dashboard: conversations, bookings, unanswered questions
- [ ] Google Calendar backend for the pilot (service account, free/busy, create event, `external_ref`) — DEC-48
- [x] Booking tools wired into chat and voice, per-business enabled tools (#151): booking tools are `Tool(optional=True)`, so `TOOLS` holds only the always-on tools; `registry.tools_for(business_id)` adds the ones listed in `businesses.settings["enabled_tools"]` (e.g. `{"enabled_tools": ["check_availability", "create_booking", "cancel_booking"]}`; missing/malformed/unknown names = off). The executor refuses an optional tool the business didn't enable, even if the model calls it. Chat (`answer.py`) and the voice session (`_load_business`, also the warm-up request) send the per-business list, and the prompt gets a booking rule (`prompt.BOOKING_PROMPT`) only then. Tested with fakes (`tests/test_enabled_tools.py`); live check under "Test on Mac". `list_sessions`/`join_session` follow the same way once the group-sessions tools (#149) register with `optional=True`; the setting already accepts their names. No settings UI yet (SQL, like `transfer_number`)
- [ ] Google Calendar per-business OAuth (R14) — stage 3
- [ ] Owner notifications
- [x] Test scripts: simulated conversations for booking happy path + edge cases (#55): `tests/test_booking_conversations.py` — scripted calls where a fake model plays tool calls and answers through the real tool loop (`answer_question`), executor, booking tools, rules and Postgres. Covers happy path, appointment with a service, closed day, outside hours, full slot (+ alternatives), party too large, change of mind, declining at the read-back, bad date / missing name, cancellation (frees the slot, wrong phone, unknown reference), text chat without caller ID, caller ID beating a made-up number. Tests only: the booking tools are still not wired into live calls (#151), and a fake model doesn't show how a real model behaves — live run under 03 "Test on Mac"
- [x] Owner-side group sessions in the Bookings view (branch `feature-bookings-sessions`, DEC-54/DEC-56): `sessions` table + `bookings.session_id` (migration 0012), `POST/PATCH/DELETE /businesses/{id}/sessions[/{sid}]` and attendee removal, faded per-hour **+** UI, mock SMS (`app/actions/sms.py`, OPEN-24). Sessions do not yet block 1:1 bookings and have no real attendees until the assistant side (#149) lands; iPhone check pending. [plan](feature-bookings-sessions.md)

## Notes

- Local tool calling: 20–32B-class models on the 64 GB Mac are usable for developing
  and testing tools; 8B models are not. Final booking tests still run against Gemini (R1).
- Double-booking: check availability again inside `create_booking` in a DB transaction.
- Store times in UTC with the business timezone in its profile.
