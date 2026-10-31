"""Google Calendar booking backend (DEC-48, plan/04-actions.md).

Same rules as the internal backend (`rules.py`: hours, lead time, party size, capacity),
plus Google's free/busy: the owner's own calendar entries block slots too.

- availability: rules + the day's confirmed `bookings` rows + `freeBusy` of the calendar.
- create: under the per-business advisory lock it re-runs the rules, asks `freeBusy`
  again, and only then calls `events.insert`; the `bookings` row (the double-booking lock,
  Google has no transaction) is written with the event id in `external_ref`. If the row
  can't be written the event is deleted again; if Google fails nothing is booked.
- cancel: deletes the event found by `external_ref`, then marks the row cancelled. If Google
  can't delete it the booking stays confirmed (a stale event would keep blocking the slot).

Capacity: `freeBusy` merges overlapping events into one busy block, so it can't be counted.
With capacity 1 any busy block blocks the slot; with capacity > 1 only the `bookings` rows
are counted (calendar entries by the owner are then not seen). Pilot limitation.

The event carries name, phone, service and notes in the description, and `business_id`,
`conversation_id` and `booking_id` as private extended properties. Nobody is invited.
"""

import logging
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import Text, cast, select, text

from app.actions.booking import rules as R
from app.actions.booking.google_client import CalendarClient, CalendarError
from app.actions.booking.internal import (
    MIN_REF, REF_LEN, Availability, BookingRequest, BookingResult, _check, _day_bookings, _load_rules,
)
from app.db import tenant_session
from app.db.models import Booking, Business, BusinessProfile

log = logging.getLogger(__name__)

UNAVAILABLE = "The booking calendar can't be reached right now."


def calendar_id_for(business_id: uuid.UUID, default: str = "") -> str:
    """`businesses.settings.google_calendar_id`, else the env default (pilot: one calendar)."""
    with tenant_session(business_id) as session:
        settings = session.scalar(select(Business.settings).where(Business.id == business_id)) or {}
    return str(settings.get("google_calendar_id") or default).strip()


def _event_body(booking_id: uuid.UUID, business_id: uuid.UUID, request: BookingRequest,
                start: datetime, end: datetime, tz: str, ref: str) -> dict:
    what = request.service or (f"table for {request.party_size}" if request.party_size else "booking")
    lines = [f"Name: {request.name.strip()}"]
    if request.phone:
        lines.append(f"Phone: {request.phone}")
    if request.service:
        lines.append(f"Service: {request.service}")
    if request.party_size:
        lines.append(f"People: {request.party_size}")
    if request.notes:
        lines.append(f"Notes: {request.notes.strip()}")
    lines.append(f"Reference: {ref}")
    private = {"business_id": str(business_id), "booking_id": str(booking_id)}
    if request.conversation_id:
        private["conversation_id"] = str(request.conversation_id)
    return {
        "summary": f"{request.name.strip()} - {what}",
        "description": "\n".join(lines),
        "start": {"dateTime": start.isoformat(), "timeZone": tz},
        "end": {"dateTime": end.isoformat(), "timeZone": tz},
        "extendedProperties": {"private": private},
        "reminders": {"useDefault": False},
    }


