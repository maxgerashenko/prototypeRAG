import { useRef, useState } from "react";
import type { SessionItem } from "../api";
import { TAP_GUARD_MS, bookedLine, hourRange, isFull, sessionCount } from "./bookings";
import { LockIcon, PeopleIcon } from "./icons";
import { rowDecision, rowFires, rowOffset } from "./rowSwipe";

interface Props {
  s: SessionItem;
  box: { top: number; height: number };
  sel: boolean;
  busy: boolean;
  ended: boolean; // past sessions cannot be deleted: no swipe
  swipedAt: React.MutableRefObject<number>; // the screen's day-swipe tap guard
  onOpen: () => void;
  onDelete: () => void;
}

/** A session block on the day view. Swipe it left (ItemSwipeToRemove, design/ItemSwipeToRemove.md): a red Delete
 *  grows in from the right and, dragged fully open and released, asks Keep / Delete inside the block.
 *  A swipe that starts here belongs to this block only; the rest of the day column still changes the day. */
export function SessionBlock(p: Props) {
  const { s } = p;
  const [off, setOff] = useState(0);
  const [dragging, setDragging] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const g = useRef<{ x: number; y: number; id: number; swiping: boolean } | null>(null);
  const moved = useRef(false);
  const can = !p.busy && !p.ended && !confirming;
  const style = { top: p.box.top, height: p.box.height };
  const done = (fire: boolean) => {
    g.current = null;
    setDragging(false);
    setOff(0);
    if (fire) setConfirming(true);
  };

  if (confirming) {
    const n = s.booked;
    return (
      <div className="bk-block session confirming" data-testid="session-block" style={style} role="group" aria-label={`Delete ${s.name}?`}>
        <span className="bk-confirm-text">
          Delete? {n > 0 ? `${n} ${n === 1 ? "caller" : "callers"} texted` : ""}
        </span>
        <span className="bk-confirm-btns">
          <button type="button" className="ss-btn" disabled={p.busy} onClick={() => setConfirming(false)}>Keep</button>
          <button type="button" className="ss-btn danger fill" disabled={p.busy} onClick={p.onDelete}>Delete</button>
        </span>
      </div>
    );
  }

  return (
    <button className={`bk-block session${p.sel ? " sel" : ""}${can ? " swipeable" : ""}`} data-testid="session-block" style={style}
      aria-label={`${s.name}, ${hourRange(Math.floor(s.start_min / 60))}, ${bookedLine(s)}`}
      onClick={() => { if (moved.current || Date.now() - p.swipedAt.current < TAP_GUARD_MS) { moved.current = false; return; } p.onOpen(); }}
      onDragStart={(e) => e.preventDefault()}
      onPointerDown={(e) => {
        e.stopPropagation(); // not a day swipe
        if (!can || !e.isPrimary) return;
        moved.current = false;
        g.current = { x: e.clientX, y: e.clientY, id: e.pointerId, swiping: false };
      }}
      onPointerMove={(e) => {
        const st = g.current;
        if (!st || e.pointerId !== st.id) return;
        const dx = e.clientX - st.x;
        if (!st.swiping) {
          const d = rowDecision(dx, e.clientY - st.y);
          if (d === "none") { g.current = null; return; }
          if (d === "wait") return;
          st.swiping = true;
          moved.current = true;
          setDragging(true);
          try { e.currentTarget.setPointerCapture(e.pointerId); } catch { /* fine */ }
        }
        setOff(rowOffset(dx));
      }}
      onPointerUp={(e) => {
        e.stopPropagation();
        const st = g.current;
        if (!st || e.pointerId !== st.id) return;
        done(st.swiping && rowFires(rowOffset(e.clientX - st.x)));
      }}
      onPointerCancel={() => { if (g.current) done(false); }}>
      <span className="bk-block-top">
        {s.private ? <LockIcon /> : <PeopleIcon />}
        <span className="bk-block-name">{s.name}</span>
      </span>
      <span className="bk-block-line">
        {sessionCount(s)}
        {isFull(s) && <span className="bk-full">FULL</span>}
      </span>
      <span className={`bk-reveal${dragging ? " dragging" : ""}`} aria-hidden="true" data-testid="session-reveal" style={{ width: off }}>
        <span>Delete</span>
      </span>
    </button>
  );
}
