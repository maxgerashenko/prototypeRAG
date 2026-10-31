// Data and view state of the Bookings screen (day view, month calendar, booking sheet) for the
// open business. Bookings are fetched one month at a time and cached by month key; the month of
// today loads as soon as a business is open (the Select card's hint reads it), the others when the
// screen needs them. Every request has a counter, so an answer for another business or a newer
// request of the same month is dropped.

import { useCallback, useEffect, useRef, useState } from "react";
import { getBookings, type BookingItem, type Bookings } from "../api";
import type { Loadable } from "./App";
import { bookingDays, bookingsOn, isoOf, monthKey, monthRange, nowMinutes, shiftIso, shiftMonth } from "./bookings";

const errorText = (err: unknown) => (err instanceof Error ? err.message : String(err));

const CLOCK_MS = 30_000; // how often "now" is refreshed while the screen is on
const FRESH_MS = 1000; // a month loaded this recently is not fetched again on activation

type Cache = Record<string, Loadable<Bookings>>;

/** What the Bookings screen needs from the hook (everything but the business and Back). */
export interface BookingsBindings {
  day: string; // selected YYYY-MM-DD
  todayIso: string; // device clock
  nowMin: number; // minutes since local midnight, ticks while active
  dir: -1 | 0 | 1; // direction of the last day change (slide-in)
  month: Loadable<Bookings>; // month of `day`; {status:"loading"} when not fetched yet
  dayList: BookingItem[]; // bookingsOn(month.data, day)
  calOpen: boolean;
  calMonth: string; // month key shown in the grid
  calDots: ReadonlySet<string>; // bookingDays(cache[calMonth]?.data); empty if unknown/error
  selected: BookingItem | null;
  onDay: (iso: string) => void; // jump; dir = sign(iso vs day); closes calendar + sheet
  onShiftDay: (n: number) => void; // onDay(shiftIso(day, n))
  onToday: () => void;
  onToggleCal: () => void; // opening sets calMonth = monthKey(day)
  onShiftCal: (months: number) => void; // ±1 month, ±12 year
  onSelect: (id: string) => void;
  onCloseSheet: () => void;
  onRetry: () => void; // reload monthKey(day)
}

