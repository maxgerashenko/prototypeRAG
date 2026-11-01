/** Pure decisions for swiping a list row left to reveal (and, dragged fully open, fire) its action. */
export const ROW_SLOP = 8;       // px before we decide what the move is
export const ROW_REVEAL = 62;    // width of the revealed action (the word is ~58px: ~2px each side)
export const ROW_MARGIN = 8;     // how far past the action width a release must be to fire it
export const ROW_MAX = ROW_REVEAL + 8; // the row never moves further than this

/** "swipe" = clearly leftward, "none" = vertical (scroll) or rightward (not ours), "wait" = undecided. */
export function rowDecision(dx: number, dy: number): "wait" | "swipe" | "none" {
  const ax = Math.abs(dx);
  const ay = Math.abs(dy);
  if (dx > ROW_SLOP || ay > ROW_SLOP && ay >= ax) return "none";
  if (dx < -ROW_SLOP && ax > ay) return "swipe";
  return "wait";
}

/** How far the row is shifted left for a finger move of `dx` (negative = left), the slop excluded. */
export function rowOffset(dx: number): number {
  return Math.min(ROW_MAX, Math.max(0, -dx - ROW_SLOP));
}

/** Does releasing at this offset fire the action? */
export function rowFires(offset: number): boolean {
  return offset >= ROW_REVEAL + ROW_MARGIN;
}
