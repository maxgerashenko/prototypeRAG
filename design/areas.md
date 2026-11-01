# Areas

The app is a set of **areas** (rooms). Each area owns one colour. When one area shows something that
belongs to another, it marks that thing with the *other* area's colour, so a user can read where it
comes from. Colours are named by area, never by hue ("Assistant", not "periwinkle"). Token values live
in [colors.md](colors.md) and `:root` of `web/src/app/app.css`.

## The areas

| Area | Colour | What lives there | Token |
|---|---|---|---|
| **Assistant** | strong purple `#a56bf7` | The voice assistant itself: bot avatar, orb, speaking status, anything *done by* the assistant | `--assistant` |
| **Bookings** | light purple `#b9a2ff` | Bookings, group sessions, the day view, the month calendar, the add `+`, "booked" outcome | `--bookings` |
| **Phone** | green | Phone numbers, live calls, Twilio source, push-to-talk while held | `--phone` |
| **Conversations** | blue | Chats, transcripts, web calls, user bubbles, NEW badge | `--conversations` |
| **Misc** | grey | Neutral controls and hints: `>` chevrons, the remove circle-x, captions | `--misc` |

Not areas, status colours that can appear in any area: **danger** red (destructive, End call, the
"now" line) and **warning** amber (unsaved, not cached). Surfaces (`--bg`, `--card`, `--inset`, lines)
and text greys belong to no area.

## Rules for crossing areas

1. A screen keeps its own area's colour for its structure (headers, selection, primary fill).
2. A thing that belongs to another area wears that area's colour on a small mark only: a label,
   icon, dot or text, never the whole screen.
3. Who made it decides the colour of a booking: made by the assistant → **Assistant** colour; made by
   hand → **Bookings** colour.
4. A phone number is always **Phone** colour wherever it appears (including inside Bookings).
5. A link to a conversation or a chat is **Conversations** colour.
6. Neutral affordances (chevrons, remove x, drag grip) are **Misc** grey in every area.
7. Never reuse an area colour for decoration.

Example (Bookings screen):

| Element | Area it belongs to | Colour |
|---|---|---|
| Booking block made by hand | Bookings | light purple |
| Booking block made by the assistant and its mic icon | Assistant | strong purple |
| Group session block, add `+` | Bookings | light purple |
| Caller's phone number | Phone | green |
| "Open conversation" link | Conversations | blue |
| `>` chevrons, remove circle-x | Misc | grey |

## Where the code differed (analysis; items 1-3, 5-8, 10-12 are applied, 4 and 9 remain)

| # | Today | Rule broken | Proposal |
|---|---|---|---|
| 1 | `--bot` `#8aa4ff` and `--accent` `#8ab4f8` are almost the same blue | Assistant and Conversations cannot be told apart | Move Assistant to light purple (e.g. `#a56bf7`); Conversations keeps blue |
| 2 | `.bk-block.ai` (assistant booking) is **green** with literal rgba; `BookingSheet` "Booked by assistant" uses `--call` | Rule 3: assistant-made = Assistant colour; green means phone | Assistant colour |
| 3 | `.bk-block.manual` is **blue** with literals | Blue means Conversations | Bookings strong purple |
| 4 | Group session block is `--session` violet, close to the future Bookings colour | Sessions and manual bookings would share a colour | Both are Bookings; keep telling them apart by the people icon and the count line (or a lighter tint for sessions; decide when #3 is done) |
| 5 | `--session` is a mid violet `#c58af9`, lighter than "strong" | Not distinct from the Assistant light purple once #1 is done | Make Bookings a stronger purple (e.g. `#b9a2ff`), Assistant a lighter lavender; check contrast of text on the 15% tint |
| 6 | Calendar today/selected day (`bookings.css` `.bk-cal-day`), outcome badge `.outcome-booked` use `--accent` | Bookings things in the Conversations colour | Bookings colour |
| 7 | `.ss-who a` (attendee phone link) uses `--accent`; elsewhere `.num-link` is `--call` | Rule 4 | Phone colour; then the attendee row overrides it: the phone icon is always green and the mic icon always blue, the number is grey (Misc) and takes its icon's colour only while touched |
| 8 | Global `a { color: var(--bot) }` | Links look like the Assistant | Links use Conversations blue, or Misc grey for neutral ones |
| 9 | `--accent` is also focus border, primary buttons, progress bars, "saving" chip, the web-call button | One token carries many meanings | Keep `--accent` as the Conversations colour; give generic focus/primary a neutral treatment (or a separate `--focus`) when touched |
| 10 | `--chip-text` `#c9d3ff` (monograms) is a pale blue-violet | Looks like Assistant/Conversations | Misc grey-white |
| 11 | Hex/rgba literals in component CSS: `bookings.css` (`#c3edd7 #cfddfb #e6d0fb #f1e3fd #2e333e`, rgba blocks), `settings.css` (`#13231b`, `rgba(127,214,168,.10)`) | colors.md: "no hex literals in component CSS" | Replace with area tokens plus `color-mix` |
| 12 | Tokens named by hue or by use (`--accent`, `--call`, `--session`, `--bot`, `--glow`, `--tint`, `--call-bg`) | Names do not say which area | Rename (table below) |

Also applied: the "Voice assistant" eyebrow at the top of the voice app (mic + text) is Conversations blue; the remove circle-x, `>` chevrons and the business Settings row icon are Misc grey; the
Bookings row icon on the main view is Bookings purple; in the session sheet an attendee has a green phone icon (added by hand) or a blue mic icon (booked in a conversation), always,
and the grey number turns that colour only while the row is touched (no label; an exception to rule 4:
Phone green). Open: #4 (manual booking and session share a colour, told apart by icon) and
#9 (`--conversations` still also draws generic focus borders, primary buttons and progress bars).

### Proposed token names

| Now | Proposed |
|---|---|
| `--bot`, `--bot-light` | `--assistant`, `--assistant-light` |
| `--session` | `--bookings` |
| `--call`, `--glow`, `--call-bg` | `--phone`, `--phone-glow`, `--phone-bg` |
| `--accent`, `--tint` | `--conversations`, `--conversations-tint` |
| `--faint` (icons use) | `--misc` (alias of `--faint`) |
| `--danger`, `--danger-text`, `--amber`, `--amber-bg` | unchanged (status) |

Suggested order: (1) tokens renamed with old names kept as aliases, (2) fix #2, #3, #6, #7 (visible
inconsistencies), (3) recolour Assistant and Bookings (#1, #5), (4) remove literals (#11). Colour values
here are proposals; check contrast on the dark theme before applying.
