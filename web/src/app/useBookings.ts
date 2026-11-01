// Data and view state of the Bookings screen (day view, month calendar, booking sheet) for the
// open business. Bookings are fetched one month at a time and cached by month key; the month of
// today loads as soon as a business is open (the Select card's hint reads it), the others when the
// screen needs them. Every request has a counter, so an answer for another business or a newer
// request of the same month is dropped.

import { useCallback, useEffect, useRef, useState } from "react";
import {
  createSession, deleteSession, getBookings, removeAttendee, updateSession,
  type BookingItem, type Bookings, type SessionItem,
} from "../api";
import type { Loadable } from "./App";
import {
  bookingDays, bookingsOn, freeHours, hourCreatable, isoOf, monthKey, monthRange, nowMinutes, sessionsOn, shiftIso,
  shiftMonth, viewHours,
} from "./bookings";

const errorText = (err: unknown) => (err instanceof Error ? err.message : String(err));

const CLOCK_MS = 30_000; // how often "now" is refreshed while the screen is on
const REFRESH_MS = 60_000; // visible screen re-reads its month; the Google sync fills Postgres every 60 s
const FRESH_MS = 1000; // a month loaded this recently is not fetched again on activation
const NOTICE_MS = 5000; // how long the "texted" notice stays

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
  onRefresh: () => Promise<unknown>; // pull-to-refresh: the same reload, resolved when it is done
  // group sessions
  sessions: SessionItem[]; // sessionsOn(month.data, day)
  selectedSession: SessionItem | null; // its edit sheet is open; null when the id vanished
  draft: { hour: number | null } | null; // create sheet open; hour = preselected start, null = no free hour
  busy: boolean; // a session request is running
  sheetError: string | null; // last failed request, shown in the open sheet
  notice: string | null; // "2 callers texted"; clears after 5 s and on day change
  freeHourList: number[]; // free future hours of `day` inside the shown range
  onAddAt: (hour: number) => void; // open the create sheet for this hour (ignored unless creatable)
  onSelectSession: (id: string) => void;
  onCloseSession: () => void; // closes the create and the edit sheet, clears sheetError
  onCreate: (v: { name: string; max_attendees: number; private: boolean; hour: number }) => void;
  onSaveSession: (patch: { name?: string; max_attendees?: number; private?: boolean }, sessionId?: string) => void;
  onDeleteSession: (sessionId?: string) => void;
  onRemoveAttendee: (bookingId: string) => void;
}

