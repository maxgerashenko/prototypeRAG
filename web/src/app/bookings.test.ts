import { describe, expect, it } from "vitest";
import type { BookingItem, Bookings, SessionItem } from "../api";
import {
  blockBox, bookingDays, bookingsHint, bookingsOn, daySwipe, dayTitle, daySummary, fmtMin, isClosedHour, isoOf, monthGrid, monthKey, monthRange,
  shiftIso, shiftMonth, viewHours, freeHours, hourCreatable, hourFree, hourRange, isFull, sessionCount, bookedLine, sessionsOn,
} from "./bookings";

const item = (start: number, end: number, by = false): BookingItem => ({
  id: String(start), date: "2026-11-05", start_min: start, end_min: end, name: "Ann", service: null,
  party_size: null, phone: null, notes: null, by_assistant: by,
});

describe("dates", () => {
  it("shifts across month and year ends without going through UTC", () => {
    expect(shiftIso("2026-11-30", 1)).toBe("2026-12-01");
    expect(shiftIso("2026-01-01", -1)).toBe("2025-12-31");
    expect(isoOf(new Date(2026, 2, 8))).toBe("2026-03-08");
  });
  it("month keys and ranges", () => {
    expect(monthKey("2026-11-05")).toBe("2026-11");
    expect(monthRange("2026-02")).toEqual({ start: "2026-02-01", end: "2026-02-28" });
    expect(shiftMonth("2026-12", 1)).toBe("2027-01");
    expect(shiftMonth("2026-01", -13)).toBe("2024-12");
  });
  it("month grid starts on Sunday and has 42 days", () => {
    const g = monthGrid("2026-11"); // 1 Nov 2026 is a Sunday
    expect(g).toHaveLength(42);
    expect(g[0]).toBe("2026-11-01");
    expect(monthGrid("2026-10")[0]).toBe("2026-09-27");
  });
  it("titles", () => {
    expect(dayTitle("2026-11-05", "2026-11-05")).toBe("Today, Nov 5");
    expect(dayTitle("2026-11-06", "2026-11-05")).toBe("Fri, Nov 6");
    expect(dayTitle("2027-01-02", "2026-11-05")).toBe("Sat, Jan 2 2027");
  });
});

describe("labels", () => {
  it("fmtMin", () => {
    expect(fmtMin(0)).toBe("12 AM");
    expect(fmtMin(7 * 60)).toBe("7 AM");
    expect(fmtMin(12 * 60)).toBe("12 PM");
    expect(fmtMin(19 * 60 + 30)).toBe("7:30 PM");
  });
  it("daySummary and hint", () => {
    expect(daySummary([])).toBe("No bookings");
    expect(daySummary([item(60, 120, true)])).toBe("1 booking · 1 by assistant");
    expect(daySummary([item(60, 120), item(120, 180, true)])).toBe("2 bookings · 1 by assistant");
    expect(bookingsHint(null)).toBe("Today");
    expect(bookingsHint(0)).toBe("Today · no bookings");
    expect(bookingsHint(1)).toBe("Today · 1 booking");
    expect(bookingsHint(4)).toBe("Today · 4 bookings");
  });
});

describe("day column", () => {
  const data = (hours: Bookings["hours"]): Bookings => ({ timezone: "UTC", hours, bookings: [], sessions: [], busy: [] });
  it("is one hour wider than opening hours on both sides", () => {
    const v = viewHours(data({ open: 7, close: 23 }), []);
    expect([v.start, v.end]).toEqual([6, 24]);
    expect(isClosedHour(6, v)).toBe(true);
    expect(isClosedHour(7, v)).toBe(false);
    expect(isClosedHour(23, v)).toBe(true);
  });
  it("without hours: default range, nothing hatched", () => {
    const v = viewHours(data(null), []);
    expect([v.start, v.end]).toEqual([7, 21]);
    expect(isClosedHour(7, v)).toBe(false);
  });
  it("widens for a booking outside the range", () => {
    const v = viewHours(data({ open: 9, close: 17 }), [item(5 * 60 + 30, 6 * 60 + 30), item(21 * 60, 22 * 60)]);
    expect([v.start, v.end]).toEqual([5, 22]);
  });
  it("places a block by minutes", () => {
    expect(blockBox(item(9 * 60, 10 * 60), 6)).toEqual({ top: 182, height: 56 });
  });
});

