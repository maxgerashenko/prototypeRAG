"""Group sessions as Google Calendar events (#149, design "Google Calendar bookings: backend design").

Google owns which sessions exist and when; Postgres owns seats, the private flag and attendees.
Everything here is a no-op unless `BOOKING_BACKEND=google_calendar` (`get_client()` is None), so
the Postgres-only backend keeps working unchanged.

- A session made in the app is inserted as an event (our own id: the session id, so a retry
  after a timeout gets 409 instead of a duplicate). Nobody is invited (service accounts can't).
- The event title shows seats: "5-6pm Poland Session Sauna 5 · 3/3 attendees · FULL". `name` in
  Postgres is the title without that suffix (`base_name` strips it), so syncing it back never loops.
- A seat change patches the title with `If-Match: <etag>` (412 -> re-read, retry up to 3). A failed
  patch keeps the seat; `reconcile_sessions` retries on the next sync pass.
- `reconcile_events` turns calendar changes into sessions: an event typed in Google becomes a
  session (source "google", default seats, public), a moved one moves it, a deleted one cancels
  the session and its attendee bookings (no text message, #138).
"""

import logging
import re
import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select

from app.actions.booking.google_calendar import calendar_id_for
from app.actions.booking.google_client import CalendarClient, CalendarError, PreconditionFailed
from app.config import get_settings
from app.db import tenant_session
from app.db.models import Booking, Business, GroupSession

log = logging.getLogger(__name__)

NAME_MAX = 60
DEFAULT_SEATS = 8  # seats of a session imported from Google until the owner sets them in the app
MAX_IMPORT = timedelta(hours=12)  # longer events (all-day, holidays) are not sessions
PATCH_TRIES = 3
_SUFFIX = re.compile(r"\s*·\s*\d+/\d+ attendees(?:\s*·\s*FULL)?\s*$")

_client: CalendarClient | None = None


def set_client(client: CalendarClient | None) -> None:
    """Tests (and the live check) inject the calendar client here."""
    global _client
    _client = client


def get_client() -> CalendarClient | None:
    """None unless the Google Calendar backend is on: sessions then stay Postgres-only."""
    global _client
    if _client is None and get_settings().booking_backend == "google_calendar":
        from app.actions.booking.google_client import GoogleCalendarClient

        _client = GoogleCalendarClient(get_settings().google_calendar_credentials_file)
    return _client


def calendar_for(business_id: uuid.UUID) -> str:
    return calendar_id_for(business_id, get_settings().google_calendar_id)


def base_name(summary: str | None) -> str:
    """The event title without our ' · 3/8 attendees · FULL' suffix, cut to the name limit."""
    name = _SUFFIX.sub("", summary or "").strip()[:NAME_MAX].strip()
    return name or "Session"


def title(name: str, booked: int, seats: int) -> str:
    return f"{name} · {booked}/{seats} attendees" + (" · FULL" if booked >= seats else "")


def event_id(session_id: uuid.UUID) -> str:
    return session_id.hex  # 32 chars of 0-9a-f: valid Google event id (base32hex alphabet)


def create_event(client: CalendarClient, calendar_id: str, row: GroupSession, tz: str) -> str:
    """Insert the event of a session made in the app; returns the event id. CalendarError on failure."""
    body = {
        "id": event_id(row.id),
        "summary": title(row.name, 0, row.max_attendees),
        "description": "Group session. Seats and attendees are kept by the assistant app.",
        "start": {"dateTime": row.starts_at.isoformat(), "timeZone": tz},
        "end": {"dateTime": row.ends_at.isoformat(), "timeZone": tz},
        "transparency": "opaque",
        "extendedProperties": {"private": {"business_id": str(row.business_id), "session_id": str(row.id)}},
        "reminders": {"useDefault": False},
    }
    return client.insert_event(calendar_id, body)


def delete_event(client: CalendarClient, calendar_id: str, ref: str) -> None:
    client.delete_event(calendar_id, ref)


def _booked(session, business_id: uuid.UUID, sid: uuid.UUID) -> int:
    return session.scalar(select(func.count()).select_from(Booking).where(
        Booking.business_id == business_id, Booking.session_id == sid, Booking.status == "confirmed")) or 0


def push_title(business_id: uuid.UUID, sid: uuid.UUID) -> bool:
    """Write 'name · n/max attendees' to the session's event. True when the event shows it now.
    Best effort: a failure is logged and the next sync pass retries (the seat stays taken)."""
    client = get_client()
    if client is None:
        return False
    calendar_id = calendar_for(business_id)
    for _ in range(PATCH_TRIES):
        with tenant_session(business_id) as session:
            row = session.get(GroupSession, sid)
            if row is None or not row.external_ref or row.status != "active":
                return False
            ref, want = row.external_ref, title(row.name, _booked(session, business_id, sid), row.max_attendees)
        try:
            event = client.get_event(calendar_id, ref)
            if event is None:  # deleted in Google: the next sync cancels the session
                return False
            if event.get("summary") == want:
                return True
            client.patch_event(calendar_id, ref, {"summary": want}, event.get("etag"))
            return True
        except PreconditionFailed:
            continue  # changed meanwhile: re-read the etag and the seat count
        except CalendarError as exc:
            log.warning("could not update the title of session %s: %s", sid, exc)
            return False
    log.warning("title of session %s still changing after %d tries", sid, PATCH_TRIES)
    return False


