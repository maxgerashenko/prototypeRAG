# ItemIconToRemove

The quiet **circle with an x** at the right-middle of a [ListItem](ListItem.md). It is the way to
remove an item without knowing the swipe: tap, mouse, keyboard and screen readers all work.
Code: `.ss-x` in `web/src/app/bookings.css`, `CloseIcon` in `web/src/app/icons.tsx`, rendered by
`AttendeeRow` in `web/src/app/SessionSheet.tsx`.

Tapping it opens the [ItemRemoveConfirmation](ItemRemoveConfirmation.md); it never removes by itself.
During an [ItemSwipeToRemove](ItemSwipeToRemove.md) the growing Remove block covers it.

## Look

| Part | Value |
|---|---|
| Button | Transparent (no fill), 44 × 44 px hit area, vertically centred in the item |
| Circle | 28 px, 1 px outline, `currentColor` |
| x | `CloseIcon` size 14, stroke 1.8 |
| Colour | `--faint` grey, the same as the `>` chevrons ([areas.md](areas.md): Misc). Hover or keyboard focus: `--text`. |
| Position | Centre **31 px from the item's right edge** (`margin: -6px 3px -6px 0` in a 6 px padded item). That is the middle of the word "Remove" once the 62 px swipe block is open, so the x sits "under" the word. |

## Behaviour

- A real `<button aria-label="Remove <name>">`.
- Busy: dimmed (30%) and disabled. Read-only (session ended): not rendered.
- Its click does not open the item's own tap target (the open-conversation tap).

## Do not

- Colour it red or fill it: a destructive-looking permanent icon on every row is loud; red appears only
  in the swipe block and the confirmation.
- Make it smaller than a 44 px hit area.
- Move it during a swipe; the Remove block covers it, it never moves.
