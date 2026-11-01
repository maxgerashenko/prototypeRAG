# PullToRefresh

Pull a list down to reload it. First built for the conversations list (the reference implementation),
then used by the transcript, Bookings and Business settings. The list slides down over a spinner
that sits behind it; nothing else on the screen moves.

Code: gesture state machine `web/src/app/pullToRefresh.ts` (tested in `pullToRefresh.test.ts`), touch
hook `usePullToRefresh.ts`, view `PullRegion` / `BarsSpinner` in `PullIndicator.tsx`, styles `.ptr-*` in
`app.css`, wiring and the "enabled" rule in `App.tsx`. e2e: `web/e2e/pull.spec.ts`.
History: [plan/feature-pull-to-refresh.md](../plan/feature-pull-to-refresh.md).

## Behaviour

| Rule | Value |
|---|---|
| Starts when | A touch begins at the top of the screen's scroller (`scrollTop` 0) and moves **down** by more than 8 px (`PULL_START_SLOP`); a header touch uses the screen's own scroller, and does nothing if that list is scrolled down |
| Finger to list | Damped: the list moves half as far as the finger (`PULL_RESISTANCE` 0.5), at most 96 px (`PULL_MAX`) |
| Armed at | 64 px (`PULL_THRESHOLD`); a release before that springs back and reloads nothing |
| Release when armed | The list rests at 64 px while it reloads, then slides back up |
| Minimum spin | The spinner shows at least 0.5 s (`MIN_SPIN_MS`) so a fast answer is not a flicker |
| Reload kind | **In place**: the old data stays on screen and is swapped when the answer arrives; the picker is the exception and reloads the page |
| Haptics | A short tick (`vibrateTick`) when armed, on the picker only |
| Disabled | During a call or while a phone call is live, with any sheet / draft open, while a request is running, on a second finger |
| Native pull | Off (`overscroll-behavior-y: none`); the hook uses non-passive native listeners |

## Visual rules

- The header and footer stay put. Only `.ptr-layer` (the list) translates down; the spinner is
  `.ptr-under`, absolutely positioned behind it, `z-index: 0`, height = half the layer's offset.
- Spinner: `BarsSpinner`, 32 px, eight rounded bars. While pulling the bars appear one by one and the ring
  turns with the finger; while refreshing the bars fade in turn and the ring rotates. Armed adds a soft glow.
- Position: **10 px from the top** of the region (`padding-top`), centred horizontally.
- **Colour = the colour of the control that opens the screen**, not a fixed blue. Set with
  `<PullRegion area="…">`, which writes `--ptr-color` for the bars:

| Screen | `area` | Colour | Opened by |
|---|---|---|---|
| Conversations, transcript | `conversations` | `--conversations` | the Conversations control ([PanelControls.md](PanelControls.md)) |
| Bookings | `bookings` | `--bookings` | the Bookings control |
| Business settings | `settings` | `--misc` (grey) | the Business settings control |

- **Exception, Bookings:** the spinner is not centred; it lines up with the calendar's "+" icons
  (centre of the event area, 62 px from the left, 10 px from the right), so `padding-left: 52px` shifts it.
  The "+" itself shows only on hover / touch, but the spinner keeps its column. Other screens stay centred.
- Reduced motion: the spinner still rotates (it is a progress signal); the layer follows the finger as usual.

## Do not

- Slide the header, back row or footer, or show a second spinner.
- Reload the whole page except on the business picker.
- Use a fixed colour: take it from the opening control.
- Enable the pull under a sheet, a draft, a call or a running request.

## Checklist for a new screen

- [ ] Wrap the scroller in `<PullRegion state={pull} area="<area>">`; the scroller is `.scroll` (or `.call-log`).
- [ ] Add the stage to `ptrEnabled` in `App.tsx` and its refetch to `onRefresh` (in place, `Promise.allSettled`).
- [ ] The refetch drops stale answers (request counter) and keeps old data on error.
- [ ] e2e in `pull.spec.ts`: refetch happens, no page reload, spinner colour matches the control.
