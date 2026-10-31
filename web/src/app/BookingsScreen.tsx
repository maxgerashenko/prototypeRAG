import { useRef } from "react";
import type { Business } from "../api";
import { MonthCalendar } from "./MonthCalendar";
import { BookingSheet } from "./BookingSheet";
import { useMinShow } from "./PullIndicator";
import { ChevronDown, ChevronLeft, ChevronRight, MicSmallIcon } from "./icons";
import { hostOf, monogram } from "./format";
import {
  TAP_GUARD_MS, blockBox, bookingLine, dayTitle, daySummary, daySwipe, fmtMin, isClosedHour, viewHours,
} from "./bookings";
import type { BookingsBindings } from "./useBookings";
import "./bookings.css";

interface Props extends BookingsBindings {
  biz: Business;
  onBack: () => void;
}

export function BookingsScreen(p: Props) {
  const { biz, day, todayIso, nowMin, dir, month, dayList } = p;
  const skeleton = useMinShow(month.status === "loading" && !month.data);
  const down = useRef<{ x: number; y: number } | null>(null);
  const swipedAt = useRef(0);
  const v = viewHours(month.data, dayList);
  const hours = Array.from({ length: Math.max(0, v.end - v.start) }, (_, i) => v.start + i);
  const showNow = day === todayIso && nowMin >= v.start * 60 && nowMin <= v.end * 60;

  const onUp = (e: React.PointerEvent) => {
    const s = down.current;
    down.current = null;
    if (!s) return;
    const d = daySwipe(e.clientX - s.x, e.clientY - s.y);
    if (d !== 0) {
      swipedAt.current = Date.now();
      p.onShiftDay(d);
    }
  };

  return (
    <div className="screen slide screen-bookings" data-screen="bookings">
      <div className="back-row">
        <button className="back-btn" aria-label="Back to businesses" onClick={p.onBack}>
          <span className="back-circle"><ChevronLeft /></span>
          Businesses
        </button>
      </div>

      <div className="biz-head">
        <div className="big-mono">{monogram(biz.name)}</div>
        <div style={{ display: "flex", flexDirection: "column", gap: 2, minWidth: 0 }}>
          <h1>Bookings</h1>
          <div className="ellipsis">{biz.name} · {hostOf(biz)}</div>
        </div>
      </div>

      <div className="bk-bar">
        <button className="bk-date" aria-label="Choose date" aria-expanded={p.calOpen} onClick={p.onToggleCal}>
          <span data-testid="bk-day-title">{dayTitle(day, todayIso)}</span>
          <span className={`bk-date-chev${p.calOpen ? " open" : ""}`}><ChevronDown size={16} color="currentColor" stroke={2.6} /></span>
        </button>
        {day !== todayIso && <button className="bk-today" onClick={p.onToday}>Today</button>}
        <button className="bk-arrow" aria-label="Previous day" onClick={() => p.onShiftDay(-1)}><ChevronLeft size={18} stroke={2.4} /></button>
        <button className="bk-arrow" aria-label="Next day" onClick={() => p.onShiftDay(1)}><ChevronRight size={18} color="currentColor" stroke={2.4} /></button>
      </div>

      {p.calOpen && (
        <MonthCalendar monthKey={p.calMonth} day={day} todayIso={todayIso} dots={p.calDots} onPick={p.onDay} onShift={p.onShiftCal} />
      )}

      <div className="bk-summary">
        {skeleton ? <div className="sk-block sk-line short" /> : <span data-testid="bk-summary">{month.status === "error" && !month.data ? "—" : daySummary(dayList)}</span>}
        <span>Swipe to change day</span>
      </div>

      {skeleton && (
        <div className="skeleton bk-skeleton" role="status" aria-busy="true">
          {[80, 60, 72, 50].map((w, i) => <div key={i} className="sk-block sk-line" style={{ width: `${w}%` }} />)}
          <span className="sr-only">Loading bookings</span>
        </div>
      )}
      {!skeleton && month.status === "error" && (
        <p className="set-error bk-error" role="alert">
          Couldn’t load bookings ({month.error}). <button className="see-all" onClick={p.onRetry}>Try again</button>
        </p>
      )}

      <div className="scroll bk-day" data-swipe-back="off" data-testid="bk-day"
        onPointerDown={(e) => { down.current = { x: e.clientX, y: e.clientY }; }}
        onPointerUp={onUp} onPointerCancel={() => { down.current = null; }}>
        <div key={day} className={`bk-inner${dir > 0 ? " from-next" : dir < 0 ? " from-prev" : ""}`}
          style={{ height: (v.end - v.start) * 60 }}>
          <div className="bk-last">
            <span className="bk-hour-label">{fmtMin(v.end * 60)}</span>
          </div>
          {hours.map((h) => (
            <div key={h} className={`bk-hour${isClosedHour(h, v) ? " closed" : ""}`}
              style={{ top: (h - v.start) * 60 }} data-testid={isClosedHour(h, v) ? "bk-closed" : undefined}>
              <span className="bk-hour-label">{fmtMin(h * 60)}</span>
              <div className="bk-half" />
            </div>
          ))}
          {dayList.map((b) => {
            const box = blockBox(b, v.start);
            const short = b.end_min - b.start_min < 45;
            return (
              <button key={b.id} className={`bk-block ${b.by_assistant ? "ai" : "manual"}${short ? " short" : ""}`}
                data-testid="booking-block" style={{ top: box.top, height: box.height }}
                aria-label={`${b.name}, ${bookingLine(b)}`}
                onClick={() => { if (Date.now() - swipedAt.current < TAP_GUARD_MS) return; p.onSelect(b.id); }}>
                <span className="bk-block-top">
                  {b.by_assistant && <MicSmallIcon />}
                  <span className="bk-block-name">{b.name}</span>
                </span>
                <span className="bk-block-line">{bookingLine(b)}</span>
              </button>
            );
          })}
          {showNow && (
            <div className="bk-now" data-testid="now-line" style={{ top: nowMin - v.start * 60 }}>
              <i />
            </div>
          )}
        </div>
      </div>

      {p.selected && <BookingSheet booking={p.selected} onClose={p.onCloseSheet} />}
    </div>
  );
}
