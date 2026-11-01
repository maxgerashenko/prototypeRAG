// Pure helpers of the Bookings screen: dates as local "YYYY-MM-DD" strings (never through UTC),
// the day column's hour range and the month grid. No React, so they are unit-tested.

import type { BookingItem, Bookings, SessionItem } from "../api";

export const PX_PER_MIN = 1; // one hour = 60 px, as in the design
const DEFAULT_RANGE = { open: 8, close: 20 };

export const isoOf = (d: Date) =>
  `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;

export const parseIso = (s: string) => {
  const [y, m, d] = s.split("-").map(Number);
  return new Date(y, m - 1, d);
};

export const shiftIso = (iso: string, days: number) => {
  const d = parseIso(iso);
  d.setDate(d.getDate() + days);
  return isoOf(d);
};

/** "2026-11" of a day. */
export const monthKey = (iso: string) => iso.slice(0, 7);

/** First and last day of a month key, for GET /bookings. */
export function monthRange(key: string): { start: string; end: string } {
  const [y, m] = key.split("-").map(Number);
  return { start: isoOf(new Date(y, m - 1, 1)), end: isoOf(new Date(y, m, 0)) };
}

export const shiftMonth = (key: string, months: number) => {
  const [y, m] = key.split("-").map(Number);
  return monthKey(isoOf(new Date(y, m - 1 + months, 1)));
};

/** "7 AM", "7:30 PM", "12 AM". */
export function fmtMin(total: number): string {
  const h = Math.floor(total / 60) % 24;
  const m = total % 60;
  return `${((h + 11) % 12) + 1}${m ? `:${String(m).padStart(2, "0")}` : ""} ${h >= 12 ? "PM" : "AM"}`;
}

/** "Today, Nov 5" / "Thu, Nov 6" (+ year when it is not this one). */
export function dayTitle(iso: string, todayIso: string): string {
  const d = parseIso(iso);
  const date = d.toLocaleDateString("en-US", { month: "short", day: "numeric" });
  const year = d.getFullYear() !== parseIso(todayIso).getFullYear() ? ` ${d.getFullYear()}` : "";
  const lead = iso === todayIso ? "Today" : d.toLocaleDateString("en-US", { weekday: "short" });
  return `${lead}, ${date}${year}`;
}

export function daySummary(list: BookingItem[], sessions: SessionItem[] = []): string {
  const sess = sessions.length ? `${sessions.length} ${sessions.length === 1 ? "session" : "sessions"}` : "";
  if (list.length === 0) return sess || "No bookings";
  const assistant = list.filter((b) => b.by_assistant).length;
  const text = `${list.length} ${list.length === 1 ? "booking" : "bookings"} · ${assistant} by assistant`;
  return sess ? `${text} · ${sess}` : text;
}

/** One hour before opening to one hour after closing (edge hours are hatched), widened so no
 * booking of the day falls outside. Opening hours are the booking rules'; without them a
 * default range is shown and nothing is hatched. */
export function viewHours(data: Bookings | undefined, day: BookingItem[], sessions: SessionItem[] = []) {
  const hours = data?.hours ?? null;
  const base = hours ?? DEFAULT_RANGE;
  let start = Math.max(0, base.open - 1);
  let end = Math.min(24, base.close + 1);
  for (const b of [...day, ...sessions]) {
    start = Math.min(start, Math.floor(b.start_min / 60));
    end = Math.max(end, Math.ceil(b.end_min / 60));
  }
  return { start, end, open: hours ? hours.open : null, close: hours ? hours.close : null };
}

/** Is this hour row outside opening hours? Only known when the rules have hours. */
export const isClosedHour = (h: number, v: { open: number | null; close: number | null }) =>
  v.open !== null && v.close !== null && (h < v.open || h >= v.close);

/** Top/height in px of a booking inside the day column that starts at `startHour`. */
export function blockBox(b: { start_min: number; end_min: number }, startHour: number) {
  return {
    top: (b.start_min - startHour * 60) * PX_PER_MIN + 2,
    height: Math.max(20, (b.end_min - b.start_min) * PX_PER_MIN - 4),
  };
}

export const durationLabel = (b: BookingItem) => `${b.end_min - b.start_min} min`;

export const bookingLine = (b: BookingItem) =>
  `${b.service ?? "Booking"} · ${fmtMin(b.start_min)}–${fmtMin(b.end_min)}`;

/** The 6×7 month grid: Sunday first, days of the neighbouring months included. */
export function monthGrid(key: string): string[] {
  const [y, m] = key.split("-").map(Number);
  const first = new Date(y, m - 1, 1);
  return Array.from({ length: 42 }, (_, i) => isoOf(new Date(y, m - 1, 1 - first.getDay() + i)));
}

export const monthTitle = (key: string) => {
  const [y, m] = key.split("-").map(Number);
  return new Date(y, m - 1, 1).toLocaleDateString("en-US", { month: "long", year: "numeric" });
};

/** Hint under "Bookings" in the open card: "Today · 3 bookings". */
export function bookingsHint(count: number | null): string {
  if (count === null) return "Today";
  return `Today · ${count === 0 ? "no bookings" : `${count} ${count === 1 ? "booking" : "bookings"}`}`;
}

export const SWIPE_MIN = 50; // px a horizontal swipe must travel to change the day
export const TAP_GUARD_MS = 350; // a click this soon after a swipe is ignored

/** +1 = next day (swipe left), -1 = previous day (swipe right), 0 = not a day swipe. */
export function daySwipe(dx: number, dy: number): -1 | 0 | 1 {
  if (Math.abs(dx) <= Math.abs(dy) * 1.4) return 0;
  if (dx < -SWIPE_MIN) return 1;
  if (dx > SWIPE_MIN) return -1;
  return 0;
}

/** Bookings of one day, earliest first; none without data. */
export const bookingsOn = (data: Bookings | undefined, iso: string): BookingItem[] =>
  (data?.bookings ?? []).filter((b) => b.date === iso).sort((a, b) => a.start_min - b.start_min);

/** Days that have at least one booking. */
export const bookingDays = (data: Bookings | undefined): Set<string> =>
  new Set([...(data?.bookings ?? []), ...(data?.sessions ?? [])].map((b) => b.date));

// --- group sessions: creating one on an empty hour ---

export const SESSION_MAX_ATTENDEES = 30;
export const SESSION_DEFAULT_MAX = 10;
export const SESSION_NAME_MAX = 60;

/** Sessions of one day, earliest first. */
export const sessionsOn = (data: Bookings | undefined, iso: string): SessionItem[] =>
  (data?.sessions ?? []).filter((s) => s.date === iso).sort((a, b) => a.start_min - b.start_min);

type Span = { start_min: number; end_min: number };

/** No booking or session overlaps the hour by even a minute. */
export const hourFree = (h: number, dayList: Span[], sessions: Span[]) =>
  ![...dayList, ...sessions].some((x) => x.start_min < (h + 1) * 60 && x.end_min > h * 60);

/** Free and in the future: a later day, or today after the hour has started. */
export function hourCreatable(
  h: number, iso: string, todayIso: string, nowMin: number, dayList: Span[], sessions: Span[],
): boolean {
  if (iso < todayIso) return false;
  if (iso === todayIso && h * 60 <= nowMin) return false;
  return hourFree(h, dayList, sessions);
}

export const freeHours = (
  v: { start: number; end: number }, iso: string, todayIso: string, nowMin: number, dayList: Span[], sessions: Span[],
): number[] =>
  Array.from({ length: Math.max(0, v.end - v.start) }, (_, i) => v.start + i).filter((h) =>
    hourCreatable(h, iso, todayIso, nowMin, dayList, sessions),
  );

/** "3 PM – 4 PM". */
export const hourRange = (h: number) => `${fmtMin(h * 60)} – ${fmtMin((h + 1) * 60)}`;

/** "3/8" seats taken (private sessions have a limit too; the block shows a lock). */
export const sessionCount = (s: SessionItem) => `${s.booked}/${s.max_attendees}`;

export const isFull = (s: SessionItem) => s.booked >= s.max_attendees;

/** "3 of 8 booked", "3 of 8 booked · Private". */
export const bookedLine = (s: SessionItem) => `${s.booked} of ${s.max_attendees} booked${s.private ? " \u00b7 Private" : ""}`;

/** Minutes since midnight on this device. The screen treats the device clock as the business's:
 * an owner looks at their own business, so "today" and the red line follow the phone. */
export const nowMinutes = (now = new Date()) => now.getHours() * 60 + now.getMinutes();