class GoogleCalendarBackend:
    def __init__(self, client: CalendarClient, default_calendar_id: str = ""):
        self.client = client
        self.default_calendar_id = default_calendar_id

    def _calendar(self, business_id: uuid.UUID) -> str:
        return calendar_id_for(business_id, self.default_calendar_id)

    def _busy(self, calendar_id: str, start: datetime) -> list[tuple[datetime, datetime]]:
        """Busy blocks around the local day of `start` (same +-1 day margin as the rows)."""
        return self.client.busy(calendar_id, R.to_utc(start - timedelta(days=1)), R.to_utc(start + timedelta(days=2)))

    @staticmethod
    def _existing(rules: R.Rules, rows, busy):
        # capacity > 1: freeBusy blocks are merged, so they can't be counted (module docstring)
        return rows + busy if rules.capacity == 1 else rows

    def availability(self, business_id: uuid.UUID, request: BookingRequest, now: datetime | None = None) -> Availability:
        now = now or datetime.now(timezone.utc)
        calendar_id = self._calendar(business_id)
        if not calendar_id:
            return Availability(False, UNAVAILABLE)
        try:
            with tenant_session(business_id) as session:
                rules = _load_rules(session, business_id)
                start = R.local_start(rules, request.day, request.at)
                rows = _day_bookings(session, business_id, start)
            busy = self._busy(calendar_id, start)
            _, _, reason, alts = _check(rules, request, self._existing(rules, rows, busy), now)
        except ValueError as exc:
            return Availability(False, str(exc))
        except CalendarError as exc:
            log.warning("google calendar availability failed: %s", exc)
            return Availability(False, UNAVAILABLE)
        return Availability(reason is None, reason, alts)

    def create(self, business_id: uuid.UUID, request: BookingRequest, now: datetime | None = None) -> BookingResult:
        now = now or datetime.now(timezone.utc)
        if not request.name.strip():
            return BookingResult(False, "A name for the booking is needed.")
        calendar_id = self._calendar(business_id)
        if not calendar_id:
            return BookingResult(False, UNAVAILABLE)
        event_id: str | None = None
        try:
            with tenant_session(business_id) as session:
                session.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:b, 0))"), {"b": str(business_id)})
                rules = _load_rules(session, business_id)
                try:
                    start = R.local_start(rules, request.day, request.at)
                except ValueError as exc:
                    return BookingResult(False, str(exc))
                rows = _day_bookings(session, business_id, start)
                # the re-check right before events.insert (DEC-48): fresh free/busy, inside the lock
                start, end, reason, alts = _check(rules, request, self._existing(rules, rows, self._busy(calendar_id, start)), now)
                if reason:
                    return BookingResult(False, reason, alternatives=alts)
                booking_id = uuid.uuid4()
                ref = str(booking_id)[:REF_LEN].upper()
                event_id = self.client.insert_event(
                    calendar_id, _event_body(booking_id, business_id, request, start, end, rules.timezone, ref)
                )
                session.add(Booking(
                    id=booking_id, business_id=business_id, conversation_id=request.conversation_id,
                    type="appointment" if request.service else "table",
                    starts_at=R.to_utc(start), ends_at=R.to_utc(end), party_size=request.party_size,
                    service=(request.service or "").strip() or None, name=request.name.strip(),
                    phone=request.phone, notes=(request.notes or "").strip() or None, external_ref=event_id,
                ))
                session.flush()
                name = session.scalar(select(BusinessProfile.name).where(BusinessProfile.business_id == business_id))
                if not name or name == "(pending)":
                    name = session.scalar(select(Business.name).where(Business.id == business_id))
                return BookingResult(True, "Booked.", booking_id, ref, name, start.strftime("%a %d %b, %H:%M"))
        except CalendarError as exc:
            log.warning("google calendar create failed: %s", exc)
            return BookingResult(False, UNAVAILABLE)
        except Exception:
            # the row could not be written (rolled back): don't leave an orphan event blocking the slot
            if event_id:
                self._delete_quietly(calendar_id, event_id)
            raise

    def _delete_quietly(self, calendar_id: str, event_id: str) -> None:
        try:
            self.client.delete_event(calendar_id, event_id)
        except CalendarError:
            log.error("orphan google calendar event %s in %s could not be deleted", event_id, calendar_id)

    def cancel(self, business_id: uuid.UUID, reference: str, phone: str | None, now: datetime | None = None) -> str:
        """Cancel by reference; the phone must match the booking's (as in the internal backend)."""
        ref = (reference or "").strip().lower()
        if len(ref) < MIN_REF or not phone:
            return "Error: a booking reference and the caller's phone number are needed."
        now = now or datetime.now(timezone.utc)
        calendar_id = self._calendar(business_id)
        with tenant_session(business_id) as session:
            rows = session.scalars(
                select(Booking).where(
                    Booking.business_id == business_id, Booking.status == "confirmed",
                    Booking.phone == phone, Booking.starts_at > now, cast(Booking.id, Text).startswith(ref),
                ).with_for_update()
            ).all()
            if len(rows) != 1:
                return "No upcoming booking found with that reference for this phone number."
            booking = rows[0]
            if booking.external_ref:
                try:
                    self.client.delete_event(calendar_id, booking.external_ref)
                except CalendarError as exc:
                    log.warning("google calendar cancel failed: %s", exc)
                    return f"Error: not cancelled. {UNAVAILABLE}"
            booking.status = "cancelled"
            booking.cancelled_at = now
            return f"Cancelled booking {str(booking.id)[:REF_LEN].upper()}."
