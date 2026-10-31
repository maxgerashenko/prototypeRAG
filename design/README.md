# Voice Chat Bot — design reference

The interactive design for the voice app at `/web/` (DEC-42). Use it as the reference when
implementing or changing `web/src/app/`.

| File | What it is |
|---|---|
| [`Main.dc.html`](Main.dc.html) | Full prototype source: every screen, color, sample data and interaction in one file. Read it as a spec — it does **not** run standalone (it needs the design canvas runtime, `./support.js`). |
| Live prototype | Claude Design canvas *Voice Chat Bot* — `https://claude.ai/artifact/39NZFVruiDY7NNH95uf6KR` (private; open it from the owner's account, press Play). |

Snapshot: 2026-10-31, synced with `web/src/app` at `146556a` plus the new Settings and
Bookings screens. When the design changes, replace `Main.dc.html` and update this file.

## How to read `Main.dc.html`

- Markup is between `<x-dc>` tags; `{{name}}` holes come from `renderVals()` in the
  `<script type="text/x-dc">` block at the bottom.
- Each screen is one `<sc-if value="{{isX}}">` block, marked with a comment
  `<!-- ============ N. NAME ============ -->`.
- `<sc-for list="{{items}}" as="item">` = a list; styles computed per item live in
  `renderVals()` (`cardStyle`, `monoStyle`, …).
- Sample data (businesses, conversations, questions, numbers, bookings) is generated in
  the script — it stands in for API data, it is not real.

## Tokens

Same values as `web/src/app/app.css` `:root` — keep them in one place.

| Token | Value | Use |
|---|---|---|
| `--accent` | `#8ab4f8` | Chat and **web calls** (globe icon), selection, links, NEW badge, user bubbles |
| `--call` | `#7fd6a8` | **Phone** (Twilio numbers, live phone calls), Live dot, push-to-talk while held, listening orb |
| `--bg` | `#0d0f14` | Screen background |
| `--card` / `--line` | `#161920` / `#262a33` | Cards and their borders |
| `--bubble` | `#1b1f27` | Bot bubble, idle push-to-talk |
| `--chip` / `--chip-text` | `#222733` / `#c9d3ff` | Monograms, small icon tiles |
| `--bot` | `#8aa4ff` | Bot avatar and speaking orb |
| `--danger` | `#d93d42` | End call |
| amber | `#e9c46a` | "Not cached" / "Unsaved changes" |
| red line | `#f28b82` | Current time in the bookings day view |

Text on accent/call fills is `--ink` `#0b1220`. Fonts: Manrope (UI), JetBrains Mono (numbers,
timers). Phone layout 390 × 844; touch targets ≥ 44 px.

## Screens

| # | Screen | In the app today | Notes |
|---|---|---|---|
| 1 | Pick a business | `SelectScreen.tsx` | **Change:** no radio/check mark and no blue border on the open card — the card only expands (chevron flips). Open card: `#181b22` background, monogram black `#07090c` with a 1.5 px accent border. List keeps its order and scrolls the open card to the top. |
| 1a | Open-card panel | `SelectScreen.tsx` (`.recent`) | **New:** two rows above *Recent conversations*: **Business settings** (hint: number · greeting cached?) and **Bookings** (hint: today's count) — both built. |
| 1b | Business settings | — **new** | See below. |
| 1c | Bookings (day view) | `BookingsScreen.tsx`, `MonthCalendar.tsx`, `BookingSheet.tsx` | Built. **Change:** the top is the Settings-style header (`Businesses` back row, monogram, h1 *Bookings*, `name · host`), not the `BOOKINGS · <business>` caption; the date bar sits below it. See below. |
| 2 | Previous conversations | `ConversationsScreen.tsx` | Matches. Header monogram is black with accent border. |
| 3 | Transcript | `TranscriptScreen.tsx` | Matches. |
| 4 | Connecting | `CallScreens.tsx` | Matches. |
| 5 | Live call / phone call (watch-only) | `CallScreens.tsx` | Matches; the call opens with the business's saved greeting. |
| 6 | Call ended | `CallScreens.tsx` | Matches. |

### 1b. Business settings (new)

Back button → list (the business stays open). Header: monogram (black + accent border),
"Settings", `name · category`. One scroll column of compact cards, in this order:

1. **Phone number** — one row: phone tile (green), number (mono), `area · Twilio · Voice & SMS`,
   **Change**. With no number: "No number assigned" + green **Choose**.
   - Choose/Change opens a bottom sheet: *Choose a number* — filter by area code or city,
     radio list of available Twilio numbers (number, city, Voice/SMS chips), numbers used
     by another business are hidden, **Assign** → spinner "Assigning…" → saved, sheet closes.
   - Open question vs. the app: the app has **one main number** that moves to the picked
     business (`POST /businesses/{id}/main-number`); the design also assigns a number
     **per business**. Decide which model wins (record a DEC) before building this card.
2. **First greeting** — title + status chip, 3-line textarea (max 300), footer
   `count/300 · note`, **Reset** (only when edited) and **Save & cache**.
   - Chip states: **Cached** (green, check) · **Not cached** (amber dot, "Generated live on
     each call") · **Unsaved changes** (amber, textarea border amber) · **Caching…**
     (spinner, while saving) → back to **Cached · just now**.
   - Backend: `app/voice/greeting_cache.py` already caches greeting audio per text/voice;
     the screen needs an editable greeting text per business and an endpoint that saves it
     and reports whether the audio row exists.
3. **Top questions** — header `N questions · M calls · 30 days`; top 5 rows: rank, question,
   percent, thin bar + `N× asked`. Tap a row to expand **Expected answer** (one open at a time,
   first open by default). Needs an aggregate over conversations (question clustering) — no
   API yet.

### 1c. Bookings — day view (built)

Back button → list. Header as on Settings (see the table); under it a bar with the date button (`Today, Oct 31` ▾), **Today**
(when not on today), ‹ › day arrows. Summary line `N bookings · K by assistant`.

- **Day column** like Google Calendar, one day, vertical scroll. Visible hours = **1 h before
  opening to 1 h after closing** (design: open 7 AM–11 PM → view 6 AM–12 AM); the edge hours
  are hatched (closed). Opens scrolled to the top.
- **Every booking is 1 hour and starts on the hour** — matches `Rules.slot_minutes = 60` in
  `app/actions/booking/rules.py`; take hours from the rules' `hours`.
- Block colors: green tint + mic icon = booked by the assistant on a call; blue tint = manual.
  Tap → bottom sheet: source, name, service, time, duration, Close.
- Red current-time line on today.
- **Swipe** left/right (horizontal, `touch-action: pan-y`) = next/previous day, with a short
  slide-in animation; a swipe must not also open the booking under the finger.
- Built with `GET /businesses/{id}/bookings` (one month per request, times in the business
  timezone as minutes since local midnight). "Today" and the red line follow the device clock.
  The day column opts out of swipe-back (`data-swipe-back="off"`); the header and bar still
  swipe back. Known limits: overlapping bookings are drawn on top of each other; the sheet
  shows only the design's fields (no phone/notes).
- **Mini calendar**: tapping the date opens a month grid inline — circles, selected day
  filled accent, today outlined accent, green dot = day has bookings; `«` `‹` `›` `»` =
  previous/next year and month; tapping a day jumps to it and closes the grid.

## Already in the app, not in the design

Pull-to-refresh, skeleton loaders, error banners, the hands-free *End call* button on
mobile, keyboard shortcuts (Space / Esc / Backspace). Keep them.
