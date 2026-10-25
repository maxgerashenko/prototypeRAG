/** Pure state machine for a pull-to-refresh touch gesture. */
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

export const IDLE: PullState = Object.freeze({ phase: "idle" });

/** Dampens a raw pixel distance to the pull indicator range. */
export function dampen(raw: number): number {
  if (raw <= 0 || Number.isNaN(raw)) {
    return 0;
  }
  return Math.min(PULL_MAX, raw * PULL_RESISTANCE);
}

/** Reducer for the pull-to-refresh state machine. */
export function pullReducer(state: PullState, action: PullAction): PullState {
  switch (action.type) {
    case "start": {
      if (state.phase === "refreshing") {
        return state;
      }
      if (!action.enabled || !action.atTop || action.touches !== 1) {
        return IDLE;
      }
      return { phase: "tracking", startX: action.x, startY: action.y };
    }

    case "move": {
      if (state.phase === "idle" || state.phase === "refreshing") {
        return state;
      }
      if (action.touches !== 1) {
        return IDLE;
      }
      if (state.phase === "tracking") {
        const dy = action.y - state.startY;
        const dx = action.x - state.startX;
        if (Math.abs(dx) > PULL_START_SLOP && Math.abs(dx) > Math.abs(dy)) {
          return IDLE;
        }
        if (dy < -PULL_START_SLOP) {
          return IDLE;
        }
        if (dy > PULL_START_SLOP) {
          return { phase: "pulling", startY: state.startY, distance: dampen(dy - PULL_START_SLOP) };
        }
        return state;
      }
      if (state.phase === "pulling") {
        const newDist = dampen(action.y - state.startY - PULL_START_SLOP);
        if (newDist === state.distance) {
          return state;
        }
        return { phase: "pulling", startY: state.startY, distance: newDist };
      }
      return state;
    }

    case "end": {
      if (state.phase === "pulling") {
        if (state.distance >= PULL_THRESHOLD) {
          return { phase: "refreshing" };
        }
        return IDLE;
      }
      if (state.phase === "tracking") {
        return IDLE;
      }
      return state;
    }

    case "cancel": {
      if (state.phase === "tracking" || state.phase === "pulling") {
        return IDLE;
      }
      return state;
    }

    case "done": {
      return state.phase === "idle" ? state : IDLE;
    }

    default: {
      return state;
    }
  }
}

/** Returns true when the gesture should prevent native scroll. */
export function shouldPreventScroll(state: PullState): boolean {
  return state.phase === "pulling";
}

/** Returns a 0..1 progress value for the current phase. */
export function pullProgress(state: PullState): number {
  if (state.phase === "pulling") {
    return Math.min(1, state.distance / PULL_THRESHOLD);
  }
  if (state.phase === "refreshing") {
    return 1;
  }
  return 0;
}

/** Returns true when the pull has crossed the threshold. */
export function isArmed(state: PullState): boolean {
  return state.phase === "pulling" && state.distance >= PULL_THRESHOLD;
}

/** Returns true only on the transition that first reaches the threshold. */
export function armedCrossed(prev: PullState, next: PullState): boolean {
  return !isArmed(prev) && isArmed(next);
}

/** Triggers a short vibration if available, returning success status. */
export function vibrateTick(nav?: { vibrate?: (pattern: number) => boolean }): boolean {
  const target = nav ?? globalThis.navigator;
  if (!target || typeof target.vibrate !== "function") {
    return false;
  }
  try {
    return target.vibrate(VIBRATE_MS) === true;
  } catch {
    return false;
  }
}
