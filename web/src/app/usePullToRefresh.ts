// Touch hook for pull-to-refresh: native listeners on the app root (React's own touch
// listeners are passive, so preventDefault would be ignored). The gesture logic is the
// pure reducer in pullToRefresh.ts; what a release does is up to the caller's onRefresh.

import { useEffect, useRef, useState, type RefObject } from "react";
import { IDLE, armedCrossed, pullReducer, shouldPreventScroll, type PullAction, type PullState } from "./pullToRefresh";

const SCROLLER = ".scroll, .call-log";

/** A refresh shows its spinner for at least this long. */
export const MIN_SPIN_MS = 500;

export function usePullToRefresh(
  rootRef: RefObject<HTMLElement | null>,
  opts: {
    enabled: boolean;
    onRefresh: () => Promise<unknown> | void; // called once on a release past the threshold
    onArmed?: () => void; // called when the pull first reaches the threshold
  },
): PullState {
  const [state, setState] = useState<PullState>(IDLE);
  const stateRef = useRef<PullState>(IDLE);
  const enabledRef = useRef(opts.enabled);
  const onRefreshRef = useRef(opts.onRefresh);
  const onArmedRef = useRef(opts.onArmed);
  enabledRef.current = opts.enabled;
  onRefreshRef.current = opts.onRefresh;
  onArmedRef.current = opts.onArmed;
  const applyRef = useRef<(action: PullAction) => { prev: PullState; next: PullState }>(() => ({ prev: IDLE, next: IDLE }));

  useEffect(() => {
    const root = rootRef.current;
    if (!root) return;
    let mounted = true;

    const apply = (action: PullAction) => {
      const prev = stateRef.current;
      const next = pullReducer(prev, action);
      if (next !== prev) {
        stateRef.current = next;
        setState(next);
      }
      return { prev, next };
    };
    applyRef.current = apply;

    const onStart = (e: TouchEvent) => {
      // a touch on a header has no scroller above it: use the screen's own (one screen is mounted at a time)
      const scroller = (e.target as Element).closest(SCROLLER) ?? root.querySelector(SCROLLER);
      const t = e.touches[0];
      if (!t) return;
      apply({
        type: "start", x: t.clientX, y: t.clientY, atTop: !scroller || scroller.scrollTop <= 0,
        enabled: enabledRef.current, touches: e.touches.length,
      });
    };
    const onMove = (e: TouchEvent) => {
      const t = e.touches[0];
      if (!t) return;
      const { prev, next } = apply({ type: "move", x: t.clientX, y: t.clientY, touches: e.touches.length });
      if (armedCrossed(prev, next)) onArmedRef.current?.();
      if (shouldPreventScroll(next) && e.cancelable) e.preventDefault();
    };
    const onEnd = (e: TouchEvent) => {
      if (e.touches.length > 0) {
        apply({ type: "cancel" });
        return;
      }
      const { prev, next } = apply({ type: "end" });
      if (prev.phase === "refreshing" || next.phase !== "refreshing") return;
      const released = Date.now();
      void Promise.resolve()
        .then(() => onRefreshRef.current())
        .catch(() => {}) // the loaders show their own error state
        .then(() => new Promise((r) => setTimeout(r, Math.max(0, MIN_SPIN_MS - (Date.now() - released)))))
        .then(() => {
          if (mounted) apply({ type: "done" });
        });
    };
    const onCancel = () => {
      apply({ type: "cancel" });
    };

    root.addEventListener("touchstart", onStart, { passive: true });
    root.addEventListener("touchmove", onMove, { passive: false });
    root.addEventListener("touchend", onEnd, { passive: true });
    root.addEventListener("touchcancel", onCancel, { passive: true });
    return () => {
      mounted = false;
      root.removeEventListener("touchstart", onStart);
      root.removeEventListener("touchmove", onMove);
      root.removeEventListener("touchend", onEnd);
      root.removeEventListener("touchcancel", onCancel);
    };
  }, [rootRef]);

  // turned off mid-gesture: drop the pull (a refresh already running finishes)
  useEffect(() => {
    if (!opts.enabled) applyRef.current({ type: "cancel" });
  }, [opts.enabled]);

  return state;
}
