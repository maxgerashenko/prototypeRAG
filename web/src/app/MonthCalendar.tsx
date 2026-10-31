import { DoubleChevronLeft, DoubleChevronRight, ChevronLeft, ChevronRight } from "./icons";
import { monthGrid, monthTitle, parseIso } from "./bookings";

interface Props {
  monthKey: string;
  day: string;
  todayIso: string;
  dots: ReadonlySet<string>;
  onPick: (iso: string) => void;
  onShift: (months: number) => void;
}

const WEEKDAYS = ["S", "M", "T", "W", "T", "F", "S"];

/** The mini month calendar under the date title: 6 weeks, dots on days with bookings. */
export function MonthCalendar({ monthKey, day, todayIso, dots, onPick, onShift }: Props) {
  return (
    <div className="bk-cal" data-testid="bk-cal">
      <div className="bk-cal-head">
        <button className="bk-cal-nav dim" aria-label="Previous year" onClick={() => onShift(-12)}><DoubleChevronLeft /></button>
        <button className="bk-cal-nav" aria-label="Previous month" onClick={() => onShift(-1)}><ChevronLeft size={16} stroke={2.4} /></button>
        <div className="bk-cal-title">{monthTitle(monthKey)}</div>
        <button className="bk-cal-nav" aria-label="Next month" onClick={() => onShift(1)}><ChevronRight size={16} color="currentColor" stroke={2.4} /></button>
        <button className="bk-cal-nav dim" aria-label="Next year" onClick={() => onShift(12)}><DoubleChevronRight /></button>
      </div>
      <div className="bk-cal-grid">
        {WEEKDAYS.map((w, i) => <div key={i} className="bk-cal-wd">{w}</div>)}
        {monthGrid(monthKey).map((iso) => {
          const inMonth = iso.slice(0, 7) === monthKey;
          const cls = ["bk-cal-day", iso === day && "sel", iso === todayIso && "today", !inMonth && "out"].filter(Boolean).join(" ");
          return (
            <div key={iso} className="bk-cal-cell">
              <button className={cls} aria-pressed={iso === day} onClick={() => onPick(iso)}
                aria-label={parseIso(iso).toLocaleDateString("en-US", { weekday: "long", month: "long", day: "numeric", year: "numeric" })}>
                <span>{parseIso(iso).getDate()}</span>
                <span className={`bk-dot${inMonth && dots.has(iso) ? " on" : ""}`} />
              </button>
            </div>
          );
        })}
      </div>
    </div>
  );
}
