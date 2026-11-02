# Layer 7 — `web` (front end)

Read from `main` at `75d3bfd` (2026-11-02). Describes what exists; nothing was changed.
Layer map: `architecture/layers.md` in the project files. Sibling docs: [crawler.md](crawler.md),
[data.md](data.md), [voice.md](voice.md), [actions.md](actions.md),
[integrations.md](integrations.md), [api.md](api.md).

## 1. Purpose and boundaries

The owner's (and tester's) window into the system: pick a business, call its assistant from the
browser, watch real phone calls live, read past conversations, manage bookings/sessions, and edit
the business's greeting and phone number.

| | |
|---|---|
| Stack | React 19 + TypeScript, Vite multi-page build (DEC-41, supersedes DEC-19's HTMX) |
| Lives in | `web/` (code), `design/` (UI spec: pattern docs + `Main.dc.html` prototype) |
| Served by | FastAPI: `app.mount("/web", StaticFiles("web/dist"))` in `app/main.py`; `/` and the old `/web/mic-test.html` redirect to `/web/`. No separate frontend service. |
| Dev | `npm run dev` on :5173, proxies `/chat /health /dev /debug /businesses /twilio /voice` (+ WebSocket) to uvicorn :8000 (`web/vite.config.ts`) |
| Talks to | **Only the [api.md](api.md) layer**, over same-origin HTTP JSON, one WebSocket (`/voice/browser`) and two SSE streams (`/voice/feed`, `/dev/reload`). Plus the Twilio Voice SDK on the `call.html` debug page. |
| Does not | Hold business logic, call the LLM/STT/TTS, or reach the database. All rules (bookings, greeting disclosure, number moves) are enforced server-side; the front end only mirrors them for display. |

Rule: the web layer imports nothing from Python and knows the backend only through the shapes in
`web/src/api.ts`. A backend shape change means editing `api.ts` (and `e2e/mocks.ts`).

## 2. Pages (Vite entry points)

| URL | Entry | Purpose |
|---|---|---|
| `/web/` | `index.html` → `src/app/main.tsx` → `App.tsx` | **The voice app** (DEC-42): installable PWA, the only product page |
| `/web/chat.html` | `src/chat/ChatApp.tsx` | Debug: text chat by business UUID, shows sources and the full LLM prompt |
| `/web/voice-debug.html` | `src/voice-debug/VoiceDebugApp.tsx` | Debug: open mic, server VAD (`mode` not `ptt`), raw event log |
| `/web/call.html` | `src/call/CallApp.tsx` | Debug: real Twilio call from the browser via Voice SDK (mode B, DEC-34/43); only works on `localhost` (token route is loopback-only) |

## 3. Voice app: screens and components

`App.tsx` is a single state machine: one `Stage` on screen at a time (no router, no URL state).

```
select ──► settings | bookings | convos ──► transcript
   │                                           │ (Continue)
   └──► connecting ──► call ──► ended ◄────────┘
phone (watch-only Twilio call) ──► phoneEnded
```

| Stage | Component | Shows |
|---|---|---|
| `select` | `SelectScreen.tsx` | Business list with search, main Twilio number card (`tel:` link), expandable card per business: recent conversations, Settings row (greeting cached?), Bookings row (today's count), call button; error banner |
| `convos` | `ConversationsScreen.tsx` | All conversations of a business, NEW marks, source icon (phone/web), outcome badge; scroll kept per business |
| `transcript` | `TranscriptScreen.tsx` + `Bubbles.tsx` | One conversation's messages, call summary/outcome, "Continue" starts a call with that history |
| `connecting` / `call` / `ended` | `CallScreens.tsx` | Own browser call: status, live bubbles, hold-to-talk button (or hands-free on mobile), timer, end summary |
| `phone` / `phoneEnded` | `CallScreens.tsx` (`PhoneCallScreen`, `EndedScreen`) | A Twilio call the server is taking, live and watch-only (DEC-46); a floating green button reopens it when it's not on screen |
| `settings` | `SettingsScreen.tsx`, `NumberSheet.tsx` | Phone-number card + picker sheet (assign/move a Twilio number, DEC-52), first greeting editor with cached-audio status (DEC-53), top questions of 30 days |
| `bookings` | `BookingsScreen.tsx`, `MonthCalendar.tsx`, `SessionBlock.tsx`, `SessionSheet.tsx`, `BookingSheet.tsx` | Day view with booking/session/Google blocks, month calendar with dots, create/edit/delete group sessions, remove attendees, booking detail sheet |

Shared components: `ViewHeader` (monogram + title + grey subtitle + "Swipe to go back"),
`PullIndicator`/`PullRegion` (spinner coloured by the opening control), `StatusChip`,
`OutcomeBadge`, `SourceIcon`, `icons.tsx`. Styles: `app.css`, `settings.css`, `bookings.css`
(tokens per area, see `design/areas.md`, `design/colors.md`).

Input: keyboard (Space = call / hold to talk, Esc = back or end, Backspace = end, ←/→ = day in
Bookings), touch gestures (pull-to-refresh, swipe-right = back, swipe-left on a row = delete,
drag a sheet down = close). Each gesture is a pure reducer plus a hook with native listeners.

## 4. State and logic modules

Pattern: **pure module (unit-tested) + thin React hook**. No global store; `App.tsx` holds the
cross-screen state (businesses, conversations per business, NEW ids, selected business, banner).

| Pure logic (`src/app/`) | Hook / class | What |
|---|---|---|
| `callState.ts` — `callReducer` | `useVoiceCall.ts` → `src/voice/voiceCall.ts` | Turn phases (listening → thinking → speaking → idle), bubbles, latency, no-speech timeout. `VoiceCall` is a plain class owning `AudioContext`, mic worklet (16 kHz PCM16, 20 ms frames), WebSocket, playback queue — kept out of React state so frames don't re-render |
| `liveCalls.ts` — feed reducer | `useLiveCalls.ts` | Turns `/voice/feed` SSE messages into one `CallState` per phone call, **reusing `callReducer`**; feed open only while the tab is visible (DEC-35 cost) |
| `bookings.ts` — dates, hour ranges, month grid, block layout | `useBookings.ts` | Month-keyed cache, request counters drop stale answers, today's month loads as soon as a business is open, visible screen re-reads every 60 s, pull-to-refresh asks the server to sync Google first |
| `settings.ts` — greeting card states, number labels | `useBusinessSettings.ts` | Greeting loads when a business opens; questions when the screen opens; numbers when the sheet opens; request counters |
| `pullToRefresh.ts`, `swipeBack.ts`, `rowSwipe.ts`, `sheetDrag.ts` | `usePullToRefresh.ts`, `useSwipeBack.ts`, `useSheetDrag.ts` (rows use `rowSwipe` directly) | Gesture state machines |
| `format.ts` | — | Durations, dates ("Today · 9:14 AM"), initials, phone format |
| `mobile.ts` — `isMobileBrowser` | used by `useVoiceCall` | Mobile ⇒ hands-free (`mode=vad`), desktop ⇒ push-to-talk (`mode=ptt`) (#114) |

## 5. API endpoints per screen

All calls go through `src/api.ts` (except `call.html`'s `fetch("/businesses")` and the SSE/WS
openers). Server owners named per [api.md](api.md).

| Screen / module | Endpoint | Server file |
|---|---|---|
| App start / `select` | `GET /businesses`, `GET /twilio/number` | `app/api/businesses.py`, `app/voice/twilio_routes.py` |
| `select` (business opened), `convos` | `GET /businesses/{id}/conversations` | `app/api/conversations.py` |
| `transcript` | `GET /businesses/{id}/conversations/{cid}` | `app/api/conversations.py` |
| `call` (`voiceCall.ts`) | `WS /voice/browser?business_id=…[&mode=ptt][&continue_from=cid]` — PCM16 up, PCM16 + JSON `VoiceEvent`s down | `app/voice/browser_ws.py` |
| `phone` (`useLiveCalls`) | `GET /voice/feed` (SSE: `snapshot`, `call_started`, `call_event`, `call_ended`) | `app/voice/call_feed.py` |
| `settings` | `GET`/`PUT /businesses/{id}/greeting`, `GET /businesses/{id}/top-questions?limit&days`, `GET /twilio/numbers`, `PUT /businesses/{id}/number` | `app/api/business_settings.py`, `app/voice/twilio_numbers.py` |
| `bookings` | `GET /businesses/{id}/bookings?start&end[&sync=1]`, `POST /businesses/{id}/sessions`, `PATCH`/`DELETE …/sessions/{sid}`, `DELETE …/sessions/{sid}/attendees/{bid}` | `app/api/bookings.py`, `app/api/sessions.py` |
| `chat.html` | `POST /chat` | `app/api/chat.py` |
| `voice-debug.html` | `WS /voice/browser` (VAD mode) | `app/voice/browser_ws.py` |
| `call.html` | `GET /businesses`, `GET /twilio/token`; then Twilio → `POST /twilio/voice` → `WS /voice/ws` (server side, not the page) | `app/voice/twilio_routes.py`, `app/voice/ws.py` |
| every page (`dev-reload.js`) | `GET /dev/reload` (SSE, when `DEV_RELOAD=true`), `GET /health` | `app/api/dev_reload.py`, `app/main.py` |

`setMainNumber` (`POST`/`DELETE /businesses/{id}/main-number`) is still in `api.ts` and tested,
but no screen calls it any more (number assignment moved to `PUT …/number`, DEC-52).

## 6. PWA / iPhone install

- `public/manifest.webmanifest`: `id`/`start_url`/`scope` = `/web/`, `display: standalone`,
  portrait, dark theme `#0d0f14`, 192/512/maskable icons.
- `index.html`: `apple-touch-icon`, `apple-mobile-web-app-capable`, status bar
  `black-translucent`, title "Voice", `viewport-fit=cover`, `format-detection=telephone=no`.
- `public/sw.js` (cache `voice-shell-v1`, registered in production builds only, scope `/web/`):
  network-first for the `/web/` page with an offline fallback, cache-first for hashed
  `/web/assets/*`, icons and the manifest. Never touches the API, WebSocket, SSE, debug pages or
  `dev-reload.js`. Bump `CACHE` when the strategy changes.
- Mobile behaviour: hands-free calls, touch gestures, `tel:` link on the main number card.
- Installing on an iPhone needs HTTPS (ngrok today, Cloud Run in stage 3).

## 7. Tests

| Kind | Command | Where | Now |
|---|---|---|---|
| Unit (vitest) | `npm test` | `src/**/*.test.ts` — pure modules and `api.ts` (11 files) | 195/195 pass (run 2026-11-02 on this box); `tsc -b` clean |
| Browser (Playwright) | `npm run e2e` | `e2e/*.spec.ts` (10 specs, ~177 tests), one "phone" project at 390×844, Chromium fake mic, UTC | Not run here. Backend fully mocked in `e2e/mocks.ts` (HTTP routes + scripted `/voice/browser` WebSocket), so it needs no Postgres/LM Studio/Google |
| Manual | "Test on Mac" list in `plan/03-voice-channel.md` | real mic, real Twilio call, iPhone install | Only on Max's Mac |

No CI runs either suite.

## 8. Key files

| File | Why it matters |
|---|---|
| `web/src/app/App.tsx` | Stage machine, keyboard, gesture gating, auto-switch to live phone calls |
| `web/src/api.ts` | The contract with the backend: every request and JSON shape |
| `web/src/voice/voiceCall.ts` | Browser audio + `/voice/browser` protocol (shared by app and voice-debug) |
| `web/src/app/callState.ts` | Call turn logic, reused for watched phone calls |
| `web/src/app/useBookings.ts` | The largest data hook (caching, refresh, Google sync) |
| `web/vite.config.ts` | Pages, `base: /web/`, dev proxy |
| `web/public/sw.js`, `manifest.webmanifest` | Installable app |
| `web/e2e/mocks.ts` | Mock backend for browser tests; must follow `api.ts` |
| `design/README.md` + pattern docs | UI spec; `Main.dc.html` may be outdated (CLAUDE.md) |

## 9. Known gaps

From `reviews/front-end-review.md` (2026-11-01, project files), re-checked against `75d3bfd` where noted.

| # | Gap | Stage |
|---|---|---|
| G1 | **No access control.** Every screen and API route trusts the `business_id`; with ngrok up, anyone with the URL can watch live calls, read conversations, move numbers, change the greeting (F1, OPEN-19). No `Origin` check on `/voice/browser` (F2). Fix is server-side ([api.md](api.md)) | before a public URL |
| G2 | A live phone call auto-switches away from Settings and drops an unsaved greeting draft (F3). Still true: the switch only skips `connecting`/`call`/`phone` | 1 |
| G3 | e2e was 4/122 red on 2026-11-01 (stale tests, F4); no CI, so it can drift unnoticed | 1 |
| G4 | Debug pages and the full LLM prompt ship in the production build; `dev-reload.js` loads in production too (F8, F13) | before a public URL |
| G5 | `call.html` Voice SDK token is never refreshed after 1 h (F7) | 1 |
| G6 | `VoiceCall.start()` resolves before the socket is open; 16 kHz `AudioContext` may not be honoured on every browser (F9, F10) | 1 |
| G7 | Dead code: `setMainNumber` and the `/main-number` routes (F12); two phone formats (F11) | 1 |
| G8 | Ended phone calls stay in memory on a long-open tab (F14); Google Fonts not available offline (F15); a11y nits (F16) | 1–2 |
| G9 | No URL routing: reload or a shared link always lands on the business list | open |
| G10 | Live feed is in-memory on one instance; several Cloud Run instances need Postgres LISTEN/NOTIFY (DEC-46) | 3 |
| G11 | Single-owner view of all businesses; per-owner scoping (OPEN-12) and login are not designed yet | 2–3 |

## 10. Depends on / Used by

**Depends on**

| Layer | How |
|---|---|
| [api.md](api.md) | The only direct dependency: every HTTP route, `/voice/browser` WebSocket, `/voice/feed` and `/dev/reload` SSE, and FastAPI serving `web/dist` |
| [voice.md](voice.md) | Indirect, through `/voice/browser` and `/chat`: the turn events (`transcript`, `reply`, `latency`, `no_speech`, `hangup`), push-to-talk vs VAD, greeting text with the DEC-53 disclosure |
| [actions.md](actions.md) | Indirect (what the app does: booking, texting, transferring, summarizing): bookings/sessions data, attendee removal texts (SMS counts in delete results), call summaries (`summary`, `intent`, `outcome`, `follow_up`) |
| [data.md](data.md) | Indirect: businesses, domains, default location, timezone and booking hours, top questions (insights), `sources` and prompt on the chat debug page |
| [integrations.md](integrations.md) | Indirect (what lets the app work: Twilio and other external APIs): Twilio number list, Google Calendar sync (`sync=1`). Direct only on `call.html`, which loads the Twilio Voice SDK (`@twilio/voice-sdk`) in the browser |
| [crawler.md](crawler.md) | None. Businesses only appear after a crawl, but the web layer never triggers or reads a crawl |

**Used by**: nobody inside the system — it is the top layer. People use it: Max as owner/tester
(desktop and iPhone PWA).
