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
| **Google Calendar API** | Appointment businesses already using Google Calendar | Free/busy query + create event; OAuth per business |
| Cal.com / Calendly | Businesses using them | Cal.com is open-source with a good API |
| OpenTable / Resy etc. | Later | Partner APIs, access is restricted |

## Conversation summaries

At the end of each call/chat — run **inside a request** (Twilio status callback for calls,
end-of-chat request for chat), not as fire-and-forget background work, because Cloud Run
throttles CPU after a response is sent (DEC-24, R19):
- LLM generates: short summary, caller intent, outcome (`answered | booked | message | transferred | unresolved`),
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

Start simple: React + TypeScript pages in `web/` (DEC-37) calling JSON API routes, served
by the same FastAPI service from the built `web/dist` — no separate frontend service.
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
app/dashboard/       JSON API routes for the owner UI
web/src/dashboard/   React + TypeScript pages (DEC-37)
```

## Tasks

- [ ] Tool registry + executor with confirmation guard and action log
- [ ] Tool-calling loop in the RAG pipeline (text chat first, then voice)
- [ ] Internal booking backend + rules
- [ ] SMS confirmation
- [ ] Post-conversation summary job
- [ ] Owner dashboard: conversations, bookings, unanswered questions
- [ ] Google Calendar backend (OAuth, free/busy, create event) — stage 3
- [ ] Owner notifications
- [ ] Test scripts: simulated conversations for booking happy path + edge cases
  (closed day, full slot, change of mind, cancellation)

## Notes

- Local tool calling: 20–32B-class models on the 64 GB Mac are usable for developing
  and testing tools; 8B models are not. Final booking tests still run against Gemini (R1).
- Double-booking: check availability again inside `create_booking` in a DB transaction.
- Store times in UTC with the business timezone in its profile.
