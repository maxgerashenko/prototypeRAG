# ItemRemoveConfirmation

The second step before a [ListItem](ListItem.md) is removed. Both entries lead here:
[ItemIconToRemove](ItemIconToRemove.md) (tap) and a full [ItemSwipeToRemove](ItemSwipeToRemove.md).
One accidental touch therefore never removes anything. There is no undo.
Code: the `ss-confirm` group in `AttendeeRow` (`web/src/app/SessionSheet.tsx`), styles in
`web/src/app/bookings.css`.

## Look and behaviour

- It replaces the item's content, in place, inside the item: two buttons only, **Keep** and
  **Remove**. No sentence.
- The group is `role="group"` named `Remove <name>?` so screen readers announce the question.
- **Keep** (neutral outline) closes the confirmation, nothing else happens.
- **Remove** (danger outline, `--danger-text`) calls the removal once.
- While the item shows the confirmation, swiping and the icon are off.

## After Remove

1. The removal request runs; the sheet is `busy` (scrim and Close are inert).
2. On success the item disappears and counts update. A short notice states the side effect
   (e.g. "Ann texted", "No phone — not texted").
3. On failure the sheet stays open, shows the error, and the item is back at rest.

## Do not

- Skip it for any entry path (swipe, icon, keyboard).
- Add explanatory text, an undo timer or a modal; it stays inline.
- Use solid red here. Solid `--danger` is reserved for the single most final action (End call).
