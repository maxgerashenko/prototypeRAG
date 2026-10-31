# Feature: Business settings screen

Open business card gets a **Business settings** row → Settings screen with Phone number, First greeting and Top questions (design `design/README.md` 1a/1b). Issues #144 (screen), #145 (greeting), #146 (top questions), #147 (number). Milestone S1 · M5 Actions. Bookings (1c) is out of scope.

Decisions: [DEC-52](DECISIONS.md) (numbers assigned per business, taken numbers shown with *Move*), [DEC-53](DECISIONS.md) (fixed AI-disclosure sentence + owner text, "Hi" if empty). Implementer: planned as mixed, but the user ruled out local models for this feature (2026-10-31): **all QWEN steps go to `Agent_sonnet5.5_CODE`**; review stays `sonnet5.5_REVIEW`, plan/PR words by Claude.

Stage 1 simplest: no migration (greeting in `businesses.settings` JSONB, numbers in `businesses.phone_numbers`); top questions computed on request with `app.llm.embed`; numbers only from those the Twilio account owns.

## Done when

- [ ] Open card shows a "Business settings" row above *Recent conversations*, hint `<number or "No number"> · Greeting cached|not cached`.
- [ ] Settings opens with Back, swipe-back and Esc (Esc closes the sheet first). The business stays open on the list.
- [ ] Greeting: `GET/PUT /businesses/{id}/greeting`. Save synthesizes both sample rates, chip ends "Cached · just now", old audio pruned. TTS failure keeps the text, `cached:false` + error shown. Over 300 characters → 422 under the field. Hint under the input: calls start with "Hi, I am an AI assistant." then the text; empty text → the sentence alone.
- [ ] A web call and a phone call open with that greeting from cache.
- [ ] Top questions: `GET /businesses/{id}/top-questions` → `total_questions, conversations, days, questions[]` (top 5, `count/percent/answer`); near-duplicates merge; empty state; 503 when embeddings are down.
- [ ] Numbers: `GET /twilio/numbers`, `PUT /businesses/{id}/number`; list updates without reload; opening a card no longer posts `/main-number`.
- [ ] `uv run pytest -q`, `npm run typecheck`, `npm test`, `npm run e2e` green.
- [ ] USER: live greeting on a browser call and the trial number; real number list; assign and dial.

## Steps

