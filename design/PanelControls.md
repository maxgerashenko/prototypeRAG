# PanelControls

The big tappable rows at the top of a business's **open card** on the Pick-a-business screen. Each one
opens a whole screen for that business. Today: **Business settings**, **Bookings**, **Conversations**.
They sit above the "Recent conversations" list and are all the same control with a different icon,
area colour and hint. Found by comparing the three that were built one after another.

Code: `.set-row*` in `web/src/app/settings.css`; rows in `web/src/app/SelectScreen.tsx` (`.recent`);
hints `settingsHint()` (settings.ts), `bookingsHint()` (bookings.ts), `conversationsHint()` (format.ts);
`ChatCountIcon` in `icons.tsx`. Hint text follows [ControlSubtitle.md](ControlSubtitle.md).

## Anatomy

| Part | Rule |
|---|---|
| Row | Full width, min-height 52 px, padding 8 px, 6 px gap below, 1 px `#2a2f3a` border, radius 14 px, background `#12151b`; one `<button>` |
| Icon tile | 32 × 32, radius 10 px, on the left; glyph 16–20 px in the **area colour** on that area's fill |
| Title | 14 px / 700, `--text`, one line; names the place it opens ("Business settings", "Bookings", "Conversations") |
| Hint | 12 px, `--faint`, one line, ellipsis; `·`-separated facts, most useful first; never a sentence |
| Chevron | `ChevronRight`, grey `--misc` (not area colour) |
| Pressed / focus | Background `#161a21` on `:active` and `:focus-visible`; `:hover` only inside `@media (hover: hover)` so it does not stick after a touch |

## Icon tile by area ([areas.md](areas.md))

| Control | Tile background | Glyph | Content in the tile |
|---|---|---|---|
| Business settings | `--chip` | `--misc` grey | gear |
| Bookings | `--bookings-fill` | `--bookings` | calendar |
| Conversations | `--conversations-tint` | `--conversations` | speech bubble with the **number of conversations inside** |

- A number goes **inside the icon** when the control is about a count (`ChatCountIcon`): 1–99 as is,
  above 99 `99+`, font shrinks with the digits. While the count is unknown or failed to load, the bubble
  is empty. Bookings and Settings keep their count/status in the hint instead.
- A new control picks the area of what it opens. No area fits: grey `--chip` / `--misc` like Settings.

## Hints so far

| Control | Hint |
|---|---|
| Business settings | `(415) 555-0100 · Greeting cached` — number first, then the greeting state |
| Bookings | `Today · 3 bookings` / `Today · no bookings` / `Today` while unknown |
| Conversations | `All 5 conversations` / `None yet` |

## Behaviour

- Tap opens the target screen; back returns to the list with the same business still open.
- Order: Settings, Bookings, Conversations, then the "Recent conversations" header and its rows. The
  recent rows are [ListItem](ListItem.md)s, not PanelControls (smaller, no border).
- There is no "See all" link under the recent rows: the Conversations control is the only way to the full list.
- The "Recent conversations" caption above the rows is a plain subtitle (11 px, weight 400, `--faint`, no count).

## Do not

- Put a status chip or a second line of hint in the row ([StatusChip.md](StatusChip.md) belongs inside the opened screen).
- Colour the whole row with an area colour; only the icon tile carries it.
- Use hover styles without `@media (hover: hover)`.

## Checklist for a new control

- [ ] Same `.set-row` markup: tile, `.set-row-text` (title + hint), `ChevronRight`; `data-testid="<name>-row"`.
- [ ] Tile class per area (`.set-row-icon.<area>`); add the colours to [colors.md](colors.md) only if new.
- [ ] Hint function with a unit test, written as in [ControlSubtitle.md](ControlSubtitle.md).
- [ ] e2e: row visible in the open card, hint text, tap opens the screen.
