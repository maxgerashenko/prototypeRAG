# ListItemAction

The **primary action** of a [ListItem](ListItem.md): what happens when the user presses the item
anywhere except the remove icon. First built on the attendee row of the session sheet (Bookings).
Code: `AttendeeRow` in `web/src/app/SessionSheet.tsx` (`onOpen`, `canOpen`), styles `.ss-slide.openable`
and `.ss-phone` in `web/src/app/bookings.css`, e2e in `web/e2e/sessions.spec.ts`. Colours:
[colors.md](colors.md), [areas.md](areas.md).

It is the other half of the item's two actions; the first is removing
([ItemSwipeToRemove](ItemSwipeToRemove.md), [ItemIconToRemove](ItemIconToRemove.md),
[ItemRemoveConfirmation](ItemRemoveConfirmation.md)). The two never overlap: a swipe never triggers the
action, and the remove icon never does either.

## What the action is

An item has at most one action, chosen by what it knows:

| Item has | Press does | Pressed look |
|---|---|---|
| A conversation (`conversation_id`) | Opens that conversation. Back returns to the list exactly as it was (sheet still open) | Mic icon always blue; the grey number turns blue while touched |
| No conversation, has a phone | Calls the number (`tel:`) | Phone icon always green; the grey number turns green while touched |
| Neither | Nothing; the item is inert (no pointer cursor, no button role) | - |

New lists define their own row in the same table (what it opens, which area colour it wears).

The item says what pressing does with a hint after the number: "See conversation" or "Make a call".

## Target and semantics

- The target is the **whole item**: full height and width, including the strip under the Remove block.
- `role="button"`, `tabIndex=0`, `cursor: pointer`.
- `aria-label`: "Open conversation with <name>" or "Call <name>".
- Enter and Space work when the item itself has focus. Focus ring: 2 px `--conversations`, inside the item.
- Links and buttons inside the item (the `tel:` number, the remove icon) keep their own action and do not
  also fire the item's action.

## Pressed state

- The number's colour change (grey → the icon's colour) shows only while the item is **pressed** (`:active`) or keyboard-focused.
- It returns to normal the moment the touch is released or the finger slides off.
- `:hover` is used only on devices that really hover (`@media (hover: hover)`); on touch screens a tap would
  leave `:hover` stuck on, so the number would stay green after release.
- No tap-highlight flash (`-webkit-tap-highlight-color: transparent`).

## Interaction with other gestures

- A swipe left never also triggers the action: the click after a swipe is swallowed.
- The sheet's swipe-down and the day view's swipe do not start on this item.
- Scrolling the list still works; a vertical move is not a press.

## States

| State | Action |
|---|---|
| Busy (a request is running) | Off |
| Read-only (session ended) | Still works (only swipe and remove icon are off) |
| Confirming removal | Off; the item shows Keep / Remove instead |

## Do not

- Put the action on only the number or the name: the whole item is the target.
- Fire the action from the remove icon, from a swipe, or while busy.
- Leave the pressed colour on after release.
- Use a colour for the pressed state that does not belong to what the action does (phone call = Phone green).

## Checklist for a new list

- [ ] Define the action table (what it opens or does, which area colour).
- [ ] Whole item is the target, with `role="button"` and a specific `aria-label`.
- [ ] Inner links and buttons are excluded from the item's click.
- [ ] Pressed colour on `:active` / `:focus-visible`, hover only under `(hover: hover)`.
- [ ] Click after a swipe is swallowed.
- [ ] e2e: the role and label; pressed colour while the mouse is down and normal after release.
- [ ] Real-device check on an iPhone (tap vs swipe vs scroll, the `tel:` prompt), which headless tests can't prove.
