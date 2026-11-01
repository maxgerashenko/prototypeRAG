// Touch hook for swipe-right-to-go-back: native listeners on the app root, like usePullToRefresh
// (the two never fight: pull needs a vertical drag, this a horizontal one). While swiping, the
// caller shifts the screen by the returned offset (at most SWIPE_HINT_MAX px); a release past the
// threshold calls onBack. No spinner: the nudge is the whole feedback. A touch that starts inside
// an element marked data-swipe-back="off" (e.g. a horizontally scrolling calendar) stays idle:
// it never prevents scrolling or moves the screen.

import { useEffect, useRef, useState, type RefObject } from "react";
import {
  SWIPE_IDLE, isSwipeArmed, swipeCrossed, swipeOffset, swipeReducer, type SwipeAction, type SwipeState,
} from "./swipeBack";

export function useSwipeBack(
  rootRef: RefObject<HTMLElement | null>,
  opts: { enabled: boolean; onBack: () => void; onArmed?: () => void },
): { offset: number; swiping: boolean } {
  const [state, setState] = useState<SwipeState>(SWIPE_IDLE);
  const stateRef = useRef<SwipeState>(SWIPE_IDLE);
  const enabledRef = useRef(opts.enabled);
  const onBackRef = useRef(opts.onBack);
  const onArmedRef = useRef(opts.onArmed);
  enabledRef.current = opts.enabled;
  onBackRef.current = opts.onBack;
  onArmedRef.current = opts.onArmed;
  const applyRef = useRef<(a: SwipeAction) => SwipeState>(() => SWIPE_IDLE);

  useEffect(() => {
    const root = rootRef.current;
    if (!root) return;

    const apply = (action: SwipeAction) => {
      const prev = stateRef.current;
      const next = swipeReducer(prev, action);
      if (next !== prev) {
        stateRef.current = next;
        setState(next);
      }
      return prev;
    };
    applyRef.current = (a) => { apply(a); return stateRef.current; };

    const onStart = (e: TouchEvent) => {
      const t = e.touches[0];
      const off = !!(e.target as Element | null)?.closest?.('[data-swipe-back="off"]');
      if (t) apply({ type: "start", x: t.clientX, y: t.clientY, enabled: enabledRef.current && !off, touches: e.touches.length });
    };
    const onMove = (e: TouchEvent) => {
      const t = e.touches[0];
      if (!t) return;
      const prev = apply({ type: "move", x: t.clientX, y: t.clientY, touches: e.touches.length });
      if (swipeCrossed(prev, stateRef.current)) onArmedRef.current?.();
      if (stateRef.current.phase === "swiping" && e.cancelable) e.preventDefault(); // no browser edge-swipe / scroll while ours
    };
    const onEnd = (e: TouchEvent) => {
      if (e.touches.length > 0) {
        apply({ type: "cancel" });
        return;
      }
      const prev = apply({ type: "end" });
      if (isSwipeArmed(prev) && enabledRef.current) onBackRef.current();
    };
    const onCancel = () => {
      apply({ type: "cancel" });
    };

    root.addEventListener("touchstart", onStart, { passive: true });
    root.addEventListener("touchmove", onMove, { passive: false });
    root.addEventListener("touchend", onEnd, { passive: true });
    root.addEventListener("touchcancel", onCancel, { passive: true });
    return () => {
      root.removeEventListener("touchstart", onStart);
      root.removeEventListener("touchmove", onMove);
      root.removeEventListener("touchend", onEnd);
      root.removeEventListener("touchcancel", onCancel);
    };
  }, [rootRef]);

  // the screen changed or back got disabled mid-gesture: drop the swipe
  useEffect(() => {
    if (!opts.enabled) applyRef.current({ type: "cancel" });
  }, [opts.enabled]);

  return { offset: swipeOffset(state), swiping: state.phase === "swiping" };
}
