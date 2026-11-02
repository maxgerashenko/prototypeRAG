# Actions layer

**Actions are what the app does for the caller or the business**: book a table or an
appointment, put a caller into a group session, make a call (transfer the caller to a person),
take a message, write the call summary. The outside services these use (Google Calendar,
Twilio, the LLM endpoint) are not actions: they belong to the integrations layer
([integrations.md](integrations.md)), which actions call and which never calls back.

In this codebase that covers: bookings, group sessions, the Google Calendar
mirror, take a message, transfer to a person, texting callers about cancelled sessions and the post-call summary.
Written from the code on `main` (2026-11-02). Plan and decisions: [plan/04-actions.md](../plan/04-actions.md),
[plan/feature-bookings-sessions.md](../plan/feature-bookings-sessions.md), DEC-24, DEC-30, DEC-48,
DEC-54, DEC-56 in [plan/DECISIONS.md](../plan/DECISIONS.md).

## 1. Purpose and boundaries

**In this layer** (`app/actions/`):

- The **tool registry and executor**: the one place that knows every tool the LLM may call,
  its JSON schema and its handler, and the only code that runs handlers.
- **Action handlers**: `take_message`, `transfer_to_human`, `check_availability`,
  `create_booking`, `cancel_booking`, `list_sessions`, `join_session`.
- **Booking domain**: rules, two backends (Postgres-only and Google Calendar), group
  sessions, and the calendar mirror that keeps Postgres in step with Google.
- **Post-call summary** (`summary.py`). The mock SMS sender (`sms.py`) lives here but is
  logically an integration ([integrations.md](integrations.md)); only its callers
  (`booking/sessions.py`) are actions.

**Not in this layer:**

- The tool-calling loop (LLM → tool → LLM) lives in the voice layer
  ([voice.md](voice.md)): `app/voice/session.py` and `app/rag/answer.py` call
  `app/voice/tools.run_tool`, which builds an `ActionContext` and calls `executor.execute`.
- `search_business_info` is registered in the same registry but defined in
  `app/voice/tools.py` (voice layer); it is read-only and uses the data layer.
- The prompt rules that tell the model how to use the booking/session tools
  (`app/rag/prompt.py`: `BOOKING_PROMPT`, `SESSION_PROMPT`, `tools_prompt`) are in the
  voice layer.
- HTTP endpoints (`app/api/bookings.py`, `app/api/sessions.py`, the Twilio routes that
  dial a transfer, record voicemail and trigger the summary) are in the API layer ([api.md](api.md)).
- Screens (Bookings view, session sheet, settings) are in the web layer ([web.md](web.md)).

**Boundary rule:** handlers get the business, conversation, call SID and caller ID from the
call session (`ActionContext`), never from the model's arguments, so a tool call cannot reach
another business's data.

## 2. Modules and roles

