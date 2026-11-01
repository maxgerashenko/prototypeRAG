# Feature: group sessions in the Bookings view

Stage 1 · milestone `S1 · M5 Actions` · branch `feature-bookings-sessions` (stacked on `feature-bookings-view`).
Plan by `Agent_opus_ARCHITECT`, decisions by Max (2026-10-31). Executors: Claude models only
(SONNET code agent, `sonnet5.5_REVIEW`, main session = CLAUDE steps).

## Goal

The owner adds a **1-hour group session** (name + max attendees) to any free future hour of the
Bookings day view. A faded **+** in the middle of every free future hour (DEC-56; the original hold-1.5 s / click design is superseded) plus a round **+** button.
Tapping a session opens its sheet: rename, change the maximum, **remove a booked caller**, or
**delete** the session. Callers who are removed or whose session is deleted get a text message —
through a **mock sender** (logged and recorded, never Twilio; the real one is #138).

Done when:

- [x] `sessions` table (migration 0012) and `bookings.session_id`; attendees are `bookings` rows of type `session`.
- [x] `POST/PATCH/DELETE /businesses/{id}/sessions[/{sid}]` and `DELETE …/sessions/{sid}/attendees/{booking_id}`.
- [x] `GET /businesses/{id}/bookings` returns `sessions[]` with attendees; attendee rows are not listed as separate bookings.
- [x] Mock SMS (`app/actions/sms.py`): removing a caller / deleting a session "texts" each affected caller that has a phone; recorded in an in-memory outbox + log.
- [x] Day view: session blocks (`name · 3/8`, FULL chip), faded per-hour **+** buttons, round **+** button, session sheet (create / edit / remove / delete).
- [x] `uv run pytest -q`, `npm run typecheck`, `npm test`, `npm run e2e` green (known failures excepted).
- [ ] USER: real iPhone check (Safari and home-screen PWA).

## Decisions made with Max

| Question | Decision |
|---|---|
| Branch | New `feature-bookings-sessions` off `feature-bookings-view` (no PR yet) |
| Sessions block the assistant's 1:1 bookings | **No** — left to #149 (the assistant side). The architect's step 4 is dropped |
| Delete a session with booked callers | Cancel the bookings **and text the callers** (mock sender, not Twilio). Same for removing one caller |
| Defaults (not objected to) | Sessions may be created in hatched closed hours (note shown); max attendees 1–50, default 10; name required, ≤ 60 chars; time cannot be moved in edit (delete + recreate); past sessions read-only |

## Mobile UX

> **Changed 2026-10-31 (DEC-56):** the hold gesture, ghost, mouse click and summary hint below were removed on the user's request. What is built: a faded 44 px **+** (`hour-add-<h>`) centred in every free future hour ("Empty" rule below still applies) and the round **+**. The rows about hold, cancel, feedback, iOS side effects and discoverability are historical.

| Topic | Rule |
|---|---|
| Click or hold | By `pointerType`: **mouse = click** an empty hour; **touch/pen = hold 1.5 s** (`HOLD_MS`, one constant). A short touch tap shows the ghost "Hold to add a session" for 1.2 s and creates nothing. **Everyone:** the **+** button |
| Snap | `hour = startHour + floor(offsetY / 60)` with `offsetY` from `.bk-inner`'s bounding rect (scroll-safe); the session starts at that row's hour |
| "Empty" | No confirmed 1:1 booking overlaps the hour by even a minute, no active session at that hour, and the hour is in the future (today: `h*60 > nowMin`) |
| Hold starts | `pointerdown` on `.bk-day`, target not in a block, primary pointer, not mouse, hour creatable |
| Cancel | Move > 10 px, `pointercancel`, `scroll` on `.bk-day`, second pointer, `pointerup` < 1.5 s, tab hidden. A cancelled hold never blocks the day swipe |
| After firing | A `held` flag: the next `pointerup` neither swipes nor taps; cleared on the next `pointerdown` |
| Feedback | Full-width dashed ghost block appears after 150 ms with a fill growing over the rest of the hold (a ring would sit under the thumb). `vibrateTick()` on fire (iPhone Safari/PWA has **no** Vibration API — the fill is the only feedback there). Reduced motion: label "Keep holding…" only |
| iOS side effects | `.bk-day`: `-webkit-touch-callout: none`, tap highlight off, `onContextMenu` prevented. Never `preventDefault` on `pointerdown`/`touchstart` (kills scrolling) |
| Discoverability | Summary hint: "Hold an empty hour to add" (touch) / "Click an empty hour to add" (mouse) |
| **+** button | 56 px round accent button bottom-right in the thumb zone (`env(safe-area-inset-bottom)`), hidden on past days, opens the create sheet with the first free future hour; `.bk-inner` bottom margin ≥ 88 px |
| Create sheet | Reuses `.sheet`: **Starts** native `<select>` of free future hours; **Name** input (16 px, no autofocus, Go submits); **Max attendees** stepper (− / + 44 px around a numeric input); **Add session** 52 px, disabled until a name is typed. A `<form>` |
| Edit sheet | "Session", time, "3 of 8 booked" + FULL chip; name + stepper (min = max(1, booked)); **Save** only when changed; attendee rows (name, `tel:` phone, mic icon if by assistant, 44 px **Remove** with inline "Remove Ann? [Keep] [Remove]"); **Delete session** (danger outline) → inline "Delete this session? 3 callers will be cancelled and texted." [Keep] [Delete]. Ended sessions: read-only |
| Selected | The block has a 2 px outline while its sheet is open; Delete lives in the sheet |
| Back / Esc | Sheet first, then calendar, then leave; ←/→ ignored while a sheet is open |

Rejected: tap-to-create on touch, 500 ms hold, ring at the finger, haptics as main feedback,
double-tap, drag range, swipe-to-delete, `window.confirm`, autofocus on the name field,
moving a session's time in edit.

## Data model & rules (DEC-54 to record)

- `sessions`: `id, business_id, starts_at, ends_at` (timestamptz UTC), `name`, `max_attendees`, `status` (`active|cancelled`), `created_at, updated_at, cancelled_at`; checks: `ends_at = starts_at + 1 hour`, name 1–60 chars, max 1–50; `UNIQUE(business_id, id)`; partial unique `(business_id, starts_at) WHERE status='active'`.
- `bookings.session_id` NULL + composite FK `(business_id, session_id)` (NO ACTION); type check adds `'session'`.
- No RLS yet (stage 3), every query via `tenant_session`. Write routes are unauthenticated like the others (OPEN-19).
- Server validates: request carries local `date` + `hour`; DST gap → 422; create needs `starts_at > now`; edit/delete/remove need `ends_at > now`; same hour → 409; overlapping 1:1 booking → 409; max below booked → 422. Create takes the same advisory lock as `InternalBackend.create`.
- Delete = soft (`status='cancelled'`), cancels the session's confirmed bookings in the same transaction, returns the count; then texts each affected caller with a phone. Remove attendee = cancel that booking + text that caller.
- `sessions[]` in the GET: `{id,date,start_min,end_min,name,max_attendees,booked,attendees:[{id,name,phone,by_assistant}]}`.
- Mock SMS: `app/actions/sms.py` `send_sms(to, body)` logs and appends to `OUTBOX`; the seam #138 replaces with Twilio. Callers without a phone are skipped and counted in the response (`texted`, `no_phone`).
- Assistant side (join a session, capacity) stays in #149 — until then sessions have no real attendees (tests and e2e seed rows).

## Steps

| # | Step | Executor | Files | Check |
|---|---|---|---|---|
| 0 | Plan file, README link, DEC-54/DEC-55/OPEN-24 (+ Knowledge row: no `navigator.vibrate` on iOS), issues | CLAUDE | plan/, README.md | grep |
| 1 | `Session` model, `Booking.session_id`/type check, migration 0012 | SONNET | app/db/models.py, app/db/migrations/versions/0012_sessions.py | `uv run alembic upgrade head --sql`, import models |
| 2 | Apply 0012 to dev DB, round-trip | CLAUDE | — | alembic up/down/up, `\d sessions` |
| 3 | Domain logic, mock SMS, owner API, extended GET + tests | SONNET | app/actions/sms.py, app/actions/booking/sessions.py, app/api/sessions.py, app/api/bookings.py, app/main.py, tests/ | `uv run pytest -q` |
| 4 | ~~Sessions block 1:1 bookings~~ — dropped (left to #149) | — | — | — |
| 5 | API types/fetchers, pure helpers, hold state machine + vitest | SONNET | web/src/api.ts, bookings.ts, holdToAdd.ts (+ tests) | `npm run typecheck && npm test` |
| 6 | Hook (sessions, sheets, mutations, overlay order) + App key guards | SONNET | useBookings.ts, App.tsx | typecheck + test |
| 7 | UI: session blocks, ghost/hold, click, + button, `SessionSheet`, CSS | SONNET | BookingsScreen.tsx, SessionSheet.tsx, bookings.css, app.css, icons.tsx | typecheck + build |
| 8 | e2e: mocks + `sessions.spec.ts` | SONNET | web/e2e/ | `npm run e2e` |
| 9 | design/README 1c, 04-actions, tick this plan | CLAUDE | — | grep |
| 10 | iPhone check (Safari + PWA): tap the per-hour **+** (also while swiping), keyboard, remove/delete with a SQL-seeded attendee | USER | — | result in this file + issue |

Review (`sonnet5.5_REVIEW`) after steps 3, 7, 8; Claude picks findings, Sonnet fixes.
Order: 1 ∥ 5 → 2 → 3 ∥ 6 → 7 → 8 → review → 9 → 10.

## Review points

- Pointer state machine: a fired hold never also swipes/taps; a horizontal drag still swipes; `pointercancel`/`scroll` cancel; no `preventDefault` on down/start; timers use `setTimeout` only (no `Date.now()` under a frozen clock) and are cleared on unmount/day change.
- `offsetY → hour` while scrolled; the client's "empty" rule matches the server's.
- Server authoritative: DST gap, `starts_at > now`, ended = read-only, max ≥ booked, 409 on overlap and on the partial unique index, same advisory lock key as `internal.py`.
- Delete cancels attendees in one transaction; remove touches only that booking within that session and business; SMS is sent only after the commit and a failing send never rolls back.
- `list_bookings` excludes attendee rows; summary, dots and `viewHours` include sessions.
- Sheets: 16 px inputs, 44 px targets, scrim/close inert while busy, stale responses dropped, Esc/Back order.
- Write routes are unauthenticated (OPEN-19): flag, don't fix.

## Risks / open

- Built 2026-10-31: backend 461+ pytest pass (1 known failure), unit tests and e2e pass except the 4 known e2e failures (after DEC-56: 168 unit tests, full e2e 146 + 4 known, sessions.spec 13/13 re-run). Reviews (backend, frontend) done and fixed. **Not tested: real iPhone (step 10).**
- A 1:1 booking made by the assistant after a session can overlap it (`InternalBackend.create` ignores sessions) — #149.
- Invalid `business.timezone` falls back to UTC in `sessions.py`; the mock SMS logs the full phone number (drop in #138).
- Sheets do not trap/restore focus (same as the existing sheets).

- iOS behaviour (taps on the per-hour **+** next to the day swipe, keyboard over the sheet) is unverified until the iPhone check.
- #149's "code written, not pushed" was not found in the repo; this plan's schema is the base and #149 rebases onto it.
- OPEN-24: real SMS (Twilio) to callers, wording, opt-in — #138.
