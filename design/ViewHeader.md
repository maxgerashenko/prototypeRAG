# ViewHeader

The header at the top of every view opened from a business card: **Settings**, **Bookings**,
**Conversations** and **Transcript**. Built as `ViewHeader.tsx` (`.biz-head` in `app.css`).

```
┌──────────────────────────────────┐
│ [BS]  Bookings                   │   h1: the view's name
│       Bloom & Stem · bloomstem…  │   grey subtitle: business · host
│ ‹ Swipe to go back               │   small grey line, clickable
└──────────────────────────────────┘
```

| Part | Rule |
|---|---|
| Monogram | `.big-mono`, 60 px, the business's initials; same colours on every view (black + accent border). Does not change with the view. |
| Title | `h1`, 24 px / 800. The **view's name** (*Settings*, *Bookings*, *Conversations*), not the business. |
| Subtitle | 14 px `--muted`, one line, ellipsis: `business name · host` (`hostOf`). |
| Padding | `18px 20px`; the hint line follows with small margins. |

## "Swipe to go back" line

Under the header sits one small grey line, `‹ Swipe to go back` (`.back-hint`, 12 px / 600, `--faint`, tight
margins: 6 px below, pulled up under the subtitle). It tells touch users the gesture and is a plain
button for mouse users: a click goes back. There is no `‹ Businesses` row.

Going back is: the line, **swipe right** (except where a view opts out with `data-swipe-back="off"`,
e.g. the booking day column) or **Esc**. Overlays close first (sheet → calendar → leave).

## Transcript

Same shape, with the conversation in the title: h1 = the conversation's title (skeleton while
loading, *Conversation* on error), subtitle = the business name. The meta line (source badge,
when · duration · messages) sits under it in `.tr-meta`.

## Rules

- A new business view uses `ViewHeader`; never its own header markup.
- Title = what the view is; subtitle = whose it is. Keep both to one line.
- Colours come from the tokens in [colors.md](colors.md); no per-view overrides.
