/** Pure decisions for dragging a bottom sheet down to close it (see useSheetDrag.ts). */
export const DRAG_SLOP = 8;       // px of movement before we decide whether it is a sheet drag
export const DRAG_CLOSE_PX = 100; // a release this far down closes the sheet
export const FLICK_PX_PER_MS = 0.5; // ... or a quicker flick of at least FLICK_MIN_PX
export const FLICK_MIN_PX = 30;

/** May a touch that begins here start a drag? Not in a field (typing, selecting), not on a scrolled panel. */
export function canStartDrag(scrollTop: number, onField: boolean): boolean {
  return scrollTop <= 0 && !onField;
}

/** After the slop: "drag" = clearly downward, "none" = upward or sideways (never ours), "wait" = undecided. */
export function dragDecision(dx: number, dy: number): "wait" | "drag" | "none" {
  const ax = Math.abs(dx);
  if (dy < -DRAG_SLOP || ax > DRAG_SLOP && ax >= Math.abs(dy)) return "none";
  if (dy > DRAG_SLOP && dy > ax) return "drag";
  return "wait";
}

/** Should releasing after moving `dy` px down in `ms` close the sheet? */
export function shouldClose(dy: number, ms: number): boolean {
  if (dy >= DRAG_CLOSE_PX) return true;
  return dy >= FLICK_MIN_PX && dy / Math.max(1, ms) >= FLICK_PX_PER_MS;
}
