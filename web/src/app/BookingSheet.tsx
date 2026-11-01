import { useEffect, useRef } from "react";
import type { BookingItem } from "../api";
import { durationLabel, fmtMin } from "./bookings";

/** Bottom sheet with one booking's details (reuses the number sheet's .sheet classes). */
export function BookingSheet({ booking: b, onClose }: { booking: BookingItem; onClose: () => void }) {
  const closeRef = useRef<HTMLButtonElement>(null);
  useEffect(() => closeRef.current?.focus(), []);
  return (
    <div className="sheet" data-testid="booking-sheet">
      <button className="sheet-scrim" aria-label="Close booking" onClick={onClose} />
      <div className="sheet-panel bk-sheet-panel" role="dialog" aria-modal="true" aria-label="Booking details">
        <div className="sheet-grip" />
        <div className="bk-sheet-head">
          <span className="bk-sheet-source" style={{ color: b.by_assistant ? "var(--assistant)" : "var(--bookings)" }}>
            {b.by_assistant ? "Booked by assistant on a call" : "Booked manually"}
          </span>
          <span className="bk-sheet-name">{b.name}</span>
          <span className="bk-sheet-service">{b.service ?? "Booking"}</span>
        </div>
        <div className="bk-tiles">
          <div className="bk-tile"><span>Time</span><b>{fmtMin(b.start_min)} – {fmtMin(b.end_min)}</b></div>
          <div className="bk-tile"><span>Duration</span><b>{durationLabel(b)}</b></div>
        </div>
        <button ref={closeRef} className="bk-close" onClick={onClose}>Close</button>
      </div>
    </div>
  );
}