| # | Step | Executor | Alt | Files | Check |
|---|---|---|---|---|---|
| 0 | - [x] Record DEC-52/53, this plan, README link; comment on #144–#147; `LOCAL_USER_PHONE` on #145 | CLAUDE | - | plan/DECISIONS.md, this file, README.md, design/README.md | grep DEC-52 |
| 1 | - [ ] Greeting resolver, cache status, prune | QWEN | SONNET | app/voice/greeting_cache.py | `uv run pytest -q tests/test_voice_session.py` |
| 2 | - [ ] Turn pairing + greedy cosine clustering + medoid | QWEN | SONNET | app/insights/__init__.py, app/insights/questions.py | step 3 |
| 3 | - [ ] Unit tests for step 2 | QWEN | SONNET | tests/test_top_questions.py | `uv run pytest -q tests/test_top_questions.py` |
| 4 | - [ ] Twilio number list + `GET /twilio/numbers` | QWEN | SONNET | app/voice/twilio_numbers.py | step 5 tests |
| 5 | - [ ] Settings API, greeting in calls, routers, API tests | SONNET | - | app/api/business_settings.py, app/main.py, app/voice/session.py, tests/test_business_settings.py | `uv run pytest -q` |
| 6 | - [ ] Tune `is_question` + threshold on pilot data; note values here | CLAUDE | - | app/insights/questions.py | curl top-questions gives sensible groups |
| 7 | - [ ] Types + fetchers | QWEN | SONNET | web/src/api.ts | `npm run typecheck` |
| 8 | - [ ] `GearIcon`, `CloseIcon` | QWEN | SONNET | web/src/app/icons.tsx | typecheck |
| 9 | - [ ] Pure helpers | QWEN | SONNET | web/src/app/settings.ts | step 10 |
| 10 | - [ ] Vitest for step 9 | QWEN | SONNET | web/src/app/settings.test.ts | `npm test` |
| 11 | - [ ] Styles | QWEN | SONNET | web/src/app/settings.css | `npm run build` |
| 12 | - [ ] Number picker sheet | QWEN | SONNET | web/src/app/NumberSheet.tsx | typecheck |
| 13 | - [ ] Settings screen | QWEN | SONNET | web/src/app/SettingsScreen.tsx | typecheck |
| 14 | - [ ] Settings row on open card; header note | QWEN | SONNET | web/src/app/SelectScreen.tsx | typecheck + test |
| 15 | - [ ] `useBusinessSettings` hook + App wiring; remove auto-move | SONNET | - | web/src/app/useBusinessSettings.ts, App.tsx | typecheck + test |
| 16 | - [ ] E2E mocks + spec; update main-number test | SONNET | - | web/e2e/mocks.ts, settings.spec.ts, app.spec.ts | `npm run e2e` |
| 17 | - [ ] Live checks (greeting on browser/phone call, real numbers, assign and dial, phone layout) | USER | - | - | write result here and on #160 |
| 18 | - [ ] Live check that changing the number really works: picker lists the real Twilio numbers, assign a free number and dial it, Move from another business and dial, Voice URL still points at the app, failure shows the error ([#160](https://github.com/maxgerashenko/prototypeRAG/issues/160)) | USER | - | - | write result here and on #160 |

## Specs

Each qwen spec: paste the current file, return the full file, change nothing else.

### Step 1 — app/voice/greeting_cache.py
- `MAX_GREETING_LEN = 300`; `AI_DISCLOSURE = "Hi, I am an AI assistant."`
- `def business_greeting(business, profile) -> str`: owner text = `(business.settings or {}).get("greeting")` stripped if a non-empty str, else `"Hi"`; returns `f"{AI_DISCLOSURE} {owner}"`. (The standard `greeting_text(voice_name(...))` is no longer used for the opening; keep the function.)
- `pregenerate`: text = `business_greeting(business, session.get(BusinessProfile, business_id))`.
- `cache_status(business_id, text, rates=SAMPLE_RATES) -> tuple[bool, datetime | None]`: in one `tenant_session`, select `GreetingAudio.created_at` for business, text, `settings.tts_voice`, `settings.voice_language`, `sample_rate IN rates`; cached = a row for every rate; cached_at = latest.
- `prune(business_id, keep_text) -> int`: delete this business's rows whose `text != keep_text`, return count.

### Step 2 — app/insights/questions.py (pure, numpy, no I/O)
- `Turn(question, answer|None, conversation_id, at)` frozen dataclass.
- `pair_turns(rows)`: rows `(conversation_id, role, content, created_at)` ordered; each `user` row → Turn; answer = first `assistant` row after it in the same conversation before the next `user`; other roles ignored.
- `MIN_WORDS = 4`; `is_question(text)`: non-empty, ≤300 chars, and ends with "?" or has ≥ MIN_WORDS words.
- `DEFAULT_THRESHOLD = 0.80`; `cluster(vectors, threshold)`: L2-normalise (zero vector stays alone), walk in order, join the group whose leader has highest cosine ≥ threshold else new group; index lists in creation order.
- `medoid(vectors, idxs)`: highest summed cosine to the others; ties earliest.
- `top_questions(turns, vectors, limit=5, threshold)`: length mismatch → ValueError; per group `question` = medoid turn text stripped of trailing "?.!" plus one "?", `count`, `percent = round(100*count/len(turns))`, `answer` = medoid's answer else most recent answered turn's else None; sort count desc then latest `at` desc; dict keys `question, count, percent, answer`.

### Step 3 — tests/test_top_questions.py
Hand-made 3-D vectors, no DB: pair_turns (answers matched, two user rows in a row, no leak across conversations, other roles ignored); is_question ("yes", "ok thanks", 4-word sentence, "Open?", 301 chars); cluster (merge, split, inclusive threshold, zero vector, empty); medoid; top_questions (75/25 percents, ordering and recency tie-break, answer fallback, limit, mismatch raises).

### Step 4 — app/voice/twilio_numbers.py
- `account_numbers() -> list[dict]`: missing `twilio_account_sid`/`twilio_auth_token` → `RuntimeError("TWILIO_ACCOUNT_SID / TWILIO_AUTH_TOKEN are not set")`; else `twilio.rest.Client(...).incoming_phone_numbers.list(limit=100)` mapped to `{number, friendly_name, voice, sms, routed}` (`routed` = voice_url path ends with `/twilio/voice`).
- `holders(numbers) -> dict[str, tuple[uuid, str]]`: `SessionLocal()`, businesses (outer join profile) → number → `(id, display_name)` using `display_name` from `app.api.businesses`.
- `GET /twilio/numbers`: on exception log a warning and return `{"numbers": [], "error": str(exc)}`; else add `business_id`/`business_name`, sort by number, `error: None`. Docstring: reads across tenants like `app/api/businesses.py` (RLS-exempt path in stage 3).

### Step 5 — backend API + session (Sonnet)
New `app/api/business_settings.py`, registered in `app/main.py` with step 4's router.
- `GET /businesses/{id}/greeting` → `{text, is_default, cached, cached_at, max_length: 300}` where `text` is the **owner part** only (or "" when default); the full spoken text is `business_greeting`. 404 unknown business.
- `PUT /businesses/{id}/greeting` body `{text}` stripped; empty removes `settings.greeting`; >300 → 422 (string detail). Assign a **new dict** to `business.settings` (JSONB in-place mutation is not tracked; keep other keys). Then `pregenerate` (TTS failure caught → `cache_error`), then `prune(business_id, full_text)`. Response = GET shape + `cache_error`.
- `GET /businesses/{id}/top-questions?limit=5&days=30` (limit 1–20, days 1–365): tenant-scoped messages (user/assistant) of the business's conversations in range, ordered by conversation then `*message_order()`; `pair_turns` → keep `is_question` → `app.llm.embed` in batches of 64 → `top_questions`. Returns `{total_questions, conversations, days, questions}`; no turns → zeros without calling embed; embed error → 503 "embedding server unavailable".
- `PUT /businesses/{id}/number` body `{number}`: 404 unknown; `account_numbers()` error → 503; number not on account → 422; one transaction: `array_remove` from every other business (pattern of `assign_main_number`) and set `phone_numbers = [number]`. Returns `{business_id, phone_numbers, moved_from}`. Keep `/main-number` endpoints and tests.
- `session.py`: opening greeting (not "Welcome back") from `greeting_cache.business_greeting(business, profile)`; `_speak_fixed` cache key path unchanged.
- Tests (dev-DB fixtures as `tests/test_twilio.py`): greeting default/custom/cached/empty reset/301 → 422/TTS failure keeps text/old rows pruned/other `settings` keys survive; session speaks custom greeting; top-questions empty, merge + percent, 503; `/twilio/numbers` missing creds and holders; PUT number moves it and `/twilio/voice` `To` reaches new business, unknown number 422, no creds 503.
- Constraints: every tenant query via `tenant_session` + `business_id`; no migration; never commit; not against pilot data.

### Step 7 — web/src/api.ts
Section `// --- business settings (app/api/business_settings.py, app/voice/twilio_numbers.py) ---`. Types `GreetingStatus {text,is_default,cached,cached_at,max_length,cache_error?}`, `TopQuestion {question,count,percent,answer}`, `TopQuestions {total_questions,conversations,days,questions}`, `TwilioNumber {number,friendly_name,voice,sms,routed,business_id,business_name}`, `TwilioNumbers {numbers,error}`, `AssignResult {business_id,phone_numbers,moved_from}`. Private `sendJson<T>(url, method, body)` (non-ok → `Error(detail)` if string else `HTTP status`), `getGreeting`, `saveGreeting`, `getTopQuestions(id, limit=5, days=30)`, `listTwilioNumbers`, `assignNumber(id, number)`; `encodeURIComponent` ids; update file-top comment.

### Step 8 — icons.tsx
`GearIcon({size=16})` with the design's gear (circle r=3 + path, copy from `design/Main.dc.html` line 90); `CloseIcon({size=18})` two lines.

### Step 9 — web/src/app/settings.ts (pure)
`greetingView({saved,draft,cached,saving,cachedAt,now?})` → `{kind: saving|dirty|cached|notCached, chip, note, saveLabel, saveDisabled, dirty, count}` (precedence saving > dirty > cached > notCached; chips "Caching…"/"Unsaved changes"/"Cached"/"Not cached"; notes "Generating audio…"/"Save to re-cache"/"Cached "+cachedLabel/"Generated live on each call"; save labels "Saving…"/"Save & cache"/"Saved"/"Cache greeting"; `saveDisabled = saving || (!dirty && cached)`; empty draft is allowed — it resets to the sentence alone). `cachedLabel(iso, now)` ("" / "just now" under 60 s / "Oct 28, 4:12 PM"). `settingsHint(phoneNumbers, cached)`. `capsLabel`, `numberSubtitle`, `filterNumbers(list, query, bizId)` (digit query matches number digits, else friendly_name; order: own, free, taken), `assignLabel(pick, currentNumber, assigning)` ("Assigning…"/"Select a number"/"Currently assigned"/"Move from X"/"Assign +1 …"), `canAssign`, `barWidth`. `GREETING_PREFIX = "Hi, I am an AI assistant."` exported for the hint.

### Step 10 — settings.test.ts
Vitest as `format.test.ts`: every greetingView state, cachedLabel, settingsHint, capsLabel, numberSubtitle, filterNumbers ordering, assignLabel (5 cases), barWidth clamp.

### Step 11 — settings.css
Classes and values from `design/Main.dc.html` lines 88–335 using `:root` tokens of `app.css`: `.set-row*` (incl. `.set-row-text`), `.screen-settings` (position relative), `.set-head`, `.set-mono`, `.set-scroll`, `.set-card`, `.num-*`, `.btn-outline/-call/-accent`, `.chip` + `-cached/-warn/-saving`, `.spin`, `.greet-*` (+ `.greet-hint`), `.q-*`, sheet classes (`.sheet-*`, `.num-opt`, `.num-radio`, `.cap-chip`, `.num-taken`), keyframes `vw-grow/sheet/fade`.

### Step 12 — NumberSheet.tsx
`NumberSheet({bizId, currentNumber, numbers: Loadable<TwilioNumbers>, assigning, error, onAssign, onClose})`; local `query`, `pick`; dialog "Choose a number", filter input, rows (`aria-pressed`, "Used by X" for others' numbers, "Not routed to this app" when `!routed`, Voice/SMS chips), loading/error/empty texts, footer assign button using `assignLabel/canAssign`; scrim and close do nothing while assigning.

### Step 13 — SettingsScreen.tsx
Props `{biz, greeting, savingGreeting, greetingError, questions, numbers, sheetOpen, assigning, assignError, onBack, onSaveGreeting, onOpenSheet, onCloseSheet, onAssign}`; back row like `ConversationsScreen`; header monogram/"Settings"/`name · host`; three sections: Phone number (Change / Choose), First greeting (chip, textarea maxLength 300, **hint below the input: "Calls start with “I am an AI assistant.” and then your text. Your text follows it."**, count/note, Reset when dirty, Save & cache), Top questions (expand one at a time, first open, empty/loading/error). No fetching; `draft` resets when saved text changes.

### Step 14 — SelectScreen.tsx
Props `greetingCached`, `onSettings`; imports `GearIcon`, `settingsHint`; insert the `.set-row` button (`data-testid="settings-row"`) before `.recent-head`; header note: replace "Pick a business below to point the number at it." with "Change which business it reaches in that business's Settings."

### Step 15 — hook + App (Sonnet)
`useBusinessSettings(bizId, active, onAssigned)`: loads greeting on bizId change, top questions when `active`, numbers when the sheet opens; stale responses ignored (request counter); `saveGreeting` keeps the draft on error, shows `cache_error`; `assign` → `onAssigned`, close sheet; returns screen props + `greetingCached`. `App.tsx`: stage `settings`; Back/swipe/Esc → select with card still open (sheet closes first); no pull-to-refresh on settings; `onAssigned` updates `businesses`; delete `assignMainNumber` and its call in `onPick` (DEC-52); keep `getMainNumber`.

### Step 16 — e2e (Sonnet)
`web/e2e/mocks.ts` handlers (greeting GET/PUT, top-questions, numbers, assign; delay/fail via `api.fail`; route new paths before the `/businesses` catch-all); `settings.spec.ts` (row hint; Back keeps card open; swipe; Esc order; greeting chip states/422/maxlength/hint; questions toggle + empty; sheet filter, "Used by X"/Move, assign updates card, error); update `app.spec.ts:85` for DEC-52.

## Review points (sonnet5.5_REVIEW)

- `business.settings` reassigned, other keys (`transfer_number`, `google_calendar_id`) survive save/reset.
- Number move in one transaction; 422 for numbers not on the account; cross-tenant reads are stage-1 dev endpoints (OPEN-19).
- "Cached" = row for **both** rates with current voice/language; `prune` keeps the current text; `session.py` uses the same resolver as `pregenerate`.
- Top questions: tenant-scoped, no answer leak across conversations, percent over kept questions, no embed on empty data, batching, 503.
- UI: chip precedence, stale greeting after switching business, sheet can't close mid-assign, Esc/swipe order, no `/main-number` POST.
- Read qwen files as diffs for unrelated reformatting.

## Risks / open

- Twilio `IncomingPhoneNumber` has no city/region: the design's "Miami, FL" becomes friendly name (if it has letters) + "Twilio · Voice & SMS" (check in step 17). No `category` in our data → header shows `name · host`.
- Assigning in our DB does not set the number's Voice URL at Twilio (`routed` flag shows it).
- Clustering threshold 0.80 and the question filter are guesses for local nomic embeddings; step 6 tunes them; Gemini embeddings in stage 3 need a re-tune.
- Greeting PUT runs TTS synchronously (a few seconds, spinner). Top questions embed per request, no persistence (fine at pilot volume).
- Optional later issue: remove the unused `/businesses/{id}/main-number` endpoints.
