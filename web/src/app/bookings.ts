// Pure helpers of the Bookings screen: dates as local "YYYY-MM-DD" strings (never through UTC),
// the day column's hour range and the month grid. No React, so they are unit-tested.

import type { BookingItem, Bookings } from "../api";

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

export function daySummary(list: BookingItem[]): string {
  if (list.length === 0) return "No bookings";
  const assistant = list.filter((b) => b.by_assistant).length;
  return `${list.length} ${list.length === 1 ? "booking" : "bookings"} · ${assistant} by assistant`;
}

/** One hour before opening to one hour after closing (edge hours are hatched), widened so no
 * booking of the day falls outside. Opening hours are the booking rules'; without them a
 * default range is shown and nothing is hatched. */
export function viewHours(data: Bookings | undefined, day: BookingItem[]) {
  const hours = data?.hours ?? null;
  const base = hours ?? DEFAULT_RANGE;
  let start = Math.max(0, base.open - 1);
  let end = Math.min(24, base.close + 1);
  for (const b of day) {
    start = Math.min(start, Math.floor(b.start_min / 60));
    end = Math.max(end, Math.ceil(b.end_min / 60));
  }
  return { start, end, open: hours ? hours.open : null, close: hours ? hours.close : null };
}

/** Is this hour row outside opening hours? Only known when the rules have hours. */
export const isClosedHour = (h: number, v: { open: number | null; close: number | null }) =>
  v.open !== null && v.close !== null && (h < v.open || h >= v.close);

/** Top/height in px of a booking inside the day column that starts at `startHour`. */
export function blockBox(b: BookingItem, startHour: number) {
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
  new Set((data?.bookings ?? []).map((b) => b.date));

/** Minutes since midnight on this device. The screen treats the device clock as the business's:
 * an owner looks at their own business, so "today" and the red line follow the phone. */
export const nowMinutes = (now = new Date()) => now.getHours() * 60 + now.getMinutes();
