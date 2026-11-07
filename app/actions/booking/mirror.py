"""What the booking backend needs from the calendar mirror (#166, on the #165 sync).

- `sync(calendar_id)`: a forced pass now (`calendar_sync.sync_now`), the one before each
  availability check and booking; raises `CalendarError` if Google can't be reached.
- `busy_events(calendar_id, start, end)`: one entry per opaque, non-cancelled event of
  `calendar_events` that overlaps [start, end), whoever owns it (demo rule: every event belongs
  to every business; hand-typed ones included). One entry per event, so capacity > 1 counts.
  Events of our own active group sessions with free seats are left out (#284): they are offered
  to join, so they don't block a 1:1 booking; a full session stays busy.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from sqlalchemy import func, select

from app.actions.booking import calendar_sync
from app.db.models import Booking, CalendarEvent, GroupSession
from app.db.session import SessionLocal


@dataclass(frozen=True)
class BusyEvent:
    start: datetime
    end: datetime
    event_id: str | None = None  # Google event id


class CalendarMirror(Protocol):
    def sync(self, calendar_id: str) -> None: ...
    def busy_events(self, calendar_id: str, start: datetime, end: datetime) -> list[BusyEvent]: ...


class DbMirror:
    def __init__(self, client=None):
        self.client = client  # None: the sync's default Google client

    def sync(self, calendar_id: str) -> None:
        calendar_sync.sync_now(calendar_id, self.client)

    def busy_events(self, calendar_id, start, end):
        with SessionLocal() as session:
            rows = session.execute(
                select(CalendarEvent.starts_at, CalendarEvent.ends_at, CalendarEvent.google_event_id).where(
                    CalendarEvent.calendar_id == calendar_id, CalendarEvent.status != "cancelled",
                    CalendarEvent.transparency == "opaque",
                    CalendarEvent.starts_at < end, CalendarEvent.ends_at > start,
                )
            ).all()
            open_ids = _open_session_events(session, [i for _, _, i in rows])
        return [BusyEvent(a, b, i) for a, b, i in rows if i not in open_ids]


def _open_session_events(session, event_ids: list[str]) -> set[str]:
    """Google event ids of active group sessions that still have free seats (#284)."""
    if not event_ids:
        return set()
    booked = (
        select(Booking.session_id, func.count().label("n")).where(Booking.status == "confirmed")
        .group_by(Booking.session_id).subquery()
    )
    rows = session.execute(
        select(GroupSession.external_ref).outerjoin(booked, booked.c.session_id == GroupSession.id).where(
            GroupSession.external_ref.in_(event_ids), GroupSession.status == "active",
            func.coalesce(booked.c.n, 0) < GroupSession.max_attendees,
        )
    ).scalars()
    return set(rows)
