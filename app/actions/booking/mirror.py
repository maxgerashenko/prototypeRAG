"""What the booking backend needs from the calendar mirror (#166, on the #165 sync).

- `sync(calendar_id)`: a forced pass now (`calendar_sync.sync_now`), the one before each
  availability check and booking; raises `CalendarError` if Google can't be reached.
- `busy_events(calendar_id, start, end)`: one entry per opaque, non-cancelled event of
  `calendar_events` that overlaps [start, end), whoever owns it (demo rule: every event belongs
  to every business; hand-typed ones included). One entry per event, so capacity > 1 counts.
  TODO #149: leave out sessions with free seats (offered to join) once events link to sessions.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from sqlalchemy import select

from app.actions.booking import calendar_sync
from app.db.models import CalendarEvent
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
        return [BusyEvent(a, b, i) for a, b, i in rows]