export function useBookings(bizId: string | null, active: boolean) {
  const [now, setNow] = useState(() => new Date());
  const [cache, setCache] = useState<Cache>({});
  const [day, setDay] = useState(() => isoOf(new Date()));
  const [dir, setDir] = useState<-1 | 0 | 1>(0);
  const [calOpen, setCalOpen] = useState(false);
  const [calMonth, setCalMonth] = useState(() => monthKey(isoOf(new Date())));
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [selectedSessionId, setSelectedSessionId] = useState<string | null>(null);
  const [draft, setDraft] = useState<{ hour: number | null } | null>(null);
  const [busy, setBusy] = useState(false);
  const [sheetError, setSheetError] = useState<string | null>(null);
  const [notice, setNoticeState] = useState<string | null>(null);
  const busyRef = useRef(false); // a session request is running: a second tap/Enter sends nothing
  const mut = useRef(0); // bumped when a session sheet closes or the day changes: older answers are dropped
  const noticeTimer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const gen = useRef(0);
  const seq = useRef(0);
  const reqs = useRef<Record<string, number>>({});
  const lastLoadAt = useRef<Record<string, number>>({});

  const todayIso = isoOf(now);
  const todayRef = useRef(todayIso);
  todayRef.current = todayIso;

  const setNotice = useCallback((text: string | null) => {
    clearTimeout(noticeTimer.current);
    setNoticeState(text);
    if (text) noticeTimer.current = setTimeout(() => setNoticeState(null), NOTICE_MS);
  }, []);
  useEffect(() => () => clearTimeout(noticeTimer.current), []);

  // close the create/edit session sheet and forget its request state
  const resetSessionSheet = useCallback(() => {
    ++mut.current;
    setSelectedSessionId(null);
    setDraft(null);
    busyRef.current = false;
    setBusy(false);
    setSheetError(null);
  }, []);

  // fetch one month; the old data stays while it reloads
  const load = useCallback(
    (key: string, sync = false) => {
      if (!bizId) return Promise.resolve();
      const id = ++seq.current;
      const g = gen.current;
      reqs.current[key] = id;
      lastLoadAt.current[key] = Date.now();
      setCache((prev) => ({ ...prev, [key]: { ...prev[key], status: "loading" } }));
      const { start, end } = monthRange(key);
      const current = () => g === gen.current && reqs.current[key] === id;
      return getBookings(bizId, start, end, sync).then(
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
    resetSessionSheet();
    setNotice(null);
    load(monthKey(today));
  }, [load, resetSessionSheet, setNotice]);

  // the screen opens: back to today (a booking made on a call shows), calendar and sheet closed
  useEffect(() => {
    if (!active) return;
    const today = isoOf(new Date());
    setNow(new Date());
    setDay(today);
    setDir(0);
    setCalOpen(false);
    setSelectedId(null);
    resetSessionSheet();
    setNotice(null);
    const key = monthKey(today);
    dropOthers(key);
    if (Date.now() - (lastLoadAt.current[key] ?? 0) >= FRESH_MS) load(key);
  }, [active, load, dropOthers, resetSessionSheet, setNotice]);

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
  const sessions = sessionsOn(month.data, day);
  const selectedSession = sessions.find((s) => s.id === selectedSessionId) ?? null;
  const nowMin = nowMinutes(now);
  const freeHourList = freeHours(viewHours(month.data, dayList, sessions), day, todayIso, nowMin, dayList, sessions);
  const calDots = bookingDays(cache[calMonth]?.data);
  const todayData = cache[monthKey(todayIso)]?.data;
  const todayCount = todayData ? bookingsOn(todayData, todayIso).length : null;

  const onDay = useCallback(
    (iso: string) => {
      setDir(iso > day ? 1 : iso < day ? -1 : 0);
      setDay(iso);
      setCalOpen(false);
      setSelectedId(null);
      resetSessionSheet();
      setNotice(null);
    },
    [day, resetSessionSheet, setNotice],
  );
  const onShiftDay = useCallback((n: number) => onDay(shiftIso(day, n)), [onDay, day]);
  const onToday = useCallback(() => onDay(todayIso), [onDay, todayIso]);
  const onToggleCal = useCallback(() => {
    if (!calOpen) setCalMonth(monthKey(day));
    setCalOpen(!calOpen);
    setSelectedId(null);
    resetSessionSheet();
  }, [calOpen, day, resetSessionSheet]);
  const onShiftCal = useCallback((months: number) => setCalMonth((m) => shiftMonth(m, months)), []);
  const onSelect = useCallback(
    (id: string) => {
      resetSessionSheet(); // one sheet at a time
      setSelectedId(id);
    },
    [resetSessionSheet],
  );
  const onCloseSheet = useCallback(() => setSelectedId(null), []);
  const onRetry = useCallback(() => void load(dayKey), [load, dayKey]);
  // pull-to-refresh also asks the server to pull Google Calendar first
  const onRefresh = useCallback(() => Promise.resolve(load(dayKey, true)), [load, dayKey]);

  // edits made in Google Calendar show up without a pull: re-read the month while the screen is visible
  useEffect(() => {
    if (!active) return;
    const reload = () => {
      if (document.visibilityState === "visible") void load(dayKey);
    };
    const timer = setInterval(reload, REFRESH_MS);
    const onVisible = () => {
      if (Date.now() - (lastLoadAt.current[dayKey] ?? 0) >= REFRESH_MS / 2) reload();
    };
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      clearInterval(timer);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, [active, load, dayKey]);

  const onAddAt = useCallback(
    (hour: number) => {
      if (!hourCreatable(hour, day, todayIso, nowMin, dayList, sessions)) return;
      resetSessionSheet();
      setSelectedId(null);
      setDraft({ hour });
    },
    [day, todayIso, nowMin, dayList, sessions, resetSessionSheet],
  );
  const onSelectSession = useCallback(
    (id: string) => {
      resetSessionSheet();
      setSelectedId(null);
      setSelectedSessionId(id);
    },
    [resetSessionSheet],
  );

  // run one session request; `apply` patches the cached sessions of the day's month from the answer,
  // `after` runs only while the sheet and day are still the ones that asked
  const mutate = useCallback(
    <T,>(
      call: (biz: string) => Promise<T>,
      apply: (list: SessionItem[], res: T) => SessionItem[],
      after: (res: T) => void,
    ) => {
      if (!bizId || busyRef.current) return;
      busyRef.current = true;
      const g = gen.current;
      const t = mut.current;
      const key = monthKey(day);
      setBusy(true);
      setSheetError(null);
      call(bizId).then(
        (res) => {
          if (g !== gen.current) return;
          setCache((prev) => {
            const cur = prev[key];
            if (!cur?.data) return prev;
            return { ...prev, [key]: { ...cur, data: { ...cur.data, sessions: apply(cur.data.sessions, res) } } };
          });
          load(key); // numbers stay true (bookings made on a call meanwhile)
          if (t !== mut.current) return;
          busyRef.current = false;
          setBusy(false);
          after(res);
        },
        (err) => {
          if (g !== gen.current || t !== mut.current) return;
          busyRef.current = false;
          setBusy(false);
          setSheetError(errorText(err));
        },
      );
    },
    [bizId, day, load],
  );
  const replaceItem = (list: SessionItem[], item: SessionItem) => list.map((s) => (s.id === item.id ? item : s));

  const onCreate = useCallback(
    (v: { name: string; max_attendees: number; private: boolean; hour: number }) =>
      mutate(
        (biz) => createSession(biz, { date: day, hour: v.hour, name: v.name.trim(), max_attendees: v.max_attendees, private: v.private }),
        (list, item) => [...list.filter((s) => s.id !== item.id), item],
        resetSessionSheet,
      ),
    [mutate, day, resetSessionSheet],
  );
  const onSaveSession = useCallback(
    (patch: { name?: string; max_attendees?: number; private?: boolean }, sessionId?: string) => {
      const id = sessionId ?? selectedSessionId; // the sheet passes its own id when it saves while closing
      if (!id) return;
      mutate((biz) => updateSession(biz, id, patch), replaceItem, () => {});
    },
    [mutate, selectedSessionId],
  );
  const onDeleteSession = useCallback((sessionId?: string) => {
    const id = sessionId ?? selectedSessionId; // a swipe on a block deletes that block, whatever is open
    if (!id) return;
    mutate(
      (biz) => deleteSession(biz, id),
      (list) => list.filter((s) => s.id !== id),
      (r) => {
        resetSessionSheet();
        const n = r.texted;
        setNotice(`${n} ${n === 1 ? "caller" : "callers"} texted${r.no_phone > 0 ? `, ${r.no_phone} without a phone` : ""}`);
      },
    );
  }, [mutate, selectedSessionId, resetSessionSheet, setNotice]);
  const onRemoveAttendee = useCallback(
    (bookingId: string) => {
      if (!selectedSessionId) return;
      const id = selectedSessionId;
      const who = selectedSession?.attendees.find((a) => a.id === bookingId)?.name;
      mutate(
        (biz) => removeAttendee(biz, id, bookingId),
        (list, r) => replaceItem(list, r),
        (r) => setNotice(r.texted ? `${who ?? "Caller"} texted` : "No phone — not texted"),
      );
    },
    [mutate, selectedSessionId, selectedSession, setNotice],
  );

  /** Back closes the booking sheet, then the session sheet / create sheet, then the calendar; false when none was open. */
  const closeOverlay = useCallback(() => {
    if (busyRef.current && (selectedSessionId !== null || draft !== null)) return true; // wait for the answer
    if (selectedId !== null) {
      setSelectedId(null);
      return true;
    }
    if (selectedSessionId !== null || draft !== null) {
      resetSessionSheet();
      return true;
    }
    if (calOpen) {
      setCalOpen(false);
      return true;
    }
    return false;
  }, [selectedId, selectedSessionId, draft, calOpen, resetSessionSheet]);

  // after a call ends: the new booking may be in any month; other months reload when shown
  const refreshToday = useCallback(() => {
    const key = monthKey(todayRef.current);
    dropOthers(key);
    load(key);
  }, [load, dropOthers]);

  const bindings: BookingsBindings = {
    day, todayIso, nowMin, dir, month, dayList, calOpen, calMonth, calDots, selected,
    onDay, onShiftDay, onToday, onToggleCal, onShiftCal, onSelect, onCloseSheet, onRetry, onRefresh,
    sessions, selectedSession, draft, busy, sheetError, notice, freeHourList,
    onAddAt, onSelectSession, onCloseSession: resetSessionSheet, onCreate, onSaveSession,
    onDeleteSession, onRemoveAttendee,
  };
  return { ...bindings, todayCount, closeOverlay, refreshToday };
}
