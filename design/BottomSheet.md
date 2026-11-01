# BottomSheet

The panel that slides up from the bottom of the screen over a dimmed background, for details or a
short task that belongs to the screen behind it. Common names: **bottom sheet** (Material: *modal
bottom sheet*; iOS: *sheet*). "Half-open menu" is a good picture of how it looks, but it is not a
different component. Used for: booking details, session create / edit, the phone-number picker.
Code: `.sheet-scrim`, `.sheet-panel`, `.sheet-grip` in `web/src/app/settings.css`; session / booking
variants `.ss-panel`, `.bk-sheet-panel` in `web/src/app/bookings.css`; drag-to-close in
`web/src/app/useSheetDrag.ts` + `sheetDrag.ts` (pure decisions, tested in `sheetDrag.test.ts`);
components `SessionSheet.tsx`, `BookingSheet.tsx`, `NumberSheet.tsx`.

## Size: fits its content, never full screen

There are **no half / full stops** and it cannot be dragged up. The height simply follows the content:

- Short content → a short sheet, anchored to the bottom.
- More content → the sheet grows taller, up to **80 % of the screen** (`max-height: 80%`).
- Beyond that the **content scrolls inside the sheet** (`overflow-y: auto`,
  `overscroll-behavior: contain`, so the screen behind does not scroll). In the picker the list
  (`.sheet-list`) scrolls and the header stays.

## Opening

- Slides up from the bottom: `vw-sheet`, `translateY(100%) → 0`, 0.32 s, `cubic-bezier(.2, .8, .2, 1)`.
- The scrim fades in (`vw-fade`, 0.2 s): `#05070aa8` over the whole screen area.
- Looks: panel `#14171d`, 1 px `--line` border (not on the bottom edge), top corners 24 px, padding
  10 / 16-18 / 24-28 px, grip at the top (40 × 5 px, `#343a46`, centred).

## Closing

| Way | Notes |
|---|---|
| **Swipe down** on the sheet | Touch: starts anywhere on the panel while it is scrolled to the top and not on a field. Mouse / pen: only by dragging the grip (the grip has an enlarged hit area). The panel follows the finger. |
| Tap the scrim (dimmed area) | Same as Close. |
| Close button (×) | In the header, except the session and number sheets: they have none (the session sheet's chips (FULL, Saved) take that spot, right-aligned on the subtitle row). Close by scrim, drag or Esc. |
| Esc key | Session sheet and number picker only (see gaps). |
| The sheet's own action | e.g. Save, Delete session, Assign. |

Swipe-down rules (`sheetDrag.ts`):

| Rule | Value |
|---|---|
| Slop before deciding | 8 px |
| Direction | Downward and vertical-dominant only. Upward or sideways moves are not ours (so the attendee row swipe, scrolling and fields keep working). |
| Closes when released | ≥ 100 px down, **or** a flick ≥ 30 px at ≥ 0.5 px/ms |
| Released short | Springs back (0.2 s `ease-out`; none under `prefers-reduced-motion`) |
| While busy (request running) | Drag, scrim and Close are inert |
| Scrolled panel | Dragging does not start until the content is back at the top, so scrolling up never closes the sheet |

## Semantics

- `role="dialog"`, `aria-modal="true"`, an `aria-label` that names the sheet ("Session", "Booking details").
- The scrim is a button with a label ("Close session").
- The sheet is drawn above the screen content (`z-index: 5`) and swipe-back / day swipe are opted out
  behind it.

## Known gaps

- Escape closes the session sheet and the number picker (handled in `App.tsx`), but not the booking-details sheet: there Esc leaves the Bookings screen instead.
- No focus trap in the sheets yet.
- No drag-up expansion and no half-height stop; if wanted, add as a new variant (detents), not by changing this one.
- On iPhone, the on-screen keyboard over a sheet with fields is not verified.
- Real-device check of swipe-down vs scroll is pending.

## Checklist for a new sheet

- [ ] Panel inside a `.sheet-scrim` + `.sheet-panel` pair with a `.sheet-grip` first.
- [ ] Mount `useSheetDrag` on the container; pass `busy` and `onClose`.
- [ ] Long content: scroll area inside the panel, not the page.
- [ ] `role="dialog"`, `aria-modal`, label; labelled scrim button.
- [ ] Every close path is inert while a request runs.
- [ ] e2e: opens, swipe down closes, short drag springs back, scrim closes.
