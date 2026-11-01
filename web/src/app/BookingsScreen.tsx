import { useRef } from "react";
import type { Business } from "../api";
import { MonthCalendar } from "./MonthCalendar";
import { BookingSheet } from "./BookingSheet";
import { SessionBlock } from "./SessionBlock";
import { SessionSheet } from "./SessionSheet";
import { PullRegion, useMinShow } from "./PullIndicator";
import type { PullState } from "./pullToRefresh";
import { ChevronDown, ChevronLeft, ChevronRight, MicSmallIcon, PlusIcon } from "./icons";
import { hostOf, monogram } from "./format";
import {
  TAP_GUARD_MS, blockBox, bookingLine, dayTitle, daySummary, daySwipe, fmtMin, hourCreatable, isClosedHour,
  viewHours,
} from "./bookings";
import type { BookingsBindings } from "./useBookings";
import "./bookings.css";

interface Props extends BookingsBindings {
  biz: Business;
  onBack: () => void;
  onOpenConversation: (conversationId: string) => void;
  pull: PullState;
}

/** Ghost blocks of the loading calendar: [hours after the first hour, length in hours, extra right inset in %]. */
const SKELETON_BLOCKS: [number, number, number][] = [[1, 1, 0], [3, 1.5, 25], [5, 1, 10]];

export function BookingsScreen(p: Props) {
  const { biz, day, todayIso, nowMin, dir, month, dayList } = p;
  const skeleton = useMinShow(month.status === "loading" && !month.data);
  const down = useRef<{ x: number; y: number } | null>(null);
  const swipedAt = useRef(0);
  const { sessions, selectedSession } = p;
  const v = viewHours(month.data, dayList, sessions);
  const hours = Array.from({ length: Math.max(0, v.end - v.start) }, (_, i) => v.start + i);
  const showNow = day === todayIso && nowMin >= v.start * 60 && nowMin <= v.end * 60;

  const onDown = (e: React.PointerEvent) => {
    if (e.isPrimary) down.current = { x: e.clientX, y: e.clientY };
  };

  const onUp = (e: React.PointerEvent) => {
    if (!e.isPrimary) return;
    const s = down.current;
    down.current = null;
    if (!s) return;
    const d = daySwipe(e.clientX - s.x, e.clientY - s.y);
    if (d !== 0) {
      swipedAt.current = Date.now();
      p.onShiftDay(d);
    }
  };

  const ended = !!selectedSession
    && (day < todayIso || (day === todayIso && selectedSession.end_min <= nowMin));

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
        {skeleton ? <div className="sk-block sk-line short" /> : <span data-testid="bk-summary">{month.status === "error" && !month.data ? "—" : daySummary(dayList, sessions)}</span>}
        <span>Swipe to change day</span>
      </div>

      {skeleton && (
        <div className="scroll bk-day" role="status" aria-busy="true" data-testid="bk-skeleton">
          <div className="bk-inner" style={{ height: (v.end - v.start) * 60 }}>
            {hours.map((h) => (
              <div key={h} className="bk-hour" style={{ top: (h - v.start) * 60 }}>
                <span className="bk-hour-label sk-block sk-hour-label" />
              </div>
            ))}
            {SKELETON_BLOCKS.filter(([at, len]) => at + len <= v.end - v.start).map(([at, len, inset], i) => (
              <div key={i} className="bk-block sk-block sk-cal-block"
                style={{ top: at * 60 + 2, height: len * 60 - 4, right: `calc(10px + ${inset}%)` }} />
            ))}
          </div>
          <span className="sr-only">Loading bookings</span>
        </div>
      )}
      {!skeleton && month.status === "error" && (
        <p className="set-error bk-error" role="alert">
          Couldn’t load bookings ({month.error}). <button className="see-all" onClick={p.onRetry}>Try again</button>
        </p>
      )}

      {!skeleton && <PullRegion state={p.pull}>
      <div className="scroll bk-day" data-swipe-back="off" data-testid="bk-day"
        onPointerDown={onDown} onPointerUp={onUp} onPointerCancel={() => { down.current = null; }}>
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
          {hours.filter((h) => hourCreatable(h, day, todayIso, nowMin, dayList, sessions)).map((h) => (
            <button key={h} className="bk-hour-add" data-testid={`hour-add-${h}`}
              style={{ top: (h - v.start) * 60 + 2 }} aria-label={`Add session at ${fmtMin(h * 60)}`}
              onClick={() => { if (Date.now() - swipedAt.current < TAP_GUARD_MS) return; p.onAddAt(h); }}>
              <span><PlusIcon size={16} /></span>
            </button>
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
          {sessions.map((s) => (
            <SessionBlock key={s.id} s={s} box={blockBox(s, v.start)} sel={selectedSession?.id === s.id} busy={p.busy}
              ended={day < todayIso || (day === todayIso && s.end_min <= nowMin)} swipedAt={swipedAt}
              onOpen={() => p.onSelectSession(s.id)} onDelete={() => p.onDeleteSession(s.id)} />
          ))}
          {showNow && (
            <div className="bk-now" data-testid="now-line" style={{ top: nowMin - v.start * 60 }}>
              <i />
            </div>
          )}
        </div>
      </div>
      </PullRegion>}

      {p.notice && <div className="bk-notice" role="status">{p.notice}</div>}

      {p.selected && <BookingSheet booking={p.selected} onClose={p.onCloseSheet} />}
      {(p.draft || selectedSession) && (
        <SessionSheet draft={p.draft} session={selectedSession} ended={ended} day={day} todayIso={todayIso}
          hoursOpen={{ open: v.open, close: v.close }} busy={p.busy} error={p.sheetError}
          freeHourList={p.freeHourList} onClose={p.onCloseSession} onCreate={p.onCreate} onSave={p.onSaveSession}
          onDelete={p.onDeleteSession} onRemove={p.onRemoveAttendee} onOpenConversation={p.onOpenConversation} />
      )}
    </div>
  );
}
