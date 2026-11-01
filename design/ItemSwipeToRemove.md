# ItemSwipeToRemove

Swipe a [ListItem](ListItem.md) **left** to start removing it. First built for the attendees in
the session sheet (Bookings). Code: `web/src/app/SessionSheet.tsx` (`AttendeeRow`),
`web/src/app/rowSwipe.ts` (pure decisions, tested in `rowSwipe.test.ts`), styles `.ss-row`,
`.ss-slide`, `.ss-reveal` in `web/src/app/bookings.css`, e2e in `web/e2e/sessions.spec.ts`
("swipe an attendee row"). Colours: [colors.md](colors.md), [areas.md](areas.md).

Related: the always-visible tap alternative is [ItemIconToRemove](ItemIconToRemove.md); both end in
[ItemRemoveConfirmation](ItemRemoveConfirmation.md). A swipe never removes anything by itself.

## Behaviour in one paragraph

The user drags the item left. A red **Remove** block grows in from the right edge, over the end
of the item. **The item's text never moves.** When the block is fully open plus a small margin and the
finger is released, the item's [ItemRemoveConfirmation](ItemRemoveConfirmation.md) appears. Released
short of that, the block shrinks back and nothing happens.

## Gesture rules

| Rule | Value | Why |
|---|---|---|
| Direction | Left only. Rightward and vertical moves are not ours. | Right belongs to swipe-back; vertical belongs to scrolling the panel. |
| Slop before deciding | 8 px (`ROW_SLOP`) | Separates a tap or scroll from a swipe. |
| Swipe vs scroll | Swipe only if the move is leftward and horizontal-dominant. Vertical-dominant (≥ the horizontal distance) ends the gesture. | Scrolling the list must keep working. |
| Revealed width | 62 px (`ROW_REVEAL`) | The word "Remove" (~58 px) plus ~2 px each side. |
| Fire threshold | Offset ≥ 62 + 8 px margin (`ROW_MARGIN`) at release | "Fully visible with a small margin". Prevents accidental removal. |
| Maximum drag | 70 px (`ROW_MAX`); the block never grows beyond | Stops overshoot. |
| Release short of threshold | Block animates back to 0 in 0.2 s; nothing happens | A partial swipe is a peek, not a decision. |
| Input | Pointer events (touch, pen, mouse). `touch-action: pan-y` on the item. | Same code on phone and desktop; vertical scroll stays native. |
| Capture | Pointer is captured once the swipe is recognised | Keeps tracking if the finger leaves the item. |
| Click after a swipe | Swallowed (capture phase) | A swipe must not also tap what is under the finger (a phone link, the open-conversation tap). |
| Primary pointer only | Second fingers are ignored | Avoids two-finger confusion. |

## Visual rules

- The item **does not slide.** Text, icons and links keep the same x position and width for the
  whole gesture; nothing reflows.
- The **Remove block** is absolutely positioned at the item's right edge, full item height, width =
  the drag offset (0 → 70 px), `overflow: hidden`, `pointer-events: none`, drawn **above** the item
  (so it covers the [ItemIconToRemove](ItemIconToRemove.md)). The word "Remove" is centred in a
  62 px-minimum span, so while the block is narrow only the right part of the word shows.
- Colours: text `--danger-text`; background the danger tint
  (`color-mix(in srgb, var(--danger) 18%, var(--inset))`). Font 15 px / weight 700. Red is a status
  colour, not an area colour ([areas.md](areas.md)).
- **No border, no rounded corners** on the block. The item's own 12 px rounded clip gives the outer shape.
- Growth and spring-back: `transition: width .2s ease-out`; off while the finger is down
  (`.dragging`) and under `prefers-reduced-motion`.

## Second use: session block on the calendar day view

`web/src/app/SessionBlock.tsx` swipes the same way (shared `rowSwipe.ts`; red **Delete** block via
`.bk-reveal`; e2e in `sessions.spec.ts`). Differences:

- Word is **Delete**, and the confirmation ("Delete? N callers texted", Keep / Delete) replaces the
  block's content **inside the block** instead of a row confirmation.
- The block stops pointer events from reaching the day view, so a swipe that starts on a session block
  only affects that block. A swipe anywhere else on the calendar (empty hours, 1:1 booking blocks) still changes the day.
- Ended sessions and busy state do not swipe.

## When the swipe is disabled

No swipe, normal scroll, when: a request is running (`busy`); the session has ended (read-only);
the item is showing its confirmation.

## Do not

- Slide the whole item or squeeze its content to make room.
- Add borders, pills or rounded corners to the Remove block.
- Use any text other than "Remove", or a colour other than the danger tokens.
- Fire on a short drag, a rightward swipe or a vertical scroll.
- Remove without the confirmation.

## Checklist for a new list

- [ ] Reuse `rowSwipe.ts` (`rowDecision`, `rowOffset`, `rowFires`); do not copy the numbers.
- [ ] `touch-action: pan-y`; pointer capture after recognising the swipe; click swallowed.
- [ ] Container does not also react: opt out of swipe-back with `data-swipe-back="off"`; a
      horizontal-dominant move already cancels the sheet's swipe-down.
- [ ] e2e: part-way swipe springs back; full swipe opens the confirmation; text x/width unchanged during the
      drag; block uses the danger tokens with 0 border and 0 radius; read-only items do not swipe.
- [ ] Real-device check on an iPhone (touch scroll vs swipe), which headless tests can't prove.