@dataclass(frozen=True)
class EventRow:
    """One mirrored calendar event, as `reconcile_events` needs it."""

    id: str
    summary: str
    starts_at: datetime
    ends_at: datetime
    status: str  # "confirmed" / "tentative" / "cancelled"
    transparency: str = "opaque"
    kind: str = "external"  # "booking" / "session" / "external"
    etag: str | None = None


def _owner_business(session, calendar_id: str) -> uuid.UUID | None:
    configured = get_settings().google_calendar_owner_business_id.strip()
    if configured:
        try:
            return uuid.UUID(configured)
        except ValueError:
            log.error("GOOGLE_CALENDAR_OWNER_BUSINESS_ID is not a UUID")
    rows = session.execute(select(Business.id, Business.settings).order_by(Business.created_at, Business.id)).all()
    default = get_settings().google_calendar_id
    for bid, settings in rows:
        if str((settings or {}).get("google_calendar_id") or default).strip() == calendar_id:
            return bid
    return None


def reconcile_events(calendar_id: str, events: Iterable[EventRow], now: datetime | None = None) -> dict:
    """Apply the mirrored events to `sessions`. Returns counts {created, moved, renamed, cancelled}."""
    now = now or datetime.now(timezone.utc)
    counts = {"created": 0, "moved": 0, "renamed": 0, "cancelled": 0}
    retitle: list[tuple[uuid.UUID, uuid.UUID]] = []
    # Demo rule (Max, 2026-11-01): every event belongs to every business, so sessions are looked up by
    # event id only. business_id is stored but not filtered on.
    with tenant_session(uuid.UUID(int=0)) as session:
        owner = _owner_business(session, calendar_id)
        by_ref = {s.external_ref: s for s in session.scalars(
            select(GroupSession).where(GroupSession.external_ref.is_not(None), GroupSession.status == "active")
        ).all()}
        for e in events:
            if e.kind == "booking":
                continue
            row = by_ref.get(e.id)
            if row is None:
                if (e.status == "cancelled" or e.transparency == "transparent" or e.kind != "external"
                        or e.ends_at <= now or not timedelta(0) < e.ends_at - e.starts_at <= MAX_IMPORT):
                    continue
                if owner is None:
                    log.warning("no business to import calendar event %s under", e.id)
                    continue
                row = GroupSession(
                    business_id=owner, starts_at=e.starts_at, ends_at=e.ends_at, name=base_name(e.summary),
                    max_attendees=DEFAULT_SEATS, external_ref=e.id, source="google",
                )
                session.add(row)
                session.flush()
                counts["created"] += 1
                retitle.append((owner, row.id))
                continue
            if e.status == "cancelled":
                for b in session.scalars(select(Booking).where(
                        Booking.business_id == row.business_id, Booking.session_id == row.id,
                        Booking.status == "confirmed")).all():
                    b.status, b.cancelled_at = "cancelled", now
                row.status, row.cancelled_at, row.updated_at = "cancelled", now, now
                counts["cancelled"] += 1
                continue
            if (row.starts_at, row.ends_at) != (e.starts_at, e.ends_at) and e.ends_at > e.starts_at:
                row.starts_at, row.ends_at, row.updated_at = e.starts_at, e.ends_at, now
                for b in session.scalars(select(Booking).where(
                        Booking.business_id == row.business_id, Booking.session_id == row.id,
                        Booking.status == "confirmed")).all():
                    b.starts_at, b.ends_at = e.starts_at, e.ends_at
                counts["moved"] += 1
            name = base_name(e.summary)
            if name != row.name:
                row.name, row.updated_at = name, now
                counts["renamed"] += 1
            if e.summary != title(row.name, _booked(session, row.business_id, row.id), row.max_attendees):
                retitle.append((row.business_id, row.id))
    for business_id, sid in retitle:  # after the commit: writes to Google must not hold the transaction
        push_title(business_id, sid)
    return counts


def sync_before_booking(business_id: uuid.UUID) -> None:
    """One forced sync of the business's calendar before a join, so an event deleted or moved in Google
    a moment ago is not booked. A no-op without Google or before the sync module exists. CalendarError
    when Google can't be reached (nothing is booked then)."""
    if get_client() is None:
        return
    try:
        from app.actions.booking import calendar_sync
    except ImportError:  # #165 not merged yet
        return
    calendar_sync.sync_now(calendar_for(business_id), get_client())
