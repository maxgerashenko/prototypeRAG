# ListItem

A row in a list inside a sheet or card. The reference implementation is the attendee row of the
session sheet (`AttendeeRow`, `web/src/app/SessionSheet.tsx`; styles `.ss-row`, `.ss-slide` in
`web/src/app/bookings.css`). Surface: `--inset` with `--inset-line` border, 12 px radius.

## Anatomy

`[ name ]  [ icon  number  hint ]  ………  [ ItemIconToRemove ]`

## Two actions

Every ListItem has exactly two actions, kept apart so they never fight:

| Action | Gesture | Result |
|---|---|---|
| **Primary action** (the item's purpose) | Press anywhere on the item that is not the remove icon | Depends on the item, see below |
| **Remove** | Swipe left, or press the [ItemIconToRemove](ItemIconToRemove.md) | [ItemRemoveConfirmation](ItemRemoveConfirmation.md) (Keep / Remove) |

The primary action of an attendee:

| Attendee | Press the item | Look while pressed |
|---|---|---|
| Booked in a conversation | opens that conversation (Back returns to the open sheet) | number stays blue |
| Added by hand, has a phone | calls the number (`tel:`) | phone icon always green; the grey number turns green while touched |
| Added by hand, no phone | nothing (item is inert) | - |

## Features

| Feature | Doc / where | Status |
|---|---|---|
| Primary action: press the whole item | [ListItemAction](ListItemAction.md) | built (open conversation, or call) |
| Swipe left to remove | [ItemSwipeToRemove](ItemSwipeToRemove.md) | built; headless-tested, iPhone check pending |
| Icon to remove | [ItemIconToRemove](ItemIconToRemove.md) | built |
| Remove confirmation (Keep / Remove) | [ItemRemoveConfirmation](ItemRemoveConfirmation.md) | built |
| Phone line: icon (green phone / blue mic, always) + grey number that colours while touched | below | built |
| Read-only and busy states | below | built |

### Primary action: press the whole item (optional)

Full description: [ListItemAction](ListItemAction.md). In short:

When an item has a primary action, the **whole item** (full height and width, including the strip under
the Remove block) is the target: `role="button"`, `aria-label` "Open conversation with <name>" or
"Call <name>", pointer cursor, Enter / Space work. Items without an action stay inert. The remove icon
and links inside keep their own action (the number is itself a `tel:` link); a swipe never also
triggers it (the click after a swipe is swallowed). Read-only items keep the action (only swipe and icon
are off); `busy` disables it.

### Phone line (secondary line)

The attendee's phone number is a `tel:` link with a small icon in front of it:

| Caller | Icon (always) | Number and hint at rest | Number and hint while touched |
|---|---|---|---|
| Added by hand (no conversation) | phone, `--phone` green | `--misc` / `--faint` grey (like the Settings row on the main view) | `--phone` green |
| Booked in a conversation (`conversation_id`) | mic, `--conversations` blue | grey | `--conversations` blue |

"Touched" = the row is pressed (`:active`), keyboard-focused, or hovered by a real mouse
(`@media (hover: hover)`); on touch screens it ends the moment the finger is released. The number is
still a `tel:` link; pressing anywhere on the item runs its action ([ListItemAction](ListItemAction.md)).

The number is formatted like the numbers in Settings and on the main view (`fmtPhone`:
`+1 (555) 000-1111`; other numbers as given).

After the number comes a **hint**, set off by a `·` separator exactly like the subtitle of a main-view
control ("+1 (555) 000-1111 · Greeting cached"): `+1 (555) 000-1111 · See conversation` or
`… · Make a call`. See [ControlSubtitle](ControlSubtitle.md) for the shared style. The hint names the
item's primary action ([ListItemAction](ListItemAction.md)), is part of the pressable item, and takes the
same colour as the number while the item is touched (green, or blue for a conversation caller).

A caller from a conversation without a phone number shows the blue mic alone. There is no label next to
the name any more. Blue means "this came from a conversation" ([areas.md](areas.md)); the icon colours
deliberately override the Phone-green rule for numbers inside this row.

### States

- **Busy** (request running): swipe and primary action off, icon dimmed.
- **Read-only** (session ended): no swipe, no icon; the primary action still works.
- **Confirming**: shows [ItemRemoveConfirmation](ItemRemoveConfirmation.md) instead of the content.

## Not built yet

- Undo after removal (deliberately none; the confirmation replaces it).
- Swipe right action, reorder, multi-select.
- Focus trap in the sheet around the list.
- Real-device (iPhone) check of swipe vs scroll and tap.
