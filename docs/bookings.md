# Bookings: how the conversation works, where the data lives, how it is tested

This page follows one booking from the caller's first sentence to the row in Postgres and the
event in Google Calendar. It describes the code on this branch. The layer view is in
[actions.md §4](actions.md#4-booking-and-calendar-design); the decisions are DEC-48 (backends) and
DEC-27/DEC-33 in [`plan/DECISIONS.md`](../plan/DECISIONS.md).

Three kinds of thing can be booked. All three go through the same chat/voice tool loop:

| Kind | Tools | Result row |
|---|---|---|
| Table (party size) | `check_availability`, `create_booking`, `cancel_booking` | `bookings.type = 'table'` |
| Appointment (a service) | the same three | `bookings.type = 'appointment'` |
| Seat in a group session | `list_sessions`, `join_session` | `bookings.type = 'session'`, `session_id` set |

The tools are optional per business: `businesses.settings["enabled_tools"]` lists the ones offered.
The prompt text for a tool group is added only when the business has that group
(`app/rag/prompt.py`: `BOOKING_PROMPT`, `SESSION_PROMPT`).

## 1. How the conversation is managed

### 1.1 The normal path

```
caller ──▶ POST /chat (or voice turn) ──▶ answer_question ──▶ LLM (+ tools) ──▶ reply
                                                  │
                                   tool call ──▶ executor.execute ──▶ booking tool ──▶ backend
```

1. The caller asks. For sessions the model calls `list_sessions`; for a table or appointment
   `check_availability`. It offers only what the tool says is free.
2. The model collects the details: session/time, first **and** last name, party size or service.
3. It **reads everything back** (session, day, time, name spelled out) and asks one question,
   "Shall I book it?". The name is part of that question; it is asked once.
4. Only after a clear yes it calls `join_session` / `create_booking` with `confirmed=true`.
5. The tool result starts with `Booked …` and the reference. Only then may the bot say it is booked
   and read the 8-character reference aloud. No text message is sent.
6. Cancel: the caller gives the reference; the bot asks for a yes; `cancel_booking` checks the
   reference **and** the caller's phone. Changing a booking = cancel + new booking, each after a yes.

### 1.2 Rules in code, not only in the prompt

The prompt asks for the above; the code enforces it because the local model sometimes ignores
the prompt. Every refusal is a tool result string the model sees, so it can recover.

| Guard | Where | What it stops |
|---|---|---|
| `confirmed=true` required | `app/actions/executor.py` | a side-effect tool run before the caller said yes; returns `Not done: …` |
| Unknown tool / bad JSON / tool not enabled | `executor.py` | model calling tools it was never offered |
| Placeholder name (`John Doe`, `Caller`, `[First Name]`) | `tools._is_placeholder` → `NAME_NEEDED` | a made-up name |
| Last name needed | `join_session` → `LAST_NAME_NEEDED` | first name only (sessions) |
| Read-back needed | `tools._read_back_asked` → `READBACK_NEEDED` | a booking made on the answer to "what is your name?" rather than to a read-back. Looks only at the **last sentence** of the bot's last message, using `heard_text` (what the caller really heard) |
| Session code resolution | `tools.resolve_session` | the model passing a session *name*; several sessions can share a name, so a name is matched to the soonest open one or refused |
| Phone from caller ID | `tools._phone` | the model's guessed number; the model's number is used only when there is no caller ID |
| Date/time parse | `tools._starts_at`, `_mentions_time` | reads `10:00 AM to 11:00 AM`, `10:00-11:00` and 24-hour times |
| Session lookup by name | `tools.resolve_session(…, hint)` | the model lost the code and passed a name; the bot's last read-back (session and time the caller agreed to) narrows the match, so a bare name cannot slip to a differently named later session |
| Table words as service | `tools._service` | `service="table"` turning a table into an appointment |

Chat-only reply guards (non-streaming `answer_question`, `app/rag/answer.py`). They fix the text the
caller sees, they never change data:

| Guard | Problem it covers |
|---|---|
| `guard_booking_claim` (`claims_booking`, `NOT_BOOKED_ANSWER`) | P9: bot says "booked" with no successful tool call |
| `repeats_name_question`, `given_name`, `known_name` | asking again for a name already given |
| `asks_only_last_name` | asking for the last name when the caller gave both |
| `relists_sessions` / `WANTS_LIST` | listing all sessions again after one was chosen |
| `shorten` (40 words) | P10: long replies on the phone |
| empty-reply retry + `turn_fallback` | the model returning nothing |
| ONE combined retry | all text guards share a single text-only retry with nudges |

The tool-level guards also protect voice. The reply-text guards do not yet apply to the streaming
voice path (open item, see §5).

### 1.3 Race and duplicate protection

- One advisory lock per calendar (`pg_advisory_xact_lock(hashtextextended(…))`; per business when
  there is no Google client), so all writers to the shared demo calendar queue up.
- A caller (conversation) cannot take two seats in one session (`caller_conversation_id`).
- Seats are counted and compared to `max_attendees` inside the lock.
- Google event ids are derived from our id (base32hex); a retry after a timeout gets 409 =
  "already there", never a second event.

### 1.4 Times

Every time the caller hears or the model reads is on a 12-hour clock with AM/PM: `rules.clock()`
gives `10:00 AM`, `6:30 PM`; session lists, booking results, alternatives, cancellation texts and the
"Now" line in the prompt all use it. Prompts tell the model to say times that way.

## 2. Source of truth: who owns what

| Data | Owner | Copy / where it lives |
|---|---|---|
| **When** an event exists, its time, moved or deleted | **Google Calendar** (only when `BOOKING_BACKEND=google_calendar`) | mirror table `calendar_events`, kept current by incremental sync (`calendar_sync.py`); on a conflict **Google wins** |
| Session seats, `private` flag, who attends | **Postgres** (`bookings`, `sessions`, `session_guests`) | Google only shows a title like `5-6pm Sauna · 3/3 attendees · FULL`, written by us |
| Booking rows (table/appointment/session), status, phone | **Postgres** `bookings` | Google event for tables/appointments holds name, last 4 digits of the phone, reference, link to the conversation |
| Rules: hours, slot length, capacity, lead time, party limit, service durations | **Postgres** `booking_rules` | read on each check (`rules.py`), identical for both backends |
| Conversation text and tool calls | **Postgres** `conversations`, `messages`, `action_log` | none |
| Guest identity | **Postgres** `session_guests` | the fake attendee email on the Google event |

With `BOOKING_BACKEND` unset the default is `InternalBackend`: Postgres is the only source and no
Google code runs (`tools.make_backend`). Tests force this.

Sync direction rules (`calendar_sync.py`, `session_events.py`, `mirror.py`):

- Event **deleted in Google** → the `bookings` with that `external_ref` are cancelled; a session
  event deleted → the session and its attendee bookings are cancelled (no text message).
- Event **moved** in Google → the bookings/session move with it.
- Event **typed by hand** in Google → becomes a `sessions` row, `source='google'`, default seats, public.
- Our seat change patches the event title with `If-Match: <etag>` (412 → re-read, retry up to 3). A
  failed patch keeps the seat; the next sync pass retries it (`reconcile_sessions`).
- Before every availability check and every booking a **forced sync** runs. If Google cannot be
  reached nothing is booked (`CalendarError` → "The calendar can't be reached right now").
- Demo rule: one shared calendar; every event blocks every business and every event is a session
  for every business. `calendar_events.business_id` is stored (from the event's private
  properties) but nothing filters on it yet.

## 3. Extra metadata saved locally

Beyond what the caller said, these are written so that a booking can always be traced back to
the conversation that made it.

| What | Table.column | Why |
|---|---|---|
| Conversation link | `bookings.conversation_id`, `bookings.caller_conversation_id`, `bookings.notes = "conversation <id>"` | find the transcript of a booking. The FK needs the same business; across businesses (demo calendar) only the id in `notes` and `caller_conversation_id` are used |
| Caller ID phone | `bookings.phone`, `session_guests.phone` | cancel check; tell two guests with the same name apart. Stored in E.164 (`normalize_phone`) |
| Guest identity | `session_guests`: `display_name` (as spoken, Unicode kept), `name_key` (ASCII, lower case), `email` (`…@guest.invalid`), `source`, `merged_into` | one stable person per name; the fake email never changes when the spelling is fixed. A near-match ("Katherine" / "Catherine") is a new guest; the owner merges |
| Google event id | `bookings.external_ref`, `sessions.external_ref` | cancel/move and sync matching |
| Event owner marks | Google private properties `business_id`, `booking_id`, `conversation_id` | read back by sync; link event → business |
| First seen as attendee | `bookings.attendee_seen_at` | sync noticed the attendee in Google |
| Mirror | `calendar_events`: `google_event_id`, `etag`, `summary`, `starts_at`, `ends_at`, `status`, `transparency`, `visibility`, `attendees` (JSON), `google_updated`, `synced_at`; `calendar_sync_state` (sync token) | availability without a Google call per check |
| Audit trail | `action_log`: `tool`, `arguments` (JSON), `result`, `status` (incl. `needs_confirmation`, refused and failed calls) | what the bot tried, and what the tool answered |
| Heard text | `messages.content` keeps the whole reply; a `[not heard: …]` suffix marks the part cut by barge-in. `heard_text()` strips it | the model is given only what the caller heard; the transcript keeps everything |
| Message order | `message_order()` (`created_at`, then role) | stable order inside one turn |
| Review state | `conversations.review_status`, `review_note`, `reviewed_at` | verified / in_testing / fixed marks shown in the UI (`/review-conversations` skill) |
| Reference | not stored: first 8 hex chars of `bookings.id` (session code: first 8 of `sessions.id`) | short code read aloud; matched with `startswith` |

## 4. Fields used

**Tool arguments** (`app/actions/booking/tools.py`):

| Tool | Arguments | Notes |
|---|---|---|
| `check_availability` | `date` (YYYY-MM-DD), `time` (HH:MM), `party_size`, `service` | business-local time |
| `create_booking` | the same + `first_name`, `last_name`, `phone`, `notes`, `confirmed` | phone = caller ID first |
| `cancel_booking` | `reference`, `phone`, `confirmed` | phone must match the booking |
| `list_sessions` | `date` (optional) | open sessions only, soonest marked `SOONEST`, no private ones |
| `join_session` | `session` (code), `first_name`, `last_name`, `phone`, `confirmed` | code exactly as listed |

**`bookings`**: `id`, `business_id`, `conversation_id`, `caller_conversation_id`, `session_id`,
`guest_id`, `type`, `starts_at`, `ends_at`, `party_size`, `service`, `name`, `phone`, `notes`,
`status` (`confirmed` / `cancelled`), `external_ref`, `attendee_seen_at`, `created_at`, `cancelled_at`.

**`sessions`**: `id`, `business_id`, `starts_at`, `ends_at`, `name`, `max_attendees`, `private`,
`status`, `external_ref`, `source` (`app` / `google`), timestamps.

**`booking_rules`** (per business): `slot_minutes` (60), `capacity` (1), `lead_time_minutes` (0),
`max_party_size`, `hours` (JSON by weekday), `service_durations` (JSON).

**Checked by availability** (`rules.py`): inside opening hours, after the lead time, party within
the limit, slot not over capacity (Google backend: every opaque mirrored event counts one by one, plus
confirmed `bookings` rows the mirror does not show yet).

**Google event** (tables/appointments): summary `Name - service`; description with the name, last
4 digits of the phone, the reference and `APP_BASE_URL/web/#conversation=<id>`; nobody is invited
(a service account cannot add attendees on a Gmail calendar).

## 5. How the conversations are tested

Three layers, cheapest first.

| Layer | Files | Needs | What it proves |
|---|---|---|---|
| Unit / API | `tests/test_booking.py`, `test_google_calendar.py`, `test_calendar_sync.py`, `test_session_events.py`, `test_sessions_api.py`, `test_guests*.py`, `test_bookings_api.py`, `test_google_sessions_view.py` | Postgres | rules, backends, sync, seat counting, guests |
| Guards | `test_placeholder_names.py`, `test_join_session_lookup.py`, `test_booking_conversations.py`, `test_answer_empty_reply.py`, `test_voice_session.py` | Postgres | every guard in §1.2 with fixed messages, `heard_text`, no model |
| Scenarios | `tests/scenarios/` (`scenarios.yaml`, `harness.py`, `test_booking_scenarios.py`, `README.md`) | Postgres + LM Studio, both models loaded | the real bot, end to end |

`tests/conftest.py` has an autouse fixture: the internal backend is always used, the Google
session client is off (`session_events.set_client(None)`), so **no test can write to the real
calendar**. At session end it deletes every business without a domain; never run two pytest
sessions at once.

### 5.1 Scenario tests

A local model (`google/gemma-4-12b`) plays the caller from a `persona`. It talks to the real
`POST /chat` (FastAPI `TestClient`) answered by the real `qwen/qwen3.6-35b-a3b`, with the real
prompt, tools and Postgres. A fresh business is created per scenario (internal backend, the five
tools enabled) and deleted after. The verdict comes **only from database and transcript checks**,
never from an LLM grader. Voice, Twilio and TTS are out of scope.

Invariants checked in every scenario:

| | Invariant |
|---|---|
| a | if a bot message says booked/confirmed/reserved, a confirmed booking exists for that conversation |
| b | never more confirmed seats than `max_attendees` |
| c | no booking without `guest_id` |
| d | a bot reply has at most 40 words and does not list several session times unless asked |
| e | a year the bot names is the current (or next) one |
| f | every time the bot says carries AM or PM (`10:00 AM`, never `10:00` or `18:00`) |

Per-scenario `expect` keys (`scenarios.yaml`): `new_confirmed`, `guest`, `phone`, `booking_session`,
`booking_type`, `party_size`, `day`, `hour`, `seats`, `cancelled`, `untouched`, `bot_says`,
`forbid_after`, `max_confirmations`, `bot_mentions_day`, `skip_claim_check`.

The 19 scenarios: session happy path, first name only, declined at read-back, same name on several
days, full session, table with party and phone, cancel by reference + phone, changes mind about
time, garbled session name (STT), name given once, context memory then "yes", brevity / no re-list,
ambiguous same name on two days, name repeated not re-asked, day and time not re-asked, sessions
question uses the tool, booking reference is read, yes to read-back books without asking again, two sessions whose names differ only by the word "session" (P12: the right one is booked).
Each is tagged with the known problem it guards (P2 false "booked", P3 garbled name, P5 brevity, P6
context memory, P8 right date; later P9–P11).

```
SCENARIO_RETRIES=0 uv run pytest -q -m scenario -p no:logging              # all, 4–5 minutes
SCENARIO_RETRIES=0 uv run pytest -q -m scenario -p no:logging -k <id>      # one
uv run python -m tests.scenarios.harness <scenario-id>                      # live transcript + DB
```

They are skipped without `-m scenario`, so the default `uv run pytest -q` stays fast. On failure the
full transcript and the DB state (bookings, seats, `action_log`) are printed. Runs are not
deterministic: the default retries a failed scenario once and warns "flaky" when it passes on retry.

**Rule for marking a conversation fixed** (`/review-conversations`): a recorded conversation is
marked *fixed* only after the scenario built from it passes twice. Until then it stays *in testing*.

### 5.2 Known limits

- 0–2 scenarios fail per full run and pass on re-run (model non-determinism); the last full run
  had 4 failures that were not re-checked after the review fixes.
- The "booked" claim guard and the reply-text guards run only in non-streaming `answer_question`;
  the streaming/voice path has the tool-level guards only.
- No scenario yet for health claims (conversation `efcddc40`, still *in testing*).
- The caller model sees only text; barge-in, STT errors and the phone line are not simulated.
- Live Google Calendar is never touched by tests; the Google backend is tested with a fake client.
