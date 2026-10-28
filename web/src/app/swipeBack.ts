/** Pure state machine for a swipe-right-to-go-back touch gesture (mobile back action). */
export const SWIPE_START_SLOP = 18;  // raw px of rightward drag before it counts as a swipe: a tap's finger drift must stay below it, or the click is lost
export const SWIPE_HINT_MAX = 20;    // the screen never moves further than this, so the user sees the action is armed
export const SWIPE_RESISTANCE = 0.25; // the screen moves a quarter as far as the finger
export const SWIPE_THRESHOLD = 80;   // raw px of rightward drag needed for a release to go back

export type SwipeState =
  | { phase: "idle" }
  | { phase: "tracking"; startX: number; startY: number }  // finger down, undecided
  | { phase: "swiping"; startX: number; raw: number };     // raw: px the finger moved right past the slop

export type SwipeAction =
  | { type: "start"; x: number; y: number; enabled: boolean; touches: number }
  | { type: "move"; x: number; y: number; touches: number }
  | { type: "end" }
  | { type: "cancel" };

export const SWIPE_IDLE: SwipeState = Object.freeze({ phase: "idle" });

/** How far the screen is shifted for a raw finger distance (0..SWIPE_HINT_MAX). */
export function swipeOffset(state: SwipeState): number {
  return state.phase === "swiping" ? Math.min(SWIPE_HINT_MAX, state.raw * SWIPE_RESISTANCE) : 0;
}

export function isSwipeArmed(state: SwipeState): boolean {
  return state.phase === "swiping" && state.raw >= SWIPE_THRESHOLD;
}

export function swipeCrossed(prev: SwipeState, next: SwipeState): boolean {
  return !isSwipeArmed(prev) && isSwipeArmed(next);
}

export function swipeReducer(state: SwipeState, action: SwipeAction): SwipeState {
  switch (action.type) {
    case "start":
      if (!action.enabled || action.touches !== 1) return SWIPE_IDLE;
      return { phase: "tracking", startX: action.x, startY: action.y };

    case "move": {
      if (state.phase === "idle") return state;
      if (action.touches !== 1) return SWIPE_IDLE;
      if (state.phase === "tracking") {
        const dx = action.x - state.startX;
        const dy = action.y - state.startY;
        // vertical drag (scroll / pull-to-refresh) or a leftward drag: not ours
        if (Math.abs(dy) > SWIPE_START_SLOP && Math.abs(dy) >= Math.abs(dx)) return SWIPE_IDLE;
        if (dx < -SWIPE_START_SLOP) return SWIPE_IDLE;
        if (dx > SWIPE_START_SLOP) return { phase: "swiping", startX: state.startX, raw: dx - SWIPE_START_SLOP };
        return state;
      }
      const raw = Math.max(0, action.x - state.startX - SWIPE_START_SLOP);
      return raw === state.raw ? state : { phase: "swiping", startX: state.startX, raw };
    }

    case "end":
    case "cancel":
      return SWIPE_IDLE;
  }
}
