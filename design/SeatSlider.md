# SeatSlider (and "Private")

Sets how many people a session takes. A slider with one stop per number, so the value is always an
exact number and never in between (replaces the old − / + stepper with a typed field).

Code: `SeatSlider` in `web/src/app/SessionSheet.tsx`; `.ss-slider*` in `web/src/app/bookings.css`;
stops in `web/src/app/bookings.ts` (`seatsToStop`, `stopToSeats`).

## Stops

| Stop | Meaning | Stored (`max_attendees`) |
|---|---|---|
| 1 … 30 | seat limit; shows `FULL` when reached | the number |
| 31 (last) = **Private** | no seat limit, closed to the public (only people added by hand; the assistant must never book into it) | `null` (`private: true` in the API) |

- Native `<input type="range" min=1 max=31 step=1>`: touch, mouse and arrow keys all move one stop at a time.
- Big value label above the track: the number, or a lock + "Private" in `--bookings`. Under the track:
  `1` on the left, `Private` on the right.
- Default for a new session: 10.
- Lowest stop = people already booked (`max(1, booked)`); dragging below snaps back. Private is always allowed.
- Disabled for an ended session.

## Where "Private" shows

| Place | Shows |
|---|---|
| Calendar block | lock icon instead of the people icon; line `3 · Private` (never `FULL`); `aria-label` ends `3 booked · Private` |
| Session sheet | `3 booked · Private`; slider on the last stop |

Colours follow [areas.md](areas.md): lock and Private label use the Bookings colour (`--bookings`).

## Open

- The assistant does not book into sessions yet; "closed to the public" is enforced when it does (the
  tool must skip `max_attendees IS NULL`).
- Tick marks on the track are not drawn (30 stops are too dense on a phone); revisit after the iPhone check.

## Update (2026-11-01)

Private is its own flag, no longer a slider stop: `private: true` keeps `max_attendees` (1-30). Private means the
assistant does not offer the session to callers; only people added by hand. The picker is a volume-style bar
(people icon inside, number outside on the right) with a small Private switch on the same row. The session sheet
has no Save button: edits save 2 s after the last change and when the sheet closes; a green "Saved" next to the
time/booked line appears after a save and hides on the next edit. Field label is "Attendees"; the list is "Booked".
Where this contradicts the sections above, this update wins.