export function useBookings(bizId: string | null, active: boolean) {
  const [now, setNow] = useState(() => new Date());
  const [cache, setCache] = useState<Cache>({});
  const [day, setDay] = useState(() => isoOf(new Date()));
  const [dir, setDir] = useState<-1 | 0 | 1>(0);
  const [calOpen, setCalOpen] = useState(false);
  const [calMonth, setCalMonth] = useState(() => monthKey(isoOf(new Date())));
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const gen = useRef(0);
  const seq = useRef(0);
  const reqs = useRef<Record<string, number>>({});
  const lastLoadAt = useRef<Record<string, number>>({});

  const todayIso = isoOf(now);
  const todayRef = useRef(todayIso);
  todayRef.current = todayIso;

  // fetch one month; the old data stays while it reloads
  const load = useCallback(
    (key: string) => {
      if (!bizId) return;
      const id = ++seq.current;
      const g = gen.current;
      reqs.current[key] = id;
      lastLoadAt.current[key] = Date.now();
      setCache((prev) => ({ ...prev, [key]: { ...prev[key], status: "loading" } }));
      const { start, end } = monthRange(key);
      const current = () => g === gen.current && reqs.current[key] === id;
      getBookings(bizId, start, end).then(
        (data) => current() && setCache((prev) => ({ ...prev, [key]: { status: "ok", data } })),
        (err) =>
          current() && setCache((prev) => ({ ...prev, [key]: { ...prev[key], status: "error", error: errorText(err) } })),
      );
    },
    [bizId],
  );

  // forget every cached month but `keep`; their in-flight answers turn stale (current() sees no request)
  const dropOthers = useCallback((keep: string) => {
    for (const k of Object.keys(reqs.current)) if (k !== keep) delete reqs.current[k];
    for (const k of Object.keys(lastLoadAt.current)) if (k !== keep) delete lastLoadAt.current[k];
    setCache((prev) => (prev[keep] ? { [keep]: prev[keep] } : {}));
  }, []);

  // another business: forget the previous one's months, load the month of today
  useEffect(() => {
    ++gen.current;
    reqs.current = {};
    lastLoadAt.current = {};
    const today = isoOf(new Date());
    setCache({});
    setDay(today);
    setDir(0);
    setCalOpen(false);
    setSelectedId(null);
    load(monthKey(today));
  }, [load]);

  // the screen opens: back to today (a booking made on a call shows), calendar and sheet closed
  useEffect(() => {
    if (!active) return;
    const today = isoOf(new Date());
    setNow(new Date());
    setDay(today);
    setDir(0);
    setCalOpen(false);
    setSelectedId(null);
    const key = monthKey(today);
    dropOthers(key);
    if (Date.now() - (lastLoadAt.current[key] ?? 0) >= FRESH_MS) load(key);
  }, [active, load, dropOthers]);

  // the clock ticks while the screen is on
  useEffect(() => {
    if (!active) return;
    const timer = setInterval(() => setNow(new Date()), CLOCK_MS);
    return () => clearInterval(timer);
  }, [active]);

  // fetch the months the screen shows when they were never asked for (errors wait for onRetry)
  const dayKey = monthKey(day);
  useEffect(() => {
    if (!active) return;
    for (const key of calOpen ? [dayKey, calMonth] : [dayKey]) {
      if (cache[key] === undefined && reqs.current[key] === undefined) load(key);
    }
  }, [active, calOpen, dayKey, calMonth, cache, load]);

  const month: Loadable<Bookings> = cache[dayKey] ?? { status: "loading" };
  const dayList = bookingsOn(month.data, day);
  const selected = dayList.find((b) => b.id === selectedId) ?? null;
  const calDots = bookingDays(cache[calMonth]?.data);
  const todayData = cache[monthKey(todayIso)]?.data;
  const todayCount = todayData ? bookingsOn(todayData, todayIso).length : null;

  const onDay = useCallback(
    (iso: string) => {
      setDir(iso > day ? 1 : iso < day ? -1 : 0);
      setDay(iso);
      setCalOpen(false);
      setSelectedId(null);
    },
    [day],
  );
  const onShiftDay = useCallback((n: number) => onDay(shiftIso(day, n)), [onDay, day]);
  const onToday = useCallback(() => onDay(todayIso), [onDay, todayIso]);
  const onToggleCal = useCallback(() => {
    if (!calOpen) setCalMonth(monthKey(day));
    setCalOpen(!calOpen);
  }, [calOpen, day]);
  const onShiftCal = useCallback((months: number) => setCalMonth((m) => shiftMonth(m, months)), []);
  const onSelect = useCallback((id: string) => setSelectedId(id), []);
  const onCloseSheet = useCallback(() => setSelectedId(null), []);
  const onRetry = useCallback(() => load(dayKey), [load, dayKey]);

  /** Back closes the sheet first, then the calendar; false when neither was open. */
  const closeOverlay = useCallback(() => {
    if (selectedId !== null) {
      setSelectedId(null);
      return true;
    }
    if (calOpen) {
      setCalOpen(false);
      return true;
    }
    return false;
  }, [selectedId, calOpen]);

  // after a call ends: the new booking may be in any month; other months reload when shown
  const refreshToday = useCallback(() => {
    const key = monthKey(todayRef.current);
    dropOthers(key);
    load(key);
  }, [load, dropOthers]);

  const bindings: BookingsBindings = {
    day, todayIso, nowMin: nowMinutes(now), dir, month, dayList, calOpen, calMonth, calDots, selected,
    onDay, onShiftDay, onToday, onToggleCal, onShiftCal, onSelect, onCloseSheet, onRetry,
  };
  return { ...bindings, todayCount, closeOverlay, refreshToday };
}
