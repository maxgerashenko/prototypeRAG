# Feature plan: Pull-to-refresh in the voice app (mobile)

**Issue:** [#124](https://github.com/maxgerashenko/prototypeRAG/issues/124)  **Plan file:** plan/feature-pull-to-refresh.md
**Stage / milestone:** S1 · Tuning (stage 1 UX, same milestone as #114 hands-free on mobile). Simplest version: one touch hook on the app, no library.

Decided with the user (2026-10-25):

| # | Decision |
|---|---|
| 1 | Works on three screens: business picker (`select`), conversations list (`convos`), conversation (`transcript`) |
| 2 | **Business picker = full reload:** while pulling, the whole screen slides down and a loading icon shows *behind* it, growing and rotating with progress. When the pull reaches the threshold (icon full) the phone vibrates once (`navigator.vibrate(15)`, feature-detected; a no-op on iOS Safari, which has no Vibration API). On release: `window.location.reload()` |
| 3 | **Conversations / transcript = refetch in place:** a spinner in front, that screen's data refetched through `web/src/api.ts`, screen and state kept, no reload |
| 4 | Disabled on `connecting` / `call` / `phone` / `phoneEnded` / `ended`, and while a Twilio call is in progress (the `phone-live` button is shown) |
| 5 | Implementer: MIXED, with QWEN steps 1-3 and SONNET steps 4-6; reviewer `sonnet5.5_REVIEW` |

## Goal and done-when

A pull-down gesture on a phone gets fresh data without leaving the screen, and never touches a live call. The business picker reloads the whole app (with a slide-down and a haptic tick). The conversations list and transcript refetch in place. The browser's own pull-to-refresh is switched off everywhere (`overscroll-behavior`), so it can no longer reload the page mid-call.

- [x] Business picker:
  - pulling slides the screen down and shows the icon behind it, growing and rotating with progress;
  - crossing the threshold vibrates exactly once per pull (Android Chrome; nothing on iOS);
  - releasing past the threshold reloads the page;
  - releasing below the threshold slides back and does not reload.
- [x] Conversations list: a release past the threshold refetches that business's conversations and the business list. A spinner shows in front for at least 0.5 s. The screen, selection and list stay; the old data stays visible while loading.
- [x] Transcript: the same, refetching the conversation. The title never flashes "Loading…".
- [x] A short pull, a pull that starts while the list is scrolled down, a horizontal swipe and a tap trigger nothing. A tap on a card still works.
- [x] No gesture on the connecting / call / phone / ended screens, and none while a Twilio call is in progress. The page never reloads during a call.
- [x] Vitest: the gesture state machine, the threshold-crossing helper (vibrate once), and `vibrateTick` feature detection.
- [x] Playwright (Chromium, CDP touch):
  - a select-screen release reloads the page and vibrates once;
  - conversations / transcript releases refetch and do not reload;
  - nothing happens during a call or while a phone call is in progress.
- [ ] Checked live on iOS Safari and Android Chrome via ngrok; the result is in `plan/03-voice-channel.md` and on the issue. (open: V30 in 03; user step)

**Status 2026-10-26:** steps 1-6 and 8 done in PR #125 (reviewed by `sonnet5.5_REVIEW`). Two follow-up fixes after the architect's check: a header pull while the list is scrolled down no longer refreshes (`usePullToRefresh.ts` falls back to the screen's own scroller), and Back from a transcript restores the list scroll position (`ConversationsScreen.tsx`). Step 7 (live phones) is open.

## Who can implement this

**Qwen (local): PARTLY.** Qwen can draft the self-contained parts: the pure gesture reducer and helpers (`pullToRefresh.ts`), its vitest file, and the presentational `PullIndicator.tsx` (front spinner and behind icon). Each is one new file from a precise spec, with no async and no DOM listeners. It does not fit:
- the touch hook: non-passive native listeners, `cancelable`, async refresh and unmount safety;
- the App.tsx wiring: a 363-line file whose loaders change shape;
- the CDP-touch e2e, which can't be written without running it.

3 of 6 implementation steps fit qwen; the hook is possible from its spec but not recommended.

**Sonnet (Agent_sonnet5.5_CODE): YES.** It can do every step. It is needed for steps 4-6: it reads App.tsx, `e2e/mocks.ts` and `app.spec.ts` and runs `npm run typecheck`, `npm test` and `npm run e2e` itself.

**Recommendation: MIXED (chosen).** QWEN for steps 1-3: free, small, parallel, easy to review. SONNET for steps 4-6: moderate paid tokens, three focused steps checked by test runs. Review effort is moderate: the risky parts are listener semantics, the sliding layer and the loader changes.

## Steps

| # | Step | Executor | Alternative | Files | Why this executor | Check |
|---|---|---|---|---|---|---|
| 1 | Pure gesture state machine + `armedCrossed` + `vibrateTick` | QWEN | SONNET | web/src/app/pullToRefresh.ts (new) | One pure file, precise spec | `npm run typecheck`; step 2 passes |
| 2 | Unit tests for step 1 | QWEN | SONNET | web/src/app/pullToRefresh.test.ts (new) | Tests of pure functions from the same spec | `npm test` |
| 3 | Indicator component (front spinner + behind icon) | QWEN | SONNET | web/src/app/PullIndicator.tsx (new) | Small presentational file | `npm run typecheck` |
| 4 | Touch hook | SONNET | QWEN (spec below, weak fit) | web/src/app/usePullToRefresh.ts (new) | Native non-passive listeners, async, iOS quirks | `npm run typecheck`; exercised by step 6 |
| 5 | Wire into App: per-screen mode, sliding layer on select, refetch on convos/transcript, CSS, SelectScreen notice | SONNET | - | web/src/app/App.tsx, web/src/app/app.css, web/src/app/SelectScreen.tsx | Large file; loaders change shape; must agree with steps 1/3/4 | `npm run typecheck && npm test && npm run e2e` (existing e2e stays green) |
| 6 | E2E with CDP touch | SONNET | - | web/e2e/pull.spec.ts (new), maybe web/e2e/mocks.ts | Needs the mocks read and real runs | `npm run e2e` |
| 7 | Live check on phones via ngrok | USER | - | - | Real iOS Safari / Android Chrome only | Checklist in step 7 |
| 8 | Record the result in plan/03-voice-channel.md and on the issue | CLAUDE | - | plan/03-voice-channel.md | CLAUDE.md rule: plan file and GitHub updated in the same pass | Plan row and issue comment both updated |

Every implemented step is reviewed by `sonnet5.5_REVIEW`. The findings go back to qwen as a new spec (current file + finding).

## Specs

### Step 1 - web/src/app/pullToRefresh.ts (QWEN)

**Purpose:** a pure state machine for a pull-to-refresh touch gesture, plus two helpers. No DOM listeners, no React, no timers. The machine doesn't know what a release does (reload or refetch); the hook and App decide that.

**Style:** TypeScript ES module, 2-space indent, double quotes, semicolons, a short doc comment per export.

**Exports, exactly:**
```ts
export const PULL_START_SLOP = 8;    // raw px of downward drag before it counts as a pull
export const PULL_RESISTANCE = 0.5;  // the indicator / sliding screen moves half as far as the finger
export const PULL_THRESHOLD = 64;    // damped px needed for a release to refresh
export const PULL_MAX = 96;          // the damped distance never exceeds this
export const VIBRATE_MS = 15;

export type PullState =
  | { phase: "idle" }
  | { phase: "tracking"; startX: number; startY: number }   // finger down at the top, undecided
  | { phase: "pulling"; startY: number; distance: number }  // distance: damped px, 0..PULL_MAX
  | { phase: "refreshing" };

export type PullAction =
  | { type: "start"; x: number; y: number; atTop: boolean; enabled: boolean; touches: number }
  | { type: "move"; x: number; y: number; touches: number }
  | { type: "end" }     // finger lifted
  | { type: "cancel" }  // touchcancel, or the feature got disabled mid-gesture
  | { type: "done" };   // the refresh finished

export const IDLE: PullState;  // { phase: "idle" }, one shared object (Object.freeze)
export function dampen(raw: number): number;
export function pullReducer(state: PullState, action: PullAction): PullState;
export function shouldPreventScroll(state: PullState): boolean;
export function pullProgress(state: PullState): number;
export function isArmed(state: PullState): boolean;
export function armedCrossed(prev: PullState, next: PullState): boolean;
export function vibrateTick(nav?: { vibrate?: (pattern: number) => boolean }): boolean;
```

**Behaviour:**

`dampen(raw)` returns 0 if `raw <= 0` or `raw` is NaN, otherwise `Math.min(PULL_MAX, raw * PULL_RESISTANCE)`.

`pullReducer(state, action)` returns the SAME state object whenever nothing changes (React bail-out; the tests check this with `toBe`). Per action:
- **start**
  - In `refreshing`: unchanged.
  - If `!enabled`, `!atTop` or `touches !== 1`: `IDLE`.
  - Otherwise `{ phase: "tracking", startX: x, startY: y }`. This also applies from `tracking` or `pulling` (a fresh gesture).
- **move**
  - In `idle` or `refreshing`: unchanged.
  - In `tracking`/`pulling` with `touches !== 1`: `IDLE`.
  - In `tracking`, with `dy = y - startY` and `dx = x - startX`:
    - `Math.abs(dx) > PULL_START_SLOP && Math.abs(dx) > Math.abs(dy)` gives `IDLE` (horizontal swipe);
    - else `dy < -PULL_START_SLOP` gives `IDLE` (the user is scrolling);
    - else `dy > PULL_START_SLOP` gives `{ phase: "pulling", startY, distance: dampen(dy - PULL_START_SLOP) }`;
    - else unchanged.
  - In `pulling`: `distance = dampen(y - startY - PULL_START_SLOP)`. It stays `pulling` even at distance 0 (the finger went back up). Same object if the distance is equal.
- **end**
  - `pulling` with `distance >= PULL_THRESHOLD`: `{ phase: "refreshing" }`.
  - Any other `pulling`, and `tracking`: `IDLE`.
  - `idle`, `refreshing`: unchanged.
- **cancel**
  - `tracking`/`pulling`: `IDLE`.
  - `idle`, `refreshing`: unchanged (a running refresh finishes and sends `done`).
- **done**: `IDLE` from any phase (same object if already idle).
- **default** (unknown action type): unchanged. Never throws.

Helpers:
- `shouldPreventScroll(s)`: `s.phase === "pulling"`.
- `pullProgress(s)`: `pulling` gives `Math.min(1, distance / PULL_THRESHOLD)`, `refreshing` gives 1, anything else 0.
- `isArmed(s)`: `s.phase === "pulling" && s.distance >= PULL_THRESHOLD`.
- `armedCrossed(prev, next)`: `!isArmed(prev) && isArmed(next)`. True only on the move that first reaches the threshold. Going below and back above within the same pull is true again; staying above is false.
- `vibrateTick(nav = globalThis.navigator)`:
  - if `nav` is undefined or `typeof nav.vibrate !== "function"`, return false (iOS Safari has no Vibration API);
  - otherwise return `nav.vibrate(VIBRATE_MS) === true`, inside try/catch (false on a throw).

### Step 2 - web/src/app/pullToRefresh.test.ts (QWEN)

**Setup:** Vitest (`import { describe, expect, it, vi } from "vitest";`), node environment (no DOM). Import everything from `"./pullToRefresh"`. Paste step 1's spec into the qwen spec as the contract. Define a helper `run(actions, from = IDLE)` that folds `pullReducer`.

**Cases:**
1. `dampen`: `-5` gives 0, `0` gives 0, `NaN` gives 0, `40` gives 20, `1000` gives `PULL_MAX`.
2. `start` gives `IDLE` with `enabled: false`, with `atTop: false`, and with `touches: 2`. A valid start gives `tracking` with `startX`/`startY`.
3. A 5 px move down stays `tracking` and returns the same object (`toBe`).
4. A 20 px move down gives `pulling` with distance 6. A 300 px move caps the distance at `PULL_MAX`.
5. Horizontal (dx 30, dy 10) gives `IDLE`. Upward (dy -20) gives `IDLE`.
6. A pull to distance ≥ `PULL_THRESHOLD` then `end` gives `refreshing`. A pull below the threshold then `end` gives `IDLE`.
7. In `refreshing`, `start`, `move`, `end` and `cancel` return the same object; `done` gives `IDLE`.
8. `cancel` during `pulling` gives `IDLE`.
9. A second finger (`touches: 2`) during `pulling` gives `IDLE`.
10. The finger back above the start: still `pulling`, distance 0.
11. `shouldPreventScroll`, `pullProgress` (0, 0.5, 1, and 1 when refreshing) and `isArmed`.
12. A move in `idle` returns the same object.
13. **Vibrate once per crossing:**
    - Fold moves at raw dy 40, 100, 140, 160, 200 and record each `(prev, next)` pair. With the slop of 8 the damped distances are 16, 46, 66, 76, 96.
    - `armedCrossed` is true exactly once, at the 140 move.
    - Then move back to dy 60 (not armed) and up to dy 180: true exactly once more.
    - `armedCrossed(IDLE, IDLE)` is false; `armedCrossed(pullingArmed, { phase: "refreshing" })` is false.
14. `vibrateTick`:
    - `undefined` gives false; `{}` gives false;
    - `{ vibrate: vi.fn(() => true) }` gives true, called once with `VIBRATE_MS`;
    - a `vibrate` that throws gives false.

### Step 3 - web/src/app/PullIndicator.tsx (QWEN)

**Component:** React 19 function component.
- Import: `import { PULL_THRESHOLD, isArmed, pullProgress, type PullState } from "./pullToRefresh";`.
- Export: `export function PullIndicator({ state, variant }: { state: PullState; variant: "front" | "behind" })`.
- No other imports, no state, class names exactly as below (the CSS comes in step 5).
- `idle` or `tracking`: return `null` for both variants.

**`variant: "front"`** (conversations / transcript: the spinner floats over the screen):
- `pulling`, with `p = pullProgress(state)`:
  ```tsx
  <div className={"ptr" + (isArmed(state) ? " armed" : "")} data-testid="ptr"
    style={{ transform: `translateY(${state.distance - 44}px)`, opacity: p }}>
    <div className="ptr-spinner" style={{ transform: `rotate(${Math.round(p * 270)}deg)` }} />
    <span className="sr-only">{isArmed(state) ? "Release to refresh" : "Pull to refresh"}</span>
  </div>
  ```
- `refreshing`:
  ```tsx
  <div className="ptr refreshing" data-testid="ptr" role="status"
    style={{ transform: `translateY(${PULL_THRESHOLD - 44}px)` }}>
    <div className="ptr-spinner" />
    <span className="sr-only">Refreshing…</span>
  </div>
  ```

**`variant: "behind"`** (business picker: the icon sits under the sliding screen):
- `pulling`:
  ```tsx
  <div className={"ptr-behind" + (isArmed(state) ? " armed" : "")} data-testid="ptr-behind"
    style={{ opacity: p, transform: `scale(${(0.4 + 0.6 * p).toFixed(3)}) rotate(${Math.round(p * 270)}deg)` }}>
    <div className="ptr-spinner" />
    <span className="sr-only">{isArmed(state) ? "Release to reload" : "Pull to reload"}</span>
  </div>
  ```
- `refreshing`: `<div className="ptr-behind refreshing" data-testid="ptr-behind" role="status">` with `<div className="ptr-spinner" />` and `<span className="sr-only">Reloading…</span>`. No inline transform.

### Step 4 - web/src/app/usePullToRefresh.ts (SONNET; spec also works for QWEN)

**Signature:**
```ts
export const MIN_SPIN_MS = 500;
export function usePullToRefresh(
  rootRef: RefObject<HTMLElement | null>,
  opts: {
    enabled: boolean;
    onRefresh: () => Promise<unknown> | void; // called once on a release past the threshold
    onArmed?: () => void;                      // called when the pull first reaches the threshold (armedCrossed)
  },
): PullState
```

**State:**
- The current state lives in `stateRef` and is mirrored into `useState` for rendering.
- `apply(action)` runs `pullReducer`. If the result is a new object it updates both, and it returns `{ prev, next }`.
- The latest `enabled`, `onRefresh` and `onArmed` are kept in refs, so the listeners are attached once.

**Listeners:** one mount effect attaches native listeners to `rootRef.current`, not React props (React's root touch listeners are passive, so `preventDefault` would be ignored).
- `touchstart`, `{ passive: true }`:
  - `scroller = (e.target as Element).closest(".scroll, .call-log")`;
  - `atTop = !scroller || scroller.scrollTop <= 0`;
  - dispatch `start` with `touches[0].clientX/clientY`, `enabledRef.current` and `e.touches.length`.
- `touchmove`, `{ passive: false }`:
  - dispatch `move`;
  - if `armedCrossed(prev, next)`, call `onArmedRef.current?.()`;
  - if `shouldPreventScroll(next) && e.cancelable`, call `e.preventDefault()`.
- `touchend`, passive:
  - if `e.touches.length > 0`, dispatch `cancel`;
  - otherwise dispatch `end`. If the state moved into `refreshing`, call `onRefreshRef.current()` inside `Promise.resolve().then(...)` and swallow errors (the loaders show their own error state). Wait until at least `MIN_SPIN_MS` has passed since the release, then dispatch `done` if still mounted.
  - When `onRefresh` reloads the page, the page unloads before `done`, which is fine.
- `touchcancel`, passive: dispatch `cancel`.
- The cleanup removes all four listeners and sets `mounted = false`.

**Other rules:**
- A separate effect on `opts.enabled`: when it turns false, dispatch `cancel` (a refresh in flight finishes).
- No mouse/pointer handling; touch only.
- The hook knows nothing about modes; App passes a different `onRefresh` / `onArmed` per screen.

### Step 5 - App.tsx, app.css, SelectScreen.tsx (SONNET)

**Goal:** wire steps 1, 3 and 4 in, with a per-screen mode.

**Loaders return promises:**
- `loadBusinesses` and `loadConvos` `return` their `.then(...)` promise (`Promise<void>`).
- `useEffect(loadBusinesses, [loadBusinesses])` becomes `useEffect(() => { loadBusinesses(); }, [loadBusinesses])`, because an effect must not return a promise.
- Other callers stay fire-and-forget.

**`refreshDetail()`:** for the open transcript:
- bump `detailReq`;
- `setDetail(prev => ({ ...prev, status: "loading" }))`, keeping `data`;
- refetch `getConversation` with the same stale-request guard as `openConversation`.

**Mode per stage:**
- `select`: mode `"reload"`.
  - `onRefresh = () => { window.location.reload(); }`. Keep it in a small `reloadPage()` function, so e2e can observe the navigation.
  - `onArmed = () => { vibrateTick(); }`.
- `convos`: mode `"refetch"`. `onRefresh = () => Promise.allSettled([loadBusinesses(), loadConvos(selId)])`.
- `transcript`: mode `"refetch"`. `onRefresh = () => refreshDetail()`.
- Others: disabled.
- Do not vibrate in refetch mode (only the business picker vibrates, per the decision).

**Enabled flag:**
- `ptrEnabled = (stage === "select" || (stage === "convos" && !!selId) || (stage === "transcript" && !!transcript)) && !live`, where `live` is `latestLive(phoneCalls)`, the Twilio call in progress.
- Do NOT gate on `call.phase`: `initialCallState.phase` is `"connecting"` before any call (callState.ts:55).

**Hook:**
- `const rootRef = useRef<HTMLDivElement>(null)` on `<div className="vapp">`.
- `const ptr = usePullToRefresh(rootRef, { enabled: ptrEnabled, onRefresh, onArmed })`.

**Rendering:**
- **Select:**
  - Render `<PullIndicator state={ptr} variant="behind" />` as a child of `.vapp`, before the layer.
  - Wrap `<SelectScreen …/>` in `<div className="ptr-layer" style={{ transform: …, transition: … }}>`.
  - `transform`: `translateY(${ptr.distance}px)` while pulling, `translateY(${PULL_THRESHOLD}px)` while refreshing, `none` otherwise.
  - `transition`: `none` while pulling (it follows the finger), `transform .2s ease` otherwise (springs back after a short pull).
  - The layer is opaque (`background: var(--bg)`) and sits above the behind icon, so the icon is only visible in the gap the slide opens.
  - The `phone-live` button stays outside the layer (gesture is disabled then anyway).
- **Convos / transcript:** `<PullIndicator state={ptr} variant="front" />` as the first child of `.vapp`.

**SelectScreen.tsx:97:** show "Loading businesses…" only when `status === "loading" && !p.businesses.data` (as ConversationsScreen.tsx:48).

**app.css:**
- Add `@keyframes vw-spin { to { transform: rotate(360deg); } }` next to the other keyframes.
- Append:
  ```css
  /* ---------- pull to refresh (mobile) ---------- */
  html, body { overscroll-behavior-y: none; } /* no native pull-to-refresh: it would reload the page, even mid-call */
  .scroll, .call-log { overscroll-behavior-y: contain; }
  /* front spinner: conversations list + transcript (refetch in place) */
  .ptr {
    position: absolute; top: 0; left: 50%; margin-left: -20px; z-index: 6; width: 40px; height: 40px;
    border-radius: 50%; background: var(--card); border: 1px solid var(--line); box-shadow: 0 6px 18px #0008;
    display: flex; align-items: center; justify-content: center; pointer-events: none;
  }
  .ptr.armed, .ptr.refreshing { border-color: var(--accent); }
  .ptr-spinner { width: 20px; height: 20px; border-radius: 50%; border: 2.5px solid var(--line); border-top-color: var(--accent); }
  .ptr.refreshing .ptr-spinner, .ptr-behind.refreshing .ptr-spinner { animation: vw-spin .8s linear infinite; }
  /* business picker: the screen slides down over a loading icon (full reload on release) */
  .ptr-layer { flex: 1; min-height: 0; display: flex; flex-direction: column; position: relative; z-index: 1; background: var(--bg); will-change: transform; }
  .ptr-behind {
    position: absolute; top: 12px; left: 50%; margin-left: -20px; z-index: 0; width: 40px; height: 40px;
    border-radius: 50%; display: flex; align-items: center; justify-content: center; pointer-events: none;
  }
  .ptr-behind .ptr-spinner { width: 28px; height: 28px; border-width: 3px; }
  .ptr-behind.armed .ptr-spinner, .ptr-behind.refreshing .ptr-spinner { border-color: var(--accent); border-top-color: var(--call); }
  ```

**Constraints:** don't touch `window.devReloadBlocked`, the keyboard handling, or the call or phone-call logic. No new dependencies. Append the CSS without reformatting the rest of app.css.

**Check:** `npm run typecheck && npm test && npm run e2e`. Everything that passes today must still pass.

### Step 6 - web/e2e/pull.spec.ts (SONNET)

**Goal:** browser tests in the "phone" Chromium project.

**Setup:**
- `test.use({ hasTouch: true })`.
- Touch goes through `page.context().newCDPSession(page)` and `Input.dispatchTouchEvent`: `touchStart`, `touchMove`s in 20 px steps, `touchEnd`. Write a `pull(page, from, dy)` helper.
- Reuse `mockApi`/`mockVoice` from `e2e/mocks.ts`. If the mocks have no request counter, add one with the smallest change, or use `page.waitForRequest`.
- Before `goto`, stub `navigator.vibrate` with `page.addInitScript`, recording its calls in `window.__vib`.

**Cases:**
1. **Select reloads:**
   - pick a business, then pull 200 px inside `.biz-list`;
   - `ptr-behind` is visible during the pull, and `.ptr-layer` has a non-`none` transform;
   - releasing triggers a navigation (`page.waitForEvent("load")` or a second document request for `/web/`);
   - after the reload the selection is gone (a fresh app).
2. **Vibrate once:** on select, move past the threshold and keep moving. `window.__vib` holds exactly one call with `15` before the release.
3. **Short pull on select:** 60 px raw gives no navigation, no vibrate call, and the layer transform goes back to `none`.
4. **Convos refetch:**
   - add a conversation to the mock data, then pull;
   - `ptr` is visible;
   - `/businesses/<id>/conversations` and `/businesses` are fetched again and the new conversation appears;
   - NO navigation: assert with a `window.__marker` set before the pull that it still exists afterwards;
   - same screen.
5. **Transcript refetch:** the detail is refetched with no navigation (marker), and the title never shows "Loading…".
6. **Scrolled down:** the list scrolled down first, then a pull: nothing (no fetch, no navigation).
7. **During a call** (app.spec.ts `startCall` pattern): a pull gives no navigation and no `/businesses` fetch; the call status stays live.
8. **Twilio call in progress** (`phone-live` shown, mocked feed as in `live.spec.ts`), then close the phone screen: a pull on select gives no navigation.
9. **Tap:** a tap (start and end at the same point) on a business card still selects it.

**Check:** `npm run e2e` passes. Report any case that cannot be driven reliably instead of weakening it.

### Step 7 - Live check (USER)

Via ngrok, on iOS Safari and Android Chrome:
- [ ] **Business picker:** the screen slides and the icon grows behind it. The threshold vibrates on Android (nothing on iOS, expected). A release reloads; a short pull springs back.
- [ ] **Conversations / transcript:** refresh in place, no reload.
- [ ] Taps on cards still work. Scrolling and the iOS bounce look normal.
- [ ] Call screen and a watched phone call: pulling does nothing, and the page never reloads.
- [ ] Note whether the vibration fires on the first pull of a fresh page (see the risk below).

## Review points

- **Mode per screen:** only `select` reloads. `convos`/`transcript` must never call `window.location.reload()`. The vibration fires only on select and only on `armedCrossed`, not on every move.
- **Call safety:**
  - disabled on `connecting`/`call`/`phone`/`phoneEnded`/`ended` and while `live`;
  - not derived from `call.phase` (it starts as `"connecting"`);
  - disabling mid-gesture cancels the pull and resets the layer transform;
  - a reload can never start during a call.
- **Listeners:** `touchmove` is native with `{ passive: false }`. `preventDefault` only while `pulling` and only if `e.cancelable`. No `touch-action: none` on scroll containers.
- **Taps:** taps under the slop are never prevented. A pull that started on a card must not also pick or open it.
- **Sliding layer:**
  - the wrapper doesn't break SelectScreen's flex layout, the footer call button or `scrollToCard`;
  - it has no transform when idle (a transform creates a containing block);
  - the behind icon is hidden by the opaque layer.
- **Loaders:** no `useEffect` returns a promise. The `detailReq` guard is kept. Old data stays visible during a refetch. `done` always fires (on rejection and after unmount), so the spinner is never stuck.
- **State identity:** the reducer returns the same object when unchanged, so `touchmove` doesn't re-render every frame.
- **CSS:** `overscroll-behavior` on `html, body` doesn't break `.call-log` (column-reverse) scrolling. The CSS is appended without reformatting the rest of app.css.

## Issues

**Title:** Voice app: pull-to-refresh on mobile (picker reloads, lists refetch, never during a call)
**Labels:** area:front, type:feature  **Milestone:** S1 · Tuning
**Body:**
```
**Area:** front   **Type:** feature   **Stage:** 1
**Blocked by:** none
**Files:** web/src/app/pullToRefresh.ts (new), web/src/app/pullToRefresh.test.ts (new), web/src/app/PullIndicator.tsx (new), web/src/app/usePullToRefresh.ts (new), web/src/app/App.tsx, web/src/app/SelectScreen.tsx, web/src/app/app.css, web/e2e/pull.spec.ts (new)
**Plan:** plan/feature-pull-to-refresh.md

## Problem
On a phone (the normal way to try `/web/` now, via ngrok) there is no way to get fresh data — a new business, a conversation from a phone call, an updated transcript — without navigating away. Where the browser's own pull-to-refresh fires, it reloads the page uncontrolled, also on the call screen, where it could cut a live call.

## What to do
- **Business picker:** pulling down slides the screen down over a loading icon that grows and rotates with the pull. At the threshold the phone vibrates once (`navigator.vibrate(15)`, feature-detected; iOS Safari has no Vibration API, so nothing there). Releasing past the threshold runs `window.location.reload()`.
- **Conversations list and transcript:** a spinner in front, and a release past the threshold refetches that screen's data through `web/src/api.ts`. The screen and its state are kept; no reload.
- Disabled on the connecting / call / phone / ended screens and while a Twilio call is in progress.
- Turn off the browser's native pull-to-refresh (`overscroll-behavior`).

## Done when
- [ ] Picker: slide + icon behind, one vibration at the threshold (Android), reload on release, spring back on a short pull.
- [ ] Conversations / transcript refetch in place with no reload; old data stays visible.
- [ ] A short pull, a pull while scrolled down, a horizontal swipe and a tap do nothing; a tap still selects.
- [ ] Nothing happens during a call or while a phone call is in progress; the page never reloads during a call.
- [ ] Vitest: state machine, vibrate-once threshold crossing, `vibrateTick` feature detection.
- [ ] Playwright (CDP touch): picker reload + one vibrate; lists refetch without a reload; no-ops during calls.
- [ ] Checked live on iOS Safari and Android Chrome via ngrok; result in `plan/03-voice-channel.md` and here.

## How to test
`cd web && npm run typecheck && npm test && npm run e2e`, then open the ngrok URL on a phone: pull on the picker (it should reload; Android vibrates), pull on a conversations list and a transcript (they refresh in place), start a call and pull on the call screen (nothing happens).
```

## Risks and open questions

| Risk | Detail | Handling |
|---|---|---|
| Vibration needs user activation (Android Chrome) | Chrome blocks `navigator.vibrate` until the page has had a user activation. `touchend` counts, but `touchstart`/`touchmove` don't, so the first pull on a freshly loaded page, before any tap, may not vibrate | Accept for the prototype; the USER step notes it. Option: none that is honest (no workaround without a prior tap) |
| iOS: no vibration at all | Safari has no Vibration API | `vibrateTick` returns false: a no-op, by decision |
| Reload loses state | The picker reload clears the selection, query and `newIds` marks | Accepted by decision (it is a reload) |
| Native pull-to-refresh suppression unverified | Not known whether it fires today in this layout (`.vapp` `100dvh; overflow: hidden`, inner `.scroll` divs); `overscroll-behavior` needs about iOS 16+ | USER step 7 checks both browsers |
| `cancelable` already false | If the browser starts its own scroll before the 8 px slop, `preventDefault` is ignored and the list may bounce under the gesture | Tune the slop after step 7 |
| Sliding layer jank | A transform per `touchmove` on a large subtree | `will-change: transform`, no transition while pulling; check on a real phone |
| Playwright is Chromium only | It cannot prove iOS behaviour | Step 7 is required |
| Reduced motion | The `prefers-reduced-motion` rule (app.css:43) stops the spinner rotating; the status text still shows | Accepted |
| Native reload removed | A user can't force a full reload by pulling on the conversations / transcript / call screens | Intended (it protects calls); the picker reload covers it |

No open questions remain that block the start; Q1 and Q2 are decided (see the table at the top). Not verified while planning: the internals of `e2e/mocks.ts` (whether it counts requests), CallScreens.tsx and callState.ts beyond the phase lines.
