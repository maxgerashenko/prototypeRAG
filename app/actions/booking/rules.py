"""Booking rules as pure functions: no DB, no clock, so every rule is unit-testable.

The rules row (`booking_rules`) is owner-set per business. Times the caller gives are
wall-clock times in the business timezone; they are stored in UTC (plan/04-actions.md).
`hours` is {"mon": [["09:00", "21:00"]], ...}: a missing or empty day is closed, and
`hours=None` means "no hours configured" -> the hours rule is skipped (the profile's
`opening_hours` is free text, so code can't enforce it).
"""

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
MAX_ALTERNATIVES = 3


@dataclass(frozen=True)
class Rules:
    timezone: str = "UTC"
    slot_minutes: int = 60  # default duration, and the step for suggested alternatives
    capacity: int = 1  # bookings allowed at the same moment
    lead_time_minutes: int = 0  # earliest booking = now + this
    max_party_size: int | None = None
    hours: dict | None = None
    service_durations: dict = field(default_factory=dict)  # lowercase service name -> minutes

    def duration(self, service: str | None) -> int:
        return int(self.service_durations.get((service or "").strip().lower(), self.slot_minutes))


def local_start(rules: Rules, day: str, at: str) -> datetime:
    """Parse 'YYYY-MM-DD' + 'HH:MM' as business-local time. ValueError with a message the
    model can repeat to the caller."""
    try:
        tz = ZoneInfo(rules.timezone)
    except (ZoneInfoNotFoundError, ValueError):
        tz = ZoneInfo("UTC")
    try:
        d = date.fromisoformat(day.strip())
    except (ValueError, AttributeError):
        raise ValueError("date must look like 2026-11-05") from None
    try:
        t = time.fromisoformat(at.strip())
    except (ValueError, AttributeError):
        raise ValueError("time must look like 19:30 (24-hour)") from None
    return datetime.combine(d, t.replace(second=0, microsecond=0), tzinfo=tz)


def _minutes(hhmm: str) -> int:
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)  # "24:00" = 1440 = end of day


def _windows(rules: Rules, d: date) -> list[tuple[int, int]]:
    return [(_minutes(a), _minutes(b)) for a, b in (rules.hours or {}).get(DAYS[d.weekday()], [])]


def violation(
    rules: Rules, start: datetime, end: datetime, party_size: int | None, now: datetime, overlapping: int
) -> str | None:
    """First broken rule as a sentence for the caller, or None if the slot is fine.
    `overlapping` = confirmed bookings of this business that overlap [start, end)."""
    if start < now + timedelta(minutes=rules.lead_time_minutes):
        lead = f" at least {rules.lead_time_minutes} minutes ahead" if rules.lead_time_minutes else ""
        return f"That time is in the past or too soon; bookings must be made{lead}."
    if party_size is not None and party_size < 1:
        return "Party size must be at least 1."
    if rules.max_party_size and party_size and party_size > rules.max_party_size:
        return f"The largest party we can book is {rules.max_party_size}."
    if rules.hours is not None:
        windows = _windows(rules, start.date())
        if not windows:
            return "We are closed that day."
        s = start.hour * 60 + start.minute
        e = s + int((end - start).total_seconds() // 60)
        if not any(a <= s and e <= b for a, b in windows):
            return "That time is outside opening hours (the booking must end before closing)."
    if overlapping >= rules.capacity:
        return "That time is fully booked."
    return None


def candidate_starts(rules: Rules, start: datetime) -> list[datetime]:
    """Grid of start times on the same local day, from each opening window's start in
    `slot_minutes` steps, nearest to `start` first. Empty without configured hours."""
    out = []
    for a, b in _windows(rules, start.date()):
        m = a
        while m < b:
            out.append(datetime.combine(start.date(), time(m // 60, m % 60), tzinfo=start.tzinfo))
            m += rules.slot_minutes
    return sorted((c for c in out if c != start), key=lambda c: abs(c - start))


def to_utc(dt: datetime) -> datetime:
    return dt.astimezone(timezone.utc)