| Module | Role |
|---|---|
| `registry.py` | `ActionContext`, `Tool`, `register()`, `get_tool()`, `TOOLS` (always-on schemas), `tools_for(business_id)` (adds the business's enabled optional tools), `enabled_tool_names()` |
| `executor.py` | `execute(ctx, name, arguments)`: validation, enabled-tool check, confirmation guard, `action_log` writes |
| `messages.py` | `take_message` tool → `messages_for_owner` row |
| `transfer.py` | `transfer_to_human` tool; `transfer_number()` and `transfer_requested()` for the Twilio routes |
| `summary.py` | `summarize_conversation()`: one-line summary, intent, outcome, follow-up on `conversations` |
| `sms.py` | Mock `send_sms()`: logs and keeps the last 200 messages in an in-memory `OUTBOX`; sends nothing (#138). Logically an integration ([integrations.md](integrations.md)) that lives under `actions/`, like `google_client.py` |
| `booking/rules.py` | Pure booking rules (no DB, no clock): `Rules`, `local_start`, `violation`, `candidate_starts` |
| `booking/internal.py` | `BookingBackend` protocol, request/result dataclasses, `InternalBackend` (Postgres only) |
| `booking/google_calendar.py` | `GoogleCalendarBackend` (DEC-48): same rules, busy time from the mirror, writes events |
| `booking/google_client.py` | Thin Google Calendar REST client behind `CalendarClient` / `CalendarEventsClient` protocols; the only file that knows Google's wire format. Logically an integration ([integrations.md](integrations.md)) that lives under `actions/booking/` |
| `booking/mirror.py` | `CalendarMirror` protocol + `DbMirror`: forced sync and busy-event query over `calendar_events` |
| `booking/calendar_sync.py` | Mirrors Google Calendar into `calendar_events` (#165): full + incremental sync, background loop, `sync_now`, `post_sync_hooks` |
| `booking/sessions.py` | Group sessions (owner side and caller side): create, update, cancel, remove attendee, `list_open`, `join_session` |
| `booking/session_events.py` | Sessions as Google events (#149): create/delete event, seat-count title patching, `reconcile_events` (Google → sessions) |
| `booking/tools.py` | Registers the five optional booking/session tools; picks the backend (`make_backend()`) at import |

Tables owned by this layer (models in `app/db/models.py`, migrations 0006, 0007, 0009, 0011–0016):
`action_log`, `messages_for_owner`, `booking_rules`, `bookings`, `sessions` (`GroupSession`),
`calendar_events`, `calendar_sync_state`, and the summary columns on `conversations`
(`summary`, `intent`, `outcome`, `follow_up`, `summarized_at`).

## 3. Data flow

### 3.1 A tool call

```
LLM tool call (voice session / chat answer)
  → app/voice/tools.run_tool(business_id, name, args, conversation_id, call_sid, caller)
  → executor.execute(ActionContext, name, args)
       unknown tool               → "Error: unknown tool ..."
       optional and not enabled   → "Error: ... not available for this business."
       args not a JSON object     → "Error: ..."
       read-only tool             → handler(ctx, args)            (not logged)
       side effect, confirmed≠true→ "Not done: ... read back, ask for a yes"  → action_log needs_confirmation
       side effect, confirmed=true→ handler(ctx, args)  → action_log ok / error
  → result text back to the LLM as the tool message
```

Which tools a business gets: `TOOLS` (always on: `search_business_info`, `take_message`,
`transfer_to_human`) plus every optional tool named in `businesses.settings["enabled_tools"]`.
If the key is missing and `BOOKING_BACKEND=google_calendar`, every optional tool is on; an
explicit list (even empty) always wins.

| Tool | Optional | Side effect | Handler |
|---|---|---|---|
| `search_business_info` | no | no | `app/voice/tools.py` (voice layer) |
| `take_message` | no | yes | `messages.take_message` |
| `transfer_to_human` | no | yes | `transfer.transfer_to_human` |
| `check_availability` | yes | no | `booking/tools.check_availability` |
| `create_booking` | yes | yes | `booking/tools.create_booking` |
| `cancel_booking` | yes | yes | `booking/tools.cancel_booking` |
| `list_sessions` | yes | no | `booking/tools.list_sessions` |
| `join_session` | yes | yes | `booking/tools.join_session` |

### 3.2 Transfer to a person (phone only)

1. The model calls `transfer_to_human(confirmed=true)`. The handler checks there is a
   `call_sid` and a valid E.164 `businesses.settings["transfer_number"]`, and returns
   `"Transfer started. ..."`. The executor logs it as `ok`.
2. `app/voice/session.py` sees the `TRANSFER_STARTED` prefix, lets the last sentence play and
   closes the media stream.
3. Twilio follows the `<Redirect>` after `<Connect>` to `POST /twilio/transfer`, which calls
   `transfer_requested()` (an `ok` `transfer_to_human` row in `action_log` for this call SID) and
   `transfer_number()`, then answers `<Dial>`. No Twilio REST credentials are needed, and the
   decision survives a restart because it is read from the database.
4. Nobody answers → `/twilio/transfer-result` records a voicemail → `/twilio/voicemail` stores a
   `messages_for_owner` row with `recording_url`.

### 3.3 Take a message

`take_message(message, name?, phone?, urgency?)` → one `messages_for_owner` row (status `new`).
Phone defaults to caller ID; a dictated number replaces it only when given. Text is cut to 1000
characters. Nobody is notified yet.

### 3.4 Post-call summary

`POST /twilio/status` with a final status → `summarize_conversation(business_id, conversation_id)`
runs **inside the request** (Cloud Run throttles CPU after the response, R19). It skips a
conversation already summarized or without caller turns, sends the last 20,000 characters of
the transcript to `app.llm.chat_json` with a strict JSON schema, and stores summary, intent,
outcome (`info_request`, `booked`, `message`, `transferred`, `unresolved`) and `follow_up`.
The read-back is re-checked under a second transaction so a duplicate Twilio callback is a no-op.

## 4. Booking and calendar design

### 4.1 Rules (`rules.py`)

One `booking_rules` row per business (owner-set by SQL; no row = defaults):
`slot_minutes` (60), `capacity` (1), `lead_time_minutes` (0), `max_party_size`, `hours`
(`{"mon": [["09:00","21:00"]], ...}`, `NULL` = no hours check), `service_durations`
(lowercase service → minutes). Timezone comes from `businesses.timezone`.

`violation()` returns the first broken rule as a sentence for the caller, in this order: too
soon / past, party < 1, party too large, closed day, outside hours (the booking must end before
closing), fully booked (`overlapping >= capacity`). `candidate_starts()` gives a same-day grid
from each opening window in `slot_minutes` steps, nearest first; up to 3 free ones are
offered as alternatives. Without configured hours there are no alternatives.

Times the caller says are business-local wall-clock; everything is stored in UTC.

### 4.2 Two backends (DEC-48)

`booking/tools.make_backend()` picks one at import from `BOOKING_BACKEND`:

| | `internal` (default) | `google_calendar` |
|---|---|---|
| Busy time | confirmed `bookings` of this business | every opaque, non-cancelled event in `calendar_events` for the calendar (any business, hand-typed ones too) + this business's confirmed `bookings` the mirror doesn't show yet |
| Lock in `create` | `pg_advisory_xact_lock(hashtextextended(business_id))` | same, keyed by **calendar id** (all demo businesses share one calendar) |
| Before the check | — | `mirror.sync(calendar_id)` (forced sync), also before `check_availability` |
| Write | `bookings` row | `events.insert` with our own event id (booking UUID in base32hex), retried once, 409 = success; then `bookings` row with `external_ref` = event id. Row write fails → event deleted |
| Cancel | reference prefix + matching phone + future → `status='cancelled'` | same lookup; delete the event first; if Google fails the booking stays confirmed |
| Google down | n/a | availability/create answer "The booking calendar can't be reached right now." and book nothing |

The booking **reference** is the first 8 hex characters of the booking id, upper-case. Cancel
needs at least 6 characters of it and the phone on the booking, so a caller can only cancel
their own booking. There is no reschedule tool; a change is cancel + create.

**Phone:** `booking/tools._phone()` uses the conversation's caller ID (`conversations.caller`)
when it is a full international number; only without one (text chat) is the model's `phone`
argument used. `create_booking` refuses without a phone.

### 4.3 Calendar mirror (`calendar_sync.py`, #165)

Google owns *when* and *whether* an event exists; `calendar_events` is the app's copy.

- **First pass** (or after 410 Gone): full list with `singleEvents`, `showDeleted`, window
  today −7 … +90 days. Rows in the window that Google didn't list are checked one by one with
  `events.get` (a delete during a gap is not reported by a full list).
- **Later passes**: incremental by sync token. The token is saved in the same transaction as the
  events it covers, so a crash never skips changes. One pass at a time per calendar
  (advisory lock `calendar-sync:<calendar_id>`).
- **Google wins**: an event cancelled in Google cancels the `bookings` whose `external_ref` is its
  id; a moved one moves them.
- `business_id` comes from the event's private extended property and is stored, but the demo
  rule (Max, 2026-11-01) is that every event belongs to every business, so nothing filters on it.
- **When it runs**: a daemon thread from `app/main.py` lifespan (`run_sync_loop`, every
  `CALENDAR_SYNC_INTERVAL_SECONDS`, min 5 s) over `configured_calendars()` (env default + every
  `businesses.settings.google_calendar_id`), and `sync_now()` before each availability check,
  booking and session join. Push notifications (`events.watch`) are out: they need a public URL.
- After every successful pass, `post_sync_hooks` run; `session_events.reconcile_sessions` is
  registered there at import.

### 4.4 Group sessions (`sessions.py`, `session_events.py`)

A session is a named slot with 1–30 seats (`sessions` table); attendees are `bookings` rows
with `session_id` and `type='session'`.

- **Owner side** (via `app/api/sessions.py`): `create_session` (future start, whole local hour,
  DST-gap hours refused, one active session per hour, no confirmed 1:1 booking overlapping, same
  per-business lock as `InternalBackend.create`), `update_session` (name / seats / private; seats
  can't go below the booked count; a Google session's name is read-only), `cancel_session`
  (cancels attendees, deletes the event, texts callers after commit), `remove_attendee`.
- **Caller side** (tools): `list_open` (public, not ended, next 30 days or one day, up to 10) and
  `join_session` (forced sync, per-calendar lock, private → refused, full → refused, one seat per
  conversation).
- **With Google** (`get_client()` is not `None` only when `BOOKING_BACKEND=google_calendar`):
  Google owns which sessions exist and when; Postgres owns seats, the private flag and
  attendees. App-made sessions are inserted as events with id = session UUID hex (a Google
  failure rolls the session back). The event title carries the seats
  (`<name> · 3/8 attendees · FULL`), patched with `If-Match` (412 → re-read, up to 3 tries); a
  failed patch keeps the seat and the next sync retries. `reconcile_events` turns calendar
  changes into sessions: an event typed in Google (≤ 12 h, opaque, not ended) becomes a session
  (`source='google'`, 8 seats, public) under `GOOGLE_CALENDAR_OWNER_BUSINESS_ID` or the first
  business using that calendar; moved → moves it and its attendees; deleted → cancels it and
  its attendees (no text).
- **Demo rule**: Google sessions show for and can be joined from every business. A join from
  another business stores the attendee under the session's owner and puts
  `conversation <id>` in `notes` (the conversation FK needs the same business).

## 5. Key functions

| Function | Where | Notes |
|---|---|---|
| `register(Tool)` | registry | Import-time registration; duplicate name raises |
| `tools_for(business_id)` | registry | Schemas for `tools=`; one DB read per call |
| `execute(ctx, name, args)` | executor | The only path to a handler |
| `Tool.schema()` | registry | Adds required `confirmed` boolean to side-effect tools |
| `summarize_conversation(bid, cid)` | summary | Idempotent, never raises |
| `transfer_requested(bid, call_sid)` / `transfer_number(bid)` | transfer | Read by `/twilio/transfer` |
| `Rules.violation()` / `candidate_starts()` | booking/rules | Pure, unit-tested |
| `InternalBackend.create/availability/cancel` | booking/internal | |
| `GoogleCalendarBackend.create/availability/cancel` | booking/google_calendar | |
| `sync_pass()` / `sync_now()` / `run_sync_loop()` | booking/calendar_sync | |
| `reconcile_events()` / `push_title()` | booking/session_events | |
| `join_session()` / `list_open()` / `sessions_in_range()` | booking/sessions | `sessions_in_range` is used by `GET /businesses/{id}/bookings` |

## 6. Config

| Setting | Where | Effect |
|---|---|---|
| `BOOKING_BACKEND` | env (`app/config.py`) | `internal` (default) or `google_calendar`; also turns on the sync thread and session events |
| `GOOGLE_CALENDAR_ID` | env | Default calendar |
| `GOOGLE_CALENDAR_TOKEN_FILE` | env | OAuth user token from `scripts/google_calendar_login.py` (preferred, #163) |
| `GOOGLE_CALENDAR_CREDENTIALS_FILE` | env | Service account key (fallback) |
| `CALENDAR_SYNC_INTERVAL_SECONDS` | env | Poll interval, default 60 |
| `GOOGLE_CALENDAR_OWNER_BUSINESS_ID` | env | Business that sessions imported from Google are stored under |
| `APP_BASE_URL` | env | Conversation link in the event description |
| `businesses.settings.enabled_tools` | DB | Optional tools offered and allowed |
| `businesses.settings.transfer_number` | DB | E.164 number for `<Dial>` |
| `businesses.settings.google_calendar_id` | DB | Per-business calendar override |
| `booking_rules` row | DB | Hours, capacity, lead time, party size, durations |
| `businesses.timezone` | DB | Business-local time for rules and display |

Settings with no screen yet are set by SQL (`enabled_tools`, `transfer_number`, `booking_rules`).

## 7. Error handling

- **To the model, not exceptions**: unknown tool, disabled tool, bad JSON and rule violations
  come back as `Error: ...` / `Not available: ...` text the model can repeat. A side-effect
  handler that raises is caught by the executor, logged and returned as
  `Error: <tool> failed (<ExceptionType>)`.
- **Audit**: every side-effect call, also refused (`needs_confirmation`) and failed (`error`)
  ones, goes to `action_log`. A failing audit write is logged and swallowed.
- **Google**: every network or HTTP failure is `CalendarError` (10 s timeout per call). Subtypes:
  `SyncTokenExpired` (410 → full resync), `PreconditionFailed` (412 → re-read), `EventExists`
  (409 → treated as success). Booking paths book nothing when Google fails; cancel keeps the
  booking confirmed; session create rolls back (502); join answers 503. Sync errors are stored in
  `calendar_sync_state.last_error`; the loop logs and continues.
- **Orphans**: an event inserted for a booking whose row then fails to write is deleted again;
  a delete that also fails is logged at error level.
- **Concurrency**: advisory transaction locks (per business, or per calendar in Google mode) make
  check-then-insert atomic; `SELECT ... FOR UPDATE` on cancel and session edits.
- **Summary**: any failure is logged and swallowed so `/twilio/status` still answers 204.
- **SMS**: sent after the commit; a failing send is logged and never undoes the change.

## 8. Known gaps

Open items from the plan and things seen while reading the code. Line references are to `main`
on 2026-11-02.

| # | Gap | Where | Note |
|---|---|---|---|
| G1 | No owner notification for messages, urgent messages or follow-ups; no list of `messages_for_owner` in the API or web | `messages.py`, plan 04 "Owner notifications" | Messages are only visible in SQL |
| G2 | SMS is a mock with an in-memory, per-process outbox | `sms.py` | Lost on restart; #138 is a suggestion outside the milestones |
| G3 | Summary only runs from the Twilio status callback | `twilio_routes.py:245` | Text chat and browser calls are never summarized; no sentiment or contact fields |
| G4 | A read-only tool that raises is not caught by the executor | `executor.py:48-49` | `check_availability` / `list_sessions` catch their own expected errors, but a DB error would reach the conversation loop |
| G5 | Transfer depends on the `action_log` write, which is best-effort | `executor.py:22-29`, `transfer.py:34` | If the log insert fails, the bot says "connecting you" and Twilio hangs up |
| G6 | `confirmed=true` is trusted from the model | `executor.py:51` | The guard forces a read-back round, not a verified "yes" |
| G7 | In Google mode, 1:1 bookings lock per calendar but `create_session` locks per business | `google_calendar.py:148`, `sessions.py:181` | A session and a 1:1 booking for the same hour don't serialize against each other |
| G8 | `create_booking` always needs a phone, though the Google backend's design says the phone is saved but not required | `booking/tools.py:96`, `google_calendar.py` docstring | `join_session` accepts no phone |
| G9 | Google calls run while the booking transaction and advisory lock are held | `google_calendar.py:146-176` | Forced sync + post-sync hooks (title patches) + insert, each up to 10 s; every writer to the calendar waits |
| G10 | Open sessions with free seats still count as busy for 1:1 bookings | `mirror.py` TODO #149 | |
| G11 | "One seat per conversation" across businesses matches on the `notes` text | `sessions.py:393` | Fragile; follows from the demo rule |
| G12 | Backend and Google clients are module-level singletons built at import | `booking/tools.py:46`, `calendar_sync.py:226`, `session_events.py:44` | Three client instances; a config change needs a restart |
| G13 | Session reconciliation is registered as a side effect of importing `session_events` | `session_events.py:253` | Works because `booking/tools` → `sessions` → `session_events` is always imported via `app/voice/tools.py` |
| G14 | Other modules use private names | `api/bookings.py` (`internal._load_rules`), `sessions.py` (`G._booked`), `google_calendar.py` (`internal._check`, `_load_rules`) | Shared helpers without a public name |
| G15 | Demo rule: every event and Google session belongs to every business | `calendar_sync.py`, `session_events.reconcile_events`, `sessions.sessions_in_range` | Must change before RLS and the second real business (stage 2, M6) |
| G16 | One calendar for all businesses, one user's OAuth token | `google_client.py` | Per-business OAuth is R14 (stage 3) |
| G17 | No reschedule tool; no settings UI for `enabled_tools`, `transfer_number`, `booking_rules` | `booking/tools.py`, plan 04 | |
| G18 | `GoogleCalendarClient.busy` (`freeBusy`) is no longer called | `google_client.py:54,130` | Kept in the protocol after #166 moved availability to the mirror |
| G19 | E.164 checks differ: transfer allows 7–15 digits, booking phones 8–15 | `transfer.py:23`, `booking/tools.py:24` | |

Live checks still pending are listed under "Test on Mac" in
[plan/03-voice-channel.md](../plan/03-voice-channel.md) (transfer, booking with a real model,
Google Calendar, sessions #150).

## 9. Depends on / Used by

The layer rule: other layers call integrations, integrations never call back.

**Depends on**

| Layer | What |
|---|---|
| [data.md](data.md) | `app/db` (`tenant_session`, `SessionLocal`, models) for every table above; `businesses`, `business_profile` (business name for results), `conversations` / `messages` (caller ID, transcript for the summary) |
| [integrations.md](integrations.md) | `app/llm.chat_json` (summary); the Google Calendar REST client (`booking/google_client.py`, which sits under `actions/` but is an integration); the SMS sender (`actions/sms.py`, mock now, Twilio later, #138; sits under `actions/` but is an integration) |
| `app/config.py` | Settings listed in section 6 (shared, not a layer) |

**Used by**

| Layer | What |
|---|---|
| [voice.md](voice.md) | `app/voice/tools.py` imports `messages`, `transfer`, `booking/tools` to register tools and uses `execute`, `TOOLS`, `tools_for`, `enabled_tool_names`, `register`; `app/voice/session.py` uses `TRANSFER_STARTED`; `app/rag/answer.py` uses the registry through `app/voice/tools.py` (`tools_for`, `enabled_tool_names`, and `run_tool`, which is defined there) |
| [api.md](api.md) | `app/api/bookings.py` (`rules`, `session_events`, `internal._load_rules`, `sessions_in_range`, `CalendarError`); `app/api/sessions.py` (all of `booking/sessions`); `app/voice/twilio_routes.py` (`summarize_conversation`, `transfer_number`, `transfer_requested`; writes `messages_for_owner` for voicemail); `app/api/conversations.py` reads the summary columns; `app/main.py` starts `calendar_sync.run_sync_loop` |
| [web.md](web.md) | Indirectly, through the API: Bookings view, session sheet and blocks, conversation summaries |
| [crawler.md](crawler.md) | No link |
