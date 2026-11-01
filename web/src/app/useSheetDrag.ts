// Drag-down-to-close for a bottom sheet. Native listeners (React's touch listeners are passive,
// so preventDefault would be ignored), like usePullToRefresh. The element carrying `rootRef` holds
// a `.sheet-panel`; a touch must begin on that panel, a mouse/pen drag on its `.sheet-grip`.
// The panel follows the finger via an inline transform; the decisions are in sheetDrag.ts.

import { useEffect, useRef, type RefObject } from "react";
import { canStartDrag, dragDecision, shouldClose, DRAG_SLOP } from "./sheetDrag";

const FIELDS = "input, select, textarea";

/** How far the content under the finger is scrolled: the largest scrollTop of the scrollable
 * elements between the touch target and the panel (the panel itself or an inner list/body). */
function scrolledAmount(target: Element, panel: HTMLElement): number {
  let top = 0;
  for (let el: Element | null = target; el; el = el === panel ? null : el.parentElement) {
    top = Math.max(top, el.scrollTop);
  }
  return top;
}

export function useSheetDrag(rootRef: RefObject<HTMLElement | null>, opts: { busy: boolean; onClose: () => void }) {
  const busyRef = useRef(opts.busy);
  const onCloseRef = useRef(opts.onClose);
  busyRef.current = opts.busy;
  onCloseRef.current = opts.onClose;

  useEffect(() => {
    const root = rootRef.current;
    if (!root) return;
    const panelOf = () => root.querySelector<HTMLElement>(".sheet-panel");
    // phase: tracking = down, undecided; dragging = the panel follows
    let g: { phase: "tracking" | "dragging"; x: number; y: number; t: number; dy: number; panel: HTMLElement } | null = null;

    const begin = (panel: HTMLElement, x: number, y: number) => {
      g = { phase: "tracking", x, y, t: performance.now(), dy: 0, panel };
    };
    const move = (x: number, y: number): boolean => { // true once the panel is being dragged
      if (!g) return false;
      const dx = x - g.x;
      const dy = y - g.y;
      if (g.phase === "tracking") {
        const d = dragDecision(dx, dy);
        if (d === "none") { g = null; return false; }
        if (d === "wait") return false;
        g.phase = "dragging";
        g.t = performance.now(); // velocity counts from when the drag began
        g.y += DRAG_SLOP; // the slop itself is not part of the offset
        g.panel.style.transition = "none";
        g.panel.style.animation = "none";
      }
      g.dy = Math.max(0, y - g.y);
      g.panel.style.transform = `translateY(${g.dy}px)`;
      return true;
    };
    const end = (cancelled: boolean) => {
      const s = g;
      g = null;
      if (!s || s.phase !== "dragging") return;
      if (!cancelled && !busyRef.current && shouldClose(s.dy, performance.now() - s.t)) {
        onCloseRef.current();
        return;
      }
      const p = s.panel;
      if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
        p.style.transform = "";
        return;
      }
      p.style.transition = "transform .2s ease-out";
      p.style.transform = "translateY(0)";
      setTimeout(() => { p.style.transition = ""; p.style.transform = ""; }, 220);
    };

    const onTouchStart = (e: TouchEvent) => {
      g = null;
      const panel = panelOf();
      const target = e.target as Element | null;
      const t = e.touches[0];
      if (!panel || !t || e.touches.length !== 1 || busyRef.current || !target || !panel.contains(target)) return;
      if (!canStartDrag(scrolledAmount(target, panel), !!target.closest(FIELDS))) return;
      begin(panel, t.clientX, t.clientY);
    };
    const onTouchMove = (e: TouchEvent) => {
      const t = e.touches[0];
      if (!g || !t) return;
      if (e.touches.length !== 1) { end(true); return; }
      if (move(t.clientX, t.clientY) && e.cancelable) e.preventDefault();
    };
    const onTouchEnd = () => end(false);
    const onTouchCancel = () => end(true);

    // mouse / pen on the grip (desktop and tests)
    const onPointerDown = (e: PointerEvent) => {
      const target = e.target as Element | null;
      const panel = panelOf();
      if (e.pointerType === "touch" || !e.isPrimary || busyRef.current || !panel || !target?.closest(".sheet-grip")) return;
      begin(panel, e.clientX, e.clientY);
      try { (target as Element).setPointerCapture(e.pointerId); } catch { /* not capturable: fine */ }
    };
    const onPointerMove = (e: PointerEvent) => {
      if (e.pointerType !== "touch") move(e.clientX, e.clientY);
    };
    const onPointerUp = (e: PointerEvent) => { if (e.pointerType !== "touch") end(false); };
    const onPointerCancel = (e: PointerEvent) => { if (e.pointerType !== "touch") end(true); };

    root.addEventListener("touchstart", onTouchStart, { passive: true });
    root.addEventListener("touchmove", onTouchMove, { passive: false });
    root.addEventListener("touchend", onTouchEnd, { passive: true });
    root.addEventListener("touchcancel", onTouchCancel, { passive: true });
    root.addEventListener("pointerdown", onPointerDown);
    root.addEventListener("pointermove", onPointerMove);
    root.addEventListener("pointerup", onPointerUp);
    root.addEventListener("pointercancel", onPointerCancel);
    return () => {
      root.removeEventListener("touchstart", onTouchStart);
      root.removeEventListener("touchmove", onTouchMove);
      root.removeEventListener("touchend", onTouchEnd);
      root.removeEventListener("touchcancel", onTouchCancel);
      root.removeEventListener("pointerdown", onPointerDown);
      root.removeEventListener("pointermove", onPointerMove);
      root.removeEventListener("pointerup", onPointerUp);
      root.removeEventListener("pointercancel", onPointerCancel);
    };
  }, [rootRef]);
}
