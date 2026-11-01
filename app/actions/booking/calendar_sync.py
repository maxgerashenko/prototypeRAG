"""Mirror a Google Calendar into Postgres (#165, design "Google Calendar bookings: backend design").

Google owns *when* and *whether* an event exists; `calendar_events` is the app's copy.

- First pass for a calendar (or after Google answers 410 to a stale token): full sync,
  `singleEvents` (recurring events arrive as instances), `showDeleted`, window today -7 to +90
  days. Events of the window that Google did not return are checked one by one with
  `events.get` (a delete during the gap is not reported by a full list).
- Later passes: incremental, by sync token. The token is saved in the same transaction as the
  events it covers, so a crash never skips changes.
- Changes win over the app: an event deleted in Google cancels the `bookings` whose
  `external_ref` is its id; a moved one moves them.
- `business_id` is read from `extendedProperties.private.business_id` and stored. Demo rule:
  every event belongs to every business, so nothing filters on it yet.

Interface for other code: `sync_now(calendar_id)` (a forced pass, safe before every booking;
raises CalendarError if Google can't be reached, so callers book nothing), `CalendarEvent`, and
`post_sync_hooks` (called with the calendar id after every pass, e.g. session reconciliation).
`run_sync_loop` polls every `CALENDAR_SYNC_INTERVAL_SECONDS`. Push (`events.watch`) is out: it
needs a public HTTPS URL.
"""

import logging
import threading
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone

from sqlalchemy import select, text, update
from sqlalchemy.dialects.postgresql import insert

from app.actions.booking.google_client import (
    CalendarError, CalendarEventsClient, GoogleCalendarClient, SyncTokenExpired,
)
from app.config import get_settings
from app.db.models import Booking, Business, CalendarEvent, CalendarSyncState
from app.db.session import SessionLocal

log = logging.getLogger(__name__)

WINDOW_PAST = timedelta(days=7)
WINDOW_FUTURE = timedelta(days=90)

# Called with the calendar id after each successful pass (inside no transaction).
post_sync_hooks: list[Callable[[str], None]] = []


@dataclass
class SyncResult:
    full: bool
    changed: int = 0  # events inserted or updated
    cancelled: int = 0  # events now cancelled
    bookings_cancelled: int = 0
    bookings_moved: int = 0


def parse_event(item: dict) -> dict | None:
    """Google event JSON -> column values; None if it carries no usable time (a bare
    deletion tombstone), which the caller applies by id only."""
    def when(part: dict | None):
        if not part:
            return None
        if part.get("dateTime"):
            return datetime.fromisoformat(part["dateTime"].replace("Z", "+00:00"))
        if part.get("date"):  # all-day: midnight UTC, the end date is exclusive
            return datetime.combine(date.fromisoformat(part["date"]), time.min, tzinfo=timezone.utc)
        return None

    start, end = when(item.get("start")), when(item.get("end"))
    private = (item.get("extendedProperties") or {}).get("private") or {}
    updated = item.get("updated")
    row = {
        "google_event_id": item["id"],
        "etag": item.get("etag"),
        "business_id": _uuid(private.get("business_id")),
        "summary": item.get("summary"),
        "status": item.get("status") if item.get("status") in ("confirmed", "tentative", "cancelled") else "confirmed",
        "transparency": "transparent" if item.get("transparency") == "transparent" else "opaque",
        "google_updated": datetime.fromisoformat(updated.replace("Z", "+00:00")) if updated else None,
    }
    if start is None or end is None:
        return row if row["status"] == "cancelled" else None
    row["starts_at"], row["ends_at"] = start, max(start, end)
    return row


def _uuid(value) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(value)) if value else None
    except ValueError:
        return None


def _lock(session, calendar_id: str) -> None:
    session.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:k, 0))"), {"k": f"calendar-sync:{calendar_id}"})


def _apply(session, calendar_id: str, items: list[dict], now: datetime, result: SyncResult) -> set[str]:
    """Write one batch of Google events; returns the event ids that were handled."""
    seen = {i["id"] for i in items if i.get("id")}
    parsed = {}
    for item in items:
        r = parse_event(item) if item.get("id") else None
        if r:
            parsed[r["google_event_id"]] = r
    # tombstones without a time: only the status changes
    for item in items:
        if item.get("id") and item["id"] not in parsed and item.get("status") == "cancelled":
            parsed[item["id"]] = {"google_event_id": item["id"], "status": "cancelled"}
    owners = {r["business_id"] for r in parsed.values() if r.get("business_id")}
    known = set(session.scalars(select(Business.id).where(Business.id.in_(owners)))) if owners else set()
    for ev_id, r in parsed.items():
        if r.get("business_id") and r["business_id"] not in known:
            r["business_id"] = None
        if "starts_at" not in r:  # tombstone: update an existing row, never create one
            n = session.execute(
                update(CalendarEvent)
                .where(CalendarEvent.calendar_id == calendar_id, CalendarEvent.google_event_id == ev_id,
                       CalendarEvent.status != "cancelled")
                .values(status="cancelled", synced_at=now)
            ).rowcount
            result.changed += n
            result.cancelled += 1
        else:
            values = {**r, "calendar_id": calendar_id, "synced_at": now}
            stmt = insert(CalendarEvent).values(**values)
            session.execute(stmt.on_conflict_do_update(
                constraint="uq_calendar_events_calendar_id_google_event_id",
                set_={k: v for k, v in values.items() if k not in ("calendar_id", "google_event_id")},
            ))
            result.changed += 1
            if r["status"] == "cancelled":
                result.cancelled += 1
        _apply_to_bookings(session, ev_id, r, now, result)
    return seen