describe("swipe and day lookups", () => {
  it("daySwipe needs distance and a mostly horizontal move", () => {
    expect(daySwipe(-60, 0)).toBe(1);
    expect(daySwipe(60, 10)).toBe(-1);
    expect(daySwipe(40, 0)).toBe(0);
    expect(daySwipe(60, 50)).toBe(0);
  });
  const withDays = (): Bookings => ({
    busy: [],
    timezone: "UTC", hours: null, sessions: [],
    bookings: [
      { ...item(600, 660), id: "b", date: "2026-11-05" },
      { ...item(540, 600), id: "a", date: "2026-11-05" },
      { ...item(480, 540), id: "c", date: "2026-11-07" },
    ],
  });
  it("bookingsOn filters by day and sorts by start", () => {
    expect(bookingsOn(withDays(), "2026-11-05").map((b) => b.id)).toEqual(["a", "b"]);
    expect(bookingsOn(withDays(), "2026-11-06")).toEqual([]);
    expect(bookingsOn(undefined, "2026-11-05")).toEqual([]);
  });
  it("bookingDays lists days with bookings", () => {
    expect(bookingDays(withDays())).toEqual(new Set(["2026-11-05", "2026-11-07"]));
    expect(bookingDays(undefined).size).toBe(0);
  });
});

const sess = (start: number, end: number, booked = 0, max = 8, date = "2026-11-05"): SessionItem => ({
  business_id: "b1", source: "app",
  id: `s${start}`, date, start_min: start, end_min: end, name: "Yoga", max_attendees: max, private: false, booked, attendees: [],
});

describe("sessions", () => {
  it("labels and counts", () => {
    expect(hourRange(15)).toBe("3 PM \u2013 4 PM");
    expect(sessionCount(sess(600, 660, 3, 8))).toBe("3/8");
    expect(isFull(sess(600, 660, 8, 8))).toBe(true);
    expect(isFull(sess(600, 660, 7, 8))).toBe(false);
    expect(sessionCount({ ...sess(600, 660, 3, 8), private: true })).toBe("3/8");
    expect(isFull({ ...sess(600, 660, 8, 8), private: true })).toBe(true);
    expect(bookedLine({ ...sess(600, 660, 3, 8), private: true })).toBe("3 of 8 booked \u00b7 Private");
    expect(bookedLine(sess(600, 660, 3, 8))).toBe("3 of 8 booked");
  });
  it("a 10:30 booking blocks hour 10 but not 11", () => {
    const b = [item(10 * 60 + 30, 11 * 60)];
    expect(hourFree(10, b, [])).toBe(false);
    expect(hourFree(11, b, [])).toBe(true);
    expect(hourFree(11, [], [sess(11 * 60, 12 * 60)])).toBe(false);
  });
  it("hourCreatable: today needs a later start, past days never, future days yes", () => {
    const now = 10 * 60 + 30;
    expect(hourCreatable(10, "2026-11-05", "2026-11-05", now, [], [])).toBe(false);
    expect(hourCreatable(11, "2026-11-05", "2026-11-05", now, [], [])).toBe(true);
    expect(hourCreatable(15, "2026-11-04", "2026-11-05", now, [], [])).toBe(false);
    expect(hourCreatable(8, "2026-11-06", "2026-11-05", now, [], [])).toBe(true);
    expect(hourCreatable(12, "2026-11-06", "2026-11-05", now, [], [sess(12 * 60, 13 * 60)])).toBe(false);
  });
  it("freeHours lists creatable hours", () => {
    const hs = freeHours({ start: 9, end: 13 }, "2026-11-05", "2026-11-05", 10 * 60 + 30, [item(11 * 60, 12 * 60)], []);
    expect(hs).toEqual([12]);
  });
  it("sessionsOn, daySummary, bookingDays and viewHours include sessions", () => {
    const data: Bookings = {
      busy: [],
      timezone: "UTC", hours: { open: 9, close: 17 }, bookings: [],
      sessions: [sess(18 * 60, 19 * 60), sess(600, 660), sess(600, 660, 0, 8, "2026-11-09")],
    };
    expect(sessionsOn(data, "2026-11-05").map((s) => s.start_min)).toEqual([600, 1080]);
    expect(sessionsOn(undefined, "2026-11-05")).toEqual([]);
    expect(daySummary([], [sess(600, 660)])).toBe("1 session");
    expect(daySummary([], [sess(600, 660), sess(700, 760)])).toBe("2 sessions");
    expect(daySummary([item(60, 120, true)], [sess(600, 660)])).toBe("1 booking · 1 by assistant · 1 session");
    expect(daySummary([item(60, 120, true)])).toBe("1 booking · 1 by assistant");
    expect(bookingDays(data)).toEqual(new Set(["2026-11-05", "2026-11-09"]));
    const vh = viewHours(data, [], sessionsOn(data, "2026-11-05"));
    expect([vh.start, vh.end]).toEqual([8, 19]);
  });
});
