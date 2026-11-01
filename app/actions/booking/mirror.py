"""What the booking backend needs from the calendar mirror (#166; the real mirror is #165).

#165 mirrors every event of the shared calendar into Postgres (`calendar_events`, syncToken
polling). Availability only needs two things from it, so the backend codes against this
protocol and #165 provides the implementation:

- `sync(calendar_id)`: one incremental sync now (the forced sync before each booking);
  raises `CalendarError` if Google can't be reached.
- `busy_events(calendar_id, start, end)`: one entry per opaque, non-cancelled event that
  overlaps [start, end), whoever owns it (hand-typed ones included), except sessions with
  free seats (those are offered to join, not busy). One entry per event, so capacity > 1
  can be counted.

`FreeBusyMirror` is the stopgap until #165 lands: no mirror, Google's merged `freeBusy`
blocks. It is `exact = False` because merged blocks can't be counted (the old limitation).
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from app.actions.booking.google_client import CalendarClient


@dataclass(frozen=True)
class BusyEvent:
    start: datetime
    end: datetime
    event_id: str | None = None  # Google event id; None when unknown (merged free/busy blocks)


class CalendarMirror(Protocol):
    exact: bool  # True: one BusyEvent per event, so overlaps can be counted

    def sync(self, calendar_id: str) -> None: ...
    def busy_events(self, calendar_id: str, start: datetime, end: datetime) -> list[BusyEvent]: ...


class FreeBusyMirror:
    exact = False

    def __init__(self, client: CalendarClient):
        self.client = client

    def sync(self, calendar_id: str) -> None:
        pass  # freeBusy always asks Google

    def busy_events(self, calendar_id, start, end):
        return [BusyEvent(a, b) for a, b in self.client.busy(calendar_id, start, end)]
