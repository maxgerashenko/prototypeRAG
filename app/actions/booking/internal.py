"""Internal booking backend: Postgres `bookings` + `booking_rules` (plan/04-actions.md).

The first backend, no external dependency. `BookingBackend` is the interface a Google
Calendar backend (DEC-48, google_calendar.py) implements too.

Double-booking: `create` takes a per-business advisory lock inside its transaction, reads
the day's confirmed bookings, applies the rules, and inserts -- two calls for the same slot
can't both pass the capacity check. Capacity counts bookings that overlap the requested
interval at all (conservative: with capacity 1, back-to-back bookings are fine, anything
overlapping is not).
"""

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Protocol

from sqlalchemy import Text, cast, select, text

from app.actions.booking import rules as R
from app.db import tenant_session
from app.db.models import Booking, BookingRule, Business, BusinessProfile

REF_LEN = 8  # booking reference = first 8 hex chars of the id, read out to the caller
MIN_REF = 6


@dataclass(frozen=True)
class BookingRequest:
    day: str  # YYYY-MM-DD, business-local
    at: str  # HH:MM, business-local
    party_size: int | None = None
    service: str | None = None
    name: str = ""
    phone: str | None = None
    notes: str | None = None
    conversation_id: uuid.UUID | None = None


@dataclass
class Availability:
    ok: bool
    reason: str | None = None
    alternatives: list[str] = field(default_factory=list)  # local "HH:MM" on the same day


@dataclass
class BookingResult:
    ok: bool
    message: str
    booking_id: uuid.UUID | None = None
    reference: str | None = None
    business_name: str | None = None
    when: str | None = None  # "Sat 05 Nov, 14:00" in business time
    alternatives: list[str] = field(default_factory=list)


class BookingBackend(Protocol):
    def availability(self, business_id: uuid.UUID, request: BookingRequest) -> Availability: ...
    def create(self, business_id: uuid.UUID, request: BookingRequest) -> BookingResult: ...
    def cancel(self, business_id: uuid.UUID, reference: str, phone: str | None) -> str: ...


def _load_rules(session, business_id: uuid.UUID) -> R.Rules:
    tz = session.scalar(select(Business.timezone).where(Business.id == business_id)) or "UTC"
    row = session.get(BookingRule, business_id)
    if row is None:
        return R.Rules(timezone=tz)
    return R.Rules(
        timezone=tz, slot_minutes=row.slot_minutes, capacity=row.capacity,
        lead_time_minutes=row.lead_time_minutes, max_party_size=row.max_party_size,
        hours=row.hours, service_durations={k.lower(): v for k, v in (row.service_durations or {}).items()},
    )


def _day_bookings(session, business_id: uuid.UUID, start: datetime) -> list[tuple[datetime, datetime]]:
    """Confirmed bookings that touch the local day of `start` (+-1 day margin for long ones)."""
    lo = R.to_utc(start - timedelta(days=1))
    hi = R.to_utc(start + timedelta(days=2))
    rows = session.execute(
        select(Booking.starts_at, Booking.ends_at).where(
            Booking.business_id == business_id, Booking.status == "confirmed",
            Booking.starts_at < hi, Booking.ends_at > lo,
        )
    ).all()
    return [(a, b) for a, b in rows]


def _overlapping(existing: list[tuple[datetime, datetime]], start: datetime, end: datetime) -> int:
    return sum(1 for a, b in existing if a < end and b > start)


def _check(rules: R.Rules, req: BookingRequest, existing, now: datetime):
    """-> (start, end, reason, alternatives) for one request."""
    start = R.local_start(rules, req.day, req.at)
    length = timedelta(minutes=rules.duration(req.service))
    end = start + length
    reason = R.violation(rules, start, end, req.party_size, now, _overlapping(existing, start, end))
    alternatives: list[str] = []
    if reason:
        for cand in R.candidate_starts(rules, start):
            if R.violation(rules, cand, cand + length, req.party_size, now, _overlapping(existing, cand, cand + length)) is None:
                alternatives.append(cand.strftime("%H:%M"))
                if len(alternatives) == R.MAX_ALTERNATIVES:
                    break
    return start, end, reason, alternatives


class InternalBackend:
    def availability(self, business_id: uuid.UUID, request: BookingRequest, now: datetime | None = None) -> Availability:
        now = now or datetime.now(timezone.utc)
        try:
            with tenant_session(business_id) as session:
                rules = _load_rules(session, business_id)
                start = R.local_start(rules, request.day, request.at)
                _, _, reason, alts = _check(rules, request, _day_bookings(session, business_id, start), now)
        except ValueError as exc:
            return Availability(False, str(exc))
        return Availability(reason is None, reason, alts)

    def create(self, business_id: uuid.UUID, request: BookingRequest, now: datetime | None = None) -> BookingResult:
        now = now or datetime.now(timezone.utc)
        if not request.name.strip():
            return BookingResult(False, "A name for the booking is needed.")
        with tenant_session(business_id) as session:
            # serialize bookings of one business; released at commit/rollback
            session.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:b, 0))"), {"b": str(business_id)})
            rules = _load_rules(session, business_id)
            try:
                start = R.local_start(rules, request.day, request.at)
            except ValueError as exc:
                return BookingResult(False, str(exc))
            start, end, reason, alts = _check(rules, request, _day_bookings(session, business_id, start), now)
            if reason:
                return BookingResult(False, reason, alternatives=alts)
            booking = Booking(
                business_id=business_id, conversation_id=request.conversation_id,
                type="appointment" if request.service else "table",
                starts_at=R.to_utc(start), ends_at=R.to_utc(end), party_size=request.party_size,
                service=(request.service or "").strip() or None, name=request.name.strip(),
                phone=request.phone, notes=(request.notes or "").strip() or None,
            )
            session.add(booking)
            session.flush()
            name = session.scalar(select(BusinessProfile.name).where(BusinessProfile.business_id == business_id))
            if not name or name == "(pending)":
                name = session.scalar(select(Business.name).where(Business.id == business_id))
            return BookingResult(
                True, "Booked.", booking.id, str(booking.id)[:REF_LEN].upper(), name,
                start.strftime("%a %d %b, %H:%M"),
            )

    def cancel(self, business_id: uuid.UUID, reference: str, phone: str | None, now: datetime | None = None) -> str:
        """Cancel by reference; the phone must match the booking's, so a caller can only
        cancel their own booking."""
        ref = (reference or "").strip().lower()
        if len(ref) < MIN_REF or not phone:
            return "Error: a booking reference and the caller's phone number are needed."
        now = now or datetime.now(timezone.utc)
        with tenant_session(business_id) as session:
            rows = session.scalars(
                select(Booking).where(
                    Booking.business_id == business_id, Booking.status == "confirmed",
                    Booking.phone == phone, Booking.starts_at > now, cast(Booking.id, Text).startswith(ref),
                ).with_for_update()
            ).all()
            if len(rows) != 1:
                return "No upcoming booking found with that reference for this phone number."
            rows[0].status = "cancelled"
            rows[0].cancelled_at = now
            return f"Cancelled booking {str(rows[0].id)[:REF_LEN].upper()}."
