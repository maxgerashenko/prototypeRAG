# StatusChip (notification / save-state labels)

A small pill that says whether something is saved. Copied from the greeting card's "Cached" chip
(`greetingView` in `web/src/app/settings.ts`), now one component: `StatusChip` in
`web/src/app/StatusChip.tsx`; styles `.chip*` and `.spin` in `web/src/app/settings.css`.

## States

| Kind | Colour token | Icon | Typical label | When |
|---|---|---|---|---|
| `saving` | `--conversations` on `--conversations-tint` | spinner (`.spin`) | `Caching…`, `Saving…` | a request is in flight |
| `dirty` | `--amber` on `--amber-bg` | dot | `Unsaved changes` | the screen differs from what is stored; a save is coming |
| `warn` | same as `dirty` | dot | `Not cached` | a steady state that needs attention, nothing pending |
| `saved` | `--phone` on `--phone-bg` | check (pops in, `vw-pop`) | `Cached`, `Saved` | stored and matching the screen |

Precedence when several apply: **saving > dirty > warn > saved** (as `greetingView`).

## Format

- 24px high pill, 11px bold text, 5px gap icon-text, radius 12px, 9px side padding; colours change with a
  .25s transition, so a chip going `dirty` -> `saving` -> `saved` morphs instead of jumping.
- Label: short, sentence case, no full stop. Say what happened, not what to do ("Unsaved changes").
- A time belongs in a nearby note, not in the chip: `cachedLabel()` gives `just now` or `Oct 28, 4:12 PM`.
- `role="status"`, `aria-live="polite"`: screen readers hear the change. Give it a `data-testid` for e2e.
- Hidden when there is nothing to say yet (a sheet just opened and nothing was edited).

## Where it is used

| Place | States |
|---|---|
| Settings, greeting card header | `saving` Caching… · `dirty` Unsaved changes · `warn` Not cached · `saved` Cached |
| Session sheet, right end of the time / booked line (the sheet has no × button; the save chip, then Ended, then FULL: FULL is always the rightmost) | `dirty` while an edit waits (autosave after 2 s or on close) · `saving` Saving… · `saved` Saved; goes back to `dirty` on the next edit |

Reuse it for any new save-state label instead of drawing a new one; a new kind or colour is added here first.
