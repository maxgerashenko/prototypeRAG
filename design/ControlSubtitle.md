# ControlSubtitle

The small grey second line (or trailing text) that explains or summarises a control, under or next to
its title. Parts are joined with a ` · ` separator, so one line carries several facts.

Examples:

| Where | Title | Subtitle |
|---|---|---|
| Main view, business | Business settings | `+1 (555) 000-1111 · Greeting cached` |
| Main view, business | Bookings | `Today: 3 booked` (just `Today` while loading) |
| Session attendee row | Name | `[icon] +1 (555) 000-1111 · See conversation` / `· Make a call` |

Code: `.set-row-hint` in `web/src/app/settings.css` with `settingsHint()` / `bookingsHint()`;
`.ss-line`, `.ss-sep`, `.ss-hint` in `web/src/app/bookings.css`.

## Rules

| Rule | Value |
|---|---|
| Size / colour | 12 px, `--faint` (= `--misc` grey) |
| Separator | ` · ` (middle dot with a space each side) between parts; never a comma, dash or pipe. In row markup it is its own `aria-hidden` element, `.ss-sep`, same size and colour |
| Order | the stable fact first (number, state), the action or extra after it |
| Overflow | one line, ellipsis; the title never shrinks |
| Touched | if the control is pressable and its title or number changes colour while touched, the subtitle parts that name its action follow the same colour (number and hint together) and go back on release |
| Wording | short, sentence case, no full stop; an action is a verb phrase ("Make a call", "See conversation") |
| Empty | an absent part is left out together with its separator; show nothing rather than "—" |

## Checklist

- [ ] Subtitle uses `--faint` at 12 px and a single line.
- [ ] Parts joined by ` · `.
- [ ] Phone numbers go through `fmtPhone`.
- [ ] Touched colour matches the number's, if the control has one.
