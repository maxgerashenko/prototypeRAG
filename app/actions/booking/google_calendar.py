"""Google Calendar booking backend (DEC-48, plan/04-actions.md).

Same rules as the internal backend (`rules.py`: hours, lead time, party size, capacity),
but busy time comes from the calendar mirror (`mirror.py`, #165), not `freeBusy`.

- availability: one incremental sync, then rules + every opaque event of the shared
  calendar (any business, hand-typed ones too) + this business's confirmed `bookings` rows
  whose event the mirror doesn't show (yet). Events are counted one by one, so capacity > 1
  works (`freeBusy` merged overlapping events).
- create: ONE advisory lock per calendar (`hashtext(calendar_id)`), not per business: in the
  demo all businesses share one calendar (Max, 2026-11-01), so all writers queue up. Inside
  it: forced sync, rules re-check, `events.insert` with our own event id derived from the
  booking id (base32hex) -- a retry after a timeout gets 409 (= already there), never a
  duplicate -- then the `bookings` row with the event id in `external_ref`. If the row can't
  be written the event is deleted again; if Google fails nothing is booked.
- cancel: deletes the event found by `external_ref`, then marks the row cancelled. If Google
  can't delete it the booking stays confirmed (a stale event would keep blocking the slot).

Event: summary "Name - service"; description with the name, last 4 digits of the phone, the
booking reference and a link to the conversation (`APP_BASE_URL/web/#conversation=<id>`);
`business_id`, `booking_id` and `conversation_id` as private extended properties. Nobody is
invited (a service account can't add attendees on a Gmail calendar). The caller is the
conversation; the caller-ID phone is saved on the booking but not required.
"""

import base64
import logging
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import Text, cast, select, text

from app.actions.booking import rules as R
from app.actions.booking.google_client import CalendarClient, CalendarError
from app.actions.booking.mirror import CalendarMirror, FreeBusyMirror
from app.actions.booking.internal import (
    MIN_REF, REF_LEN, Availability, BookingRequest, BookingResult, _check, _load_rules,
)
from app.config import get_settings
from app.db import tenant_session
from app.db.models import Booking, Business, BusinessProfile

log = logging.getLogger(__name__)

UNAVAILABLE = "The booking calendar can't be reached right now."


def calendar_id_for(business_id: uuid.UUID, default: str = "") -> str:
    """`businesses.settings.google_calendar_id`, else the env default (pilot: one calendar)."""
    with tenant_session(business_id) as session:
        settings = session.scalar(select(Business.settings).where(Business.id == business_id)) or {}
    return str(settings.get("google_calendar_id") or default).strip()


def event_id_for(booking_id: uuid.UUID) -> str:
    """Our own Google event id: the booking id in base32hex (26 chars of 0-9a-v, which is
    what Google allows). A retried insert for the same booking gets 409, not a second event."""
    return base64.b32hexencode(booking_id.bytes).decode().rstrip("=").lower()


def _conversation_link(conversation_id: uuid.UUID) -> str:
    return f"{get_settings().app_base_url.rstrip('/')}/web/#conversation={conversation_id}"


def _event_body(booking_id: uuid.UUID, business_id: uuid.UUID, request: BookingRequest,
                start: datetime, end: datetime, tz: str, ref: str) -> dict:
    what = request.service or (f"table for {request.party_size}" if request.party_size else "booking")
    lines = [f"Name: {request.name.strip()}"]
    digits = "".join(c for c in (request.phone or "") if c.isdigit())
    if digits:
        lines.append(f"Phone: ...{digits[-4:]}")  # the full number stays in our DB
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
        lines.append(f"Conversation: {_conversation_link(request.conversation_id)}")
    return {
        "id": event_id_for(booking_id),
        "summary": f"{request.name.strip()} - {what}",
        "description": "\n".join(lines),
        "start": {"dateTime": start.isoformat(), "timeZone": tz},
        "end": {"dateTime": end.isoformat(), "timeZone": tz},
        "extendedProperties": {"private": private},
        "reminders": {"useDefault": False},
    }


