# Feature plan: Guest identity for bookings

**PR:** [#312](https://github.com/maxgerashenko/prototypeRAG/pull/312) (steps 1–5)  **Plan file:** plan/feature-guest-identity.md
**Stage / milestone:** S1 (bookings, Google Calendar). Source: the "Guest identity for bookings: design" doc.

Decided with the user (2026-11-02):

| # | Decision |
|---|---|
| 1 | Table is `session_guests`, one row per person **per business** |
| 2 | Postgres `max_attendees` is the only source of truth for seats. **No** "Max attendees:" line is written to or read from Google |
| 3 | Guests are added only from the app or by voice (both check the seat limit), never from Google. A guest added in Google books nothing and the stray fake attendee is removed on the next push |
| 4 | Fake email `<name_words>@guest.invalid` (`_2`, `_3` on a clash; `guest_<8 hex>` for non-Latin names); never changes once written |
| 5 | Attendees are written only with the OAuth user token (`GOOGLE_CALENDAR_TOKEN_FILE`); a service account cannot add attendees |
| 6 | A guest removed or declined in Google loses the seat (booking cancelled, no text). 1:1 bookings the same, but the Google event stays |
| 7 | Every event write sends `sendUpdates=none` (no emails to anyone) |

## Identity rules

- Match on `name_key` (ASCII lower-case of first + last name). Same key and same or missing phone = same guest; same key and a *different known* phone = new guest. Near-matches are never merged on a guess; the owner merges (`merged_into`).
- No last name: display "Anna 2284" (last 4 phone digits).

## Done (PR #312)

- [x] 1. `session_guests` table, `bookings.guest_id` / `caller_conversation_id` / `attendee_seen_at` (migration 0017); `sendUpdates=none` on all event writes
- [x] 2. `guests.find_or_create`; `join_session` and its tool/prompt take first + last name
- [x] 3. Guests written as event attendees on every seat change (accepted, guest flags off, owner's real attendees kept, etag/If-Match patch)
- [x] 4. Sync reads attendees (migration 0018, `calendar_events.attendees`); seen-then-removed or declined guest loses the seat; unseen guest is pushed again
- [x] 5. 1:1 bookings (`create_booking`, internal and Google backends) get a guest and an attendee; `_follow_attendee` cancels a seen-then-removed 1:1 booking
- [x] Live check on the demo calendar: Google accepted the `@guest.invalid` attendee with displayName and `accepted`; patch with etag and delete worked; no API errors

## Done, not yet committed (second batch, 2026-11-02, built by parallel Sonnet agents, reviewed by sonnet5.5_REVIEW)

- [x] 6. `app/actions/booking/backfill_guests.py` (`python -m app.actions.booking.backfill_guests [--apply] [--business ID]`, dry run by default, idempotent). Past cancelled bookings also get a guest. **Not yet run on the real DB**; keep the sync's attendee comparison off until it has been
- [x] 7. `private` ↔ Google `visibility` both ways (migration 0019 `calendar_events.visibility`). A Google change applies only if newer than the session's last app update (fixes the race the reviewer found). `public`/`confidential` read as not private
- [x] 8. `app/api/guests.py`: `GET /businesses/{id}/guests`, `GET /guests/{id}` (phone masked, follows `merged_into`)
- [x] 10. `POST /guests/{id}/merge`: rows locked in id order; refused (409) when both guests hold a seat in the same session or the target is already merged

Limits found in review: merge does not re-push Google attendees (old fake email stays on events until the next push; the sync may need to treat it as the survivor's); merge does not carry the phone over; `/guests/{id}` paths need a business id or a bypass lookup before RLS (stage 3); no auth on the new routes (stage 1); concurrent-merge lock is untested; session join by short ref can fail with "session not found" when two active sessions share the prefix (flaky `test_join_session_gives_the_seat_to_a_guest`).

## To do

| # | Step | Executor | Check |
|---|---|---|---|
| 6 | **Backfill script** for existing bookings (create guests from old names/phones). Dry run first; user runs it on their Mac. Keep the sync's attendee comparison **off until the backfill is done**, or old events lose their seats | SONNET | dry-run output reviewed; second run changes nothing |
| 7 | **Private flag ↔ event visibility:** `sessions.private` ↔ Google `visibility: private`, both directions | SONNET | pytest for push and sync |
| 8 | **Guest history API:** `GET /businesses/{id}/guests`, `GET /guests/{id}` (bookings, sessions) | QWEN | pytest |
| 9 | **Guest history screen** in the voice app. Start from `design/README.md` and the list-row/sheet pattern docs; ask the user where design and `Main.dc.html` differ | SONNET + USER | vitest, Playwright, user looks |
| 10 | **Guest merge API** (owner merges two guests via `merged_into`; seats and history follow) | SONNET | pytest |
| 11 | `DEC-xx` in `DECISIONS.md` for decisions 1–7 and a GitHub issue per step 6–10 (label `LOCAL_USER_PHONE` on 6 and 9) | CLAUDE | issues exist |
| 12 | **User check in Google Calendar:** editing a guest event shows no "Send update emails?" prompt trouble; demo inbox has no stray mail | USER | user confirms |

Reviewer for every step: `sonnet5.5_REVIEW`.

## Known limits

- A removed 1:1 guest cancels the booking but leaves the Google event, which still blocks the slot until the owner deletes it.
- Not verified live: a real phone call booking with a last name; the Google UI prompt (needs a human).
- Test baseline: ~35 failures already on `main` plus the flaky `test_session_events.py::test_imported_session_item_has_its_real_length`. A "calendar post-sync hook failed" log line appeared in one full run and did not come back in the next two; not understood, watch for it.

## Where the code is

`app/actions/booking/guests.py` (identity), `sessions.py` / `internal.py` / `google_calendar.py` (booking), `session_events.py` (push attendees), `calendar_sync.py` (read attendees), `google_client.py` (`sendUpdates`), `app/db/models.py` `SessionGuest`.
