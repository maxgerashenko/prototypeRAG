# Colors

The one place that says which color means what. It mirrors the `:root` block of
`web/src/app/app.css` token for token, so the two never disagree. Components and pattern docs
refer to a **token** from this file, never to another component ("same as the Delete button").
To change or add a color: edit `:root` in `app.css` and this file together. No hex literals in
component CSS.

Dark theme only.

Which area owns which color, and how to mark things from another area: [areas.md](areas.md).

## Tokens (all of `:root`)

Every color is a hex value (alpha as 8-digit hex, e.g. `#a56bf726`); no `rgba()` or `color-mix()`
in the CSS. Tokens are named by [area](areas.md).

### Areas

| Token | Value | Role / use |
|---|---|---|
| `--assistant` | `#a56bf7` | Assistant (strong purple): bot avatar, orb, bookings made by the assistant |
| `--assistant-light` | `#e3d1fb` | Bot status text |
| `--assistant-tint` / `-fill` / `-line` / `-text` | `#a56bf72e` / `26` / `61` / `#e6d0fb` | Label background (18%), block fill (15%), block border (38%), block text |
| `--bookings` | `#b9a2ff` | Bookings (light purple): bookings, sessions, calendar today / selected / dots, add `+`, "booked" badge, Bookings row icon |
| `--bookings-fill` / `-line` / `-badge` / `-hover` / `-text` | `#b9a2ff26` / `61` / `4d` / `14` / `#ece6ff` | Block fill (15%), border (38%), FULL badge (30%), hover (8%), text on those |
| `--phone` | `#7fd6a8` | Phone (green): numbers and phone links, live calls, Live dot, push-to-talk while held |
| `--phone-glow` / `--phone-bg` | `#7fd6a82e` / `#7fd6a81f` | Phone at 18% / 12% |
| `--conversations` | `#8ab4f8` | Conversations (blue): chats, web calls, transcripts, links, user bubbles, NEW badge, the "Voice assistant" eyebrow on the voice app |
| `--conversations-tint` / `-light` | `#8ab4f81a` / `#bcd3fb` | Conversations at 10%; link hover |
| `--misc` | `#8a919d` | Misc (grey): `>` chevrons, remove circle-x, Settings row icon, captions |

### Surfaces and text

| Token | Value | Role / use |
|---|---|---|
| `--bg` | `#0d0f14` | Screen background |
| `--card` | `#161920` | Cards, panels |
| `--line` | `#262a33` | Card and input borders |
| `--divider` | `#1c2029` | Hairlines inside a card |
| `--bubble` | `#1b1f27` | Bot bubble, idle push-to-talk |
| `--chip` | `#222733` | Monograms, small icon tiles |
| `--chip-text` | `#c7ccd6` | Text on a chip |
| `--inset` / `--inset-line` | `#0f1217` / `#1f232c` | Rows and boxes inside sheets and cards; their borders; disabled-button fill |
| `--text` | `#f2f3f5` | Primary text |
| `--muted` | `#9aa1ad` | Secondary text |
| `--faint` | `var(--misc)` | Tertiary text, captions |
| `--ink` | `#0b1220` | Text on an area-colored fill |

### Status (any area)

| Token | Value | Role / use |
|---|---|---|
| `--danger` | `#d93d42` | Destructive outlines and the End-call fill |
| `--danger-text` | `#f28b82` | Text and icons of a destructive action; the "now" line |
| `--danger-tint` | `#331a1f` | Reveal block fill (danger 18% over `--inset`) |
| `--amber` / `--amber-bg` | `#e9c46a` / `#e9c46a1f` | Warning: "Not cached", "Unsaved changes" |

### Old names (aliases, do not use in new code)

`--accent` → `--conversations`, `--tint` → `--conversations-tint`, `--call` → `--phone`,
`--glow` → `--phone-glow`, `--call-bg` → `--phone-bg`, `--bot` → `--assistant`,
`--bot-light` → `--assistant-light`, `--session` → `--bookings`.

## Rules

- Meaning is fixed per area (see [areas.md](areas.md)): purple (strong) = assistant, purple (light) =
  bookings, green = phone, blue = conversations, grey = misc; red = destructive or "now"; amber = warning.
  Do not reuse an area color for decoration.
- A destructive action uses `--danger-text` on a `--danger` outline (button) or on `--danger-tint`
  (reveal blocks). Solid `--danger` is for the single most final action (End call).
- Write colors as hex. Area colors come from tokens; neutral greys used once may stay as hex in the CSS.
- Text on colored fills is `--ink`.
- Disabled = the same element at 50% opacity, not a new gray.