class GoogleCalendarBackend:
    def __init__(self, client: CalendarClient, default_calendar_id: str = "", mirror: CalendarMirror | None = None):
        self.client = client
        self.default_calendar_id = default_calendar_id
        self.mirror = mirror or FreeBusyMirror(client)

    def _calendar(self, business_id: uuid.UUID) -> str:
        return calendar_id_for(business_id, self.default_calendar_id)

    def _existing(self, session, business_id: uuid.UUID, rules: R.Rules, calendar_id: str, start: datetime):
        """Busy intervals around the local day of `start` (+-1 day margin for long ones): the
        mirror's events, plus this business's confirmed rows the mirror doesn't show."""
        lo, hi = R.to_utc(start - timedelta(days=1)), R.to_utc(start + timedelta(days=2))
        events = self.mirror.busy_events(calendar_id, lo, hi)
        rows = session.execute(
            select(Booking.starts_at, Booking.ends_at, Booking.external_ref).where(
                Booking.business_id == business_id, Booking.status == "confirmed",
                Booking.starts_at < hi, Booking.ends_at > lo,
            )
        ).all()
        if not self.mirror.exact:
            # merged free/busy blocks can't be counted: capacity 1 -> any block blocks the slot;
            # capacity > 1 -> only our rows count (the owner's own entries aren't seen)
            blocks = [(e.start, e.end) for e in events] if rules.capacity == 1 else []
            return [(a, b) for a, b, _ in rows] + blocks
        seen = {e.event_id for e in events}
        unseen = [(a, b) for a, b, ref in rows if ref not in seen]  # just written, or deleted in Google
        return [(e.start, e.end) for e in events] + unseen

    def availability(self, business_id: uuid.UUID, request: BookingRequest, now: datetime | None = None) -> Availability:
        now = now or datetime.now(timezone.utc)
        calendar_id = self._calendar(business_id)
        if not calendar_id:
            return Availability(False, UNAVAILABLE)
        try:
            self.mirror.sync(calendar_id)
            with tenant_session(business_id) as session:
                rules = _load_rules(session, business_id)
                start = R.local_start(rules, request.day, request.at)
                existing = self._existing(session, business_id, rules, calendar_id, start)
            _, _, reason, alts = _check(rules, request, existing, now)
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
                # one lock per CALENDAR: every business on it queues here; released at commit/rollback
                session.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:c, 0))"), {"c": calendar_id})
                rules = _load_rules(session, business_id)
                try:
                    start = R.local_start(rules, request.day, request.at)
                except ValueError as exc:
                    return BookingResult(False, str(exc))
                # forced sync + re-check right before events.insert (DEC-48), inside the lock
                self.mirror.sync(calendar_id)
                existing = self._existing(session, business_id, rules, calendar_id, start)
                start, end, reason, alts = _check(rules, request, existing, now)
                if reason:
                    return BookingResult(False, reason, alternatives=alts)
                booking_id = uuid.uuid4()
                ref = str(booking_id)[:REF_LEN].upper()
                body = _event_body(booking_id, business_id, request, start, end, rules.timezone, ref)
                event_id = body["id"]  # set first: if the insert times out, the event may still exist
                self._insert(calendar_id, body)
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
            if event_id:  # the insert failed twice, but a timed-out call may have gone through
                self._delete_quietly(calendar_id, event_id)
            return BookingResult(False, UNAVAILABLE)
        except Exception:
            # the row could not be written (rolled back): don't leave an orphan event blocking the slot
            if event_id:
                self._delete_quietly(calendar_id, event_id)
            raise

    def _insert(self, calendar_id: str, body: dict) -> None:
        """events.insert, once more after a failure. The event id is ours, so if the first call
        reached Google after all, the retry gets 409 (the client reads that as success)."""
        try:
            self.client.insert_event(calendar_id, body)
        except CalendarError as exc:
            log.warning("events.insert failed, retrying once: %s", exc)
            self.client.insert_event(calendar_id, body)

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