def _apply_to_bookings(session, event_id: str, r: dict, now: datetime, result: SyncResult) -> None:
    """Bookings made through this event follow it (link: `bookings.external_ref`)."""
    linked = (Booking.external_ref == event_id, Booking.status == "confirmed")
    if r["status"] == "cancelled":
        result.bookings_cancelled += session.execute(
            update(Booking).where(*linked).values(status="cancelled", cancelled_at=now)
        ).rowcount
    elif "starts_at" in r and r["ends_at"] > r["starts_at"]:  # bookings need ends_at > starts_at
        result.bookings_moved += session.execute(
            update(Booking).where(*linked, (Booking.starts_at != r["starts_at"]) | (Booking.ends_at != r["ends_at"]))
            .values(starts_at=r["starts_at"], ends_at=r["ends_at"])
        ).rowcount


def _list_all(client: CalendarEventsClient, calendar_id: str, **kw) -> tuple[list[dict], str | None]:
    items: list[dict] = []
    token = None
    while True:
        page = client.list_events(calendar_id, page_token=token, **kw)
        items += page.events
        token = page.next_page_token
        if not token:
            return items, page.next_sync_token


def _full_sync(client: CalendarEventsClient, session, calendar_id: str, now: datetime, result: SyncResult) -> str | None:
    lo, hi = now - WINDOW_PAST, now + WINDOW_FUTURE
    items, sync_token = _list_all(client, calendar_id, time_min=lo, time_max=hi)
    seen = _apply(session, calendar_id, items, now, result)
    # rows in the window that Google did not list: verify each (deleted during a gap, or moved out)
    missing = session.scalars(
        select(CalendarEvent.google_event_id).where(
            CalendarEvent.calendar_id == calendar_id, CalendarEvent.status != "cancelled",
            CalendarEvent.ends_at > lo, CalendarEvent.starts_at < hi,
            CalendarEvent.google_event_id.not_in(seen) if seen else text("true"),
        )
    ).all()
    for ev_id in missing:
        item = client.get_event(calendar_id, ev_id) or {"id": ev_id, "status": "cancelled"}
        _apply(session, calendar_id, [item], now, result)
    state = session.get(CalendarSyncState, calendar_id) or CalendarSyncState(calendar_id=calendar_id)
    state.last_full_sync_at = now
    session.add(state)
    return sync_token


def sync_pass(client: CalendarEventsClient, calendar_id: str, now: datetime | None = None) -> SyncResult:
    """One sync pass for a calendar: incremental if a token is stored, else full."""
    now = now or datetime.now(timezone.utc)
    result = SyncResult(full=False)
    try:
        with SessionLocal() as session, session.begin():
            _lock(session, calendar_id)  # one pass at a time per calendar (loop, bookings)
            state = session.get(CalendarSyncState, calendar_id)
            token = state.sync_token if state else None
            if token:
                try:
                    items, new_token = _list_all(client, calendar_id, sync_token=token)
                    _apply(session, calendar_id, items, now, result)
                except SyncTokenExpired:
                    token = None
            if not token:
                result = SyncResult(full=True)  # drop counts of a failed incremental attempt
                new_token = _full_sync(client, session, calendar_id, now, result)
            state = session.get(CalendarSyncState, calendar_id) or CalendarSyncState(calendar_id=calendar_id)
            state.sync_token, state.last_sync_at, state.last_error = new_token, now, None
            session.add(state)
    except CalendarError as exc:
        _record_error(calendar_id, str(exc))
        raise
    for hook in post_sync_hooks:
        try:
            hook(calendar_id)
        except Exception:
            log.exception("calendar post-sync hook failed")
    return result


def _record_error(calendar_id: str, message: str) -> None:
    try:
        with SessionLocal() as session, session.begin():
            stmt = insert(CalendarSyncState).values(calendar_id=calendar_id, last_error=message[:500])
            session.execute(stmt.on_conflict_do_update(index_elements=["calendar_id"], set_={"last_error": message[:500]}))
    except Exception:
        log.exception("could not record calendar sync error")


_client: GoogleCalendarClient | None = None


def default_client() -> GoogleCalendarClient:
    global _client
    if _client is None:
        _client = GoogleCalendarClient(get_settings().google_calendar_credentials_file)
    return _client


def sync_now(calendar_id: str, client: CalendarEventsClient | None = None) -> SyncResult:
    """Forced pass before an availability check or booking. Raises CalendarError when Google
    can't be reached: the caller must then book nothing."""
    return sync_pass(client or default_client(), calendar_id)


def configured_calendars() -> list[str]:
    """The env default plus every `businesses.settings.google_calendar_id`."""
    ids = {get_settings().google_calendar_id.strip()}
    with SessionLocal() as session:
        ids |= {str(v).strip() for v in session.scalars(select(Business.settings["google_calendar_id"].astext))}
    return sorted(i for i in ids if i)


def run_sync_loop(stop: threading.Event, client: CalendarEventsClient | None = None) -> None:
    """Poll every calendar until `stop` is set. A failing calendar is logged, never fatal."""
    interval = max(5, get_settings().calendar_sync_interval_seconds)
    while not stop.is_set():
        try:
            for cal in configured_calendars():
                try:
                    sync_pass(client or default_client(), cal)
                except CalendarError as exc:
                    log.warning("calendar sync %s failed: %s", cal, exc)
        except Exception:
            log.exception("calendar sync loop")
        stop.wait(interval)
