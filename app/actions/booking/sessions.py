"""Group sessions (plan/feature-bookings-sessions.md): the owner adds a 1-hour session with a
name and a capacity; attendees are `bookings` rows with `session_id` set.

Rules are enforced here, not in the screen: create needs a future start, edit/cancel/remove
need a session that has not ended, one active session per hour, no confirmed 1:1 booking
overlapping the hour. `create_session` takes the same per-business advisory lock as
`InternalBackend.create`. Callers affected by a cancel/remove are texted (mock sender) only
after the transaction committed; a failing send never undoes the change."""

import logging
import re
import uuid
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import Text, cast, select, text
from sqlalchemy.exc import IntegrityError

from app.actions import sms
from app.actions.booking import session_events as G
from app.actions.booking.google_client import CalendarError
from app.db import tenant_session
from app.db.models import Booking, Business, BusinessProfile, GroupSession

log = logging.getLogger(__name__)

NAME_MAX = 60
MAX_ATTENDEES = 30


class _Unset:
    """update_session: a field that was not given."""


UNSET = _Unset()


class SessionError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


def _zone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo("UTC")


def local_hour_start(day: str, hour: int, tz: str) -> datetime:
    """Local `day` at `hour`:00 as a tz-aware datetime. A DST-gap hour does not exist -> 422;
    an ambiguous hour (clocks go back) is the first one (fold=0)."""
    try:
        d = date.fromisoformat(day.strip())
    except (ValueError, AttributeError):
        raise SessionError(422, "date must look like 2026-11-05") from None
    if isinstance(hour, bool) or not isinstance(hour, int) or not 0 <= hour <= 23:
        raise SessionError(422, "hour must be between 0 and 23")
    zone = _zone(tz)
    dt = datetime.combine(d, time(hour), tzinfo=zone)  # fold=0
    if dt.astimezone(timezone.utc).astimezone(zone).replace(tzinfo=None) != dt.replace(tzinfo=None):
        raise SessionError(422, "That hour does not exist on that day (clocks change).")
    return dt


def _name(value: str) -> str:
    name = (value or "").strip()
    if not 1 <= len(name) <= NAME_MAX:
        raise SessionError(422, f"name must be 1-{NAME_MAX} characters")
    return name


def _max(value: int) -> int:
    """1-30 seats."""
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= MAX_ATTENDEES:
        raise SessionError(422, f"max_attendees must be 1-{MAX_ATTENDEES}")
    return value


def _business(session, business_id: uuid.UUID) -> Business:
    business = session.get(Business, business_id)
    if business is None:
        raise SessionError(404, "business not found")
    return business


def _business_label(session, business_id: uuid.UUID, business: Business) -> str:
    name = session.scalar(select(BusinessProfile.name).where(BusinessProfile.business_id == business_id))
    return name if name and name != "(pending)" else business.name


def _get_session(session, business_id: uuid.UUID, sid: uuid.UUID, lock: bool = False) -> GroupSession:
    q = select(GroupSession).where(
        GroupSession.business_id == business_id, GroupSession.id == sid, GroupSession.status == "active"
    )
    row = session.scalars(q.with_for_update() if lock else q).first()
    if row is None:
        raise SessionError(404, "session not found")
    return row


def _confirmed(session, business_id: uuid.UUID, sids: list[uuid.UUID]) -> list[Booking]:
    if not sids:
        return []
    return list(session.scalars(
        select(Booking).where(
            Booking.business_id == business_id, Booking.session_id.in_(sids), Booking.status == "confirmed",
        ).order_by(Booking.created_at, Booking.name, Booking.id)
    ).all())


def _item(s: GroupSession, attendees: list[Booking], zone: ZoneInfo) -> dict:
    a = s.starts_at.astimezone(zone)
    start_min = a.hour * 60 + a.minute
    # length, not the wall clock (wrong on DST-change days); imported events may be longer than an hour
    end_min = min(start_min + int((s.ends_at - s.starts_at).total_seconds() // 60), 24 * 60)
    return {
        "id": str(s.id), "date": a.date().isoformat(),
        "start_min": start_min, "end_min": end_min,
        "name": s.name, "max_attendees": s.max_attendees, "private": s.private, "source": s.source,
        "booked": len(attendees),
        "attendees": [
            {"id": str(x.id), "name": x.name, "phone": x.phone, "by_assistant": x.conversation_id is not None,
             "conversation_id": str(x.conversation_id) if x.conversation_id else None}
            for x in attendees
        ],
    }


def session_item(session, business_id: uuid.UUID, s: GroupSession, zone: ZoneInfo) -> dict:
    return _item(s, _confirmed(session, business_id, [s.id]), zone)


def sessions_in_range(session, business_id: uuid.UUID, lo: datetime, hi: datetime, zone: ZoneInfo) -> list[dict]:
    """Active sessions starting in [lo, hi), with their confirmed attendees."""
    rows = session.scalars(
        select(GroupSession).where(
            GroupSession.business_id == business_id, GroupSession.status == "active",
            GroupSession.starts_at >= lo, GroupSession.starts_at < hi,
        ).order_by(GroupSession.starts_at)
    ).all()
    by_session: dict[uuid.UUID, list[Booking]] = {}
    for b in _confirmed(session, business_id, [r.id for r in rows]):
        by_session.setdefault(b.session_id, []).append(b)
    return [_item(r, by_session.get(r.id, []), zone) for r in rows]


def _check_not_ended(s: GroupSession, now: datetime) -> None:
    if s.ends_at <= now:
        raise SessionError(422, "This session has ended")


def create_session(
    business_id: uuid.UUID, day: str, hour: int, name: str, max_attendees: int, private: bool = False,
    now: datetime | None = None,
) -> dict:
    now = now or datetime.now(timezone.utc)
    name, max_attendees = _name(name), _max(max_attendees)
    with tenant_session(business_id) as session:
        business = _business(session, business_id)
        zone = _zone(business.timezone)
        start = local_hour_start(day, hour, business.timezone)
        if start <= now:
            raise SessionError(422, "A session must start in the future")
        starts = start.astimezone(timezone.utc)
        ends = starts + timedelta(hours=1)
        # same key as InternalBackend.create: serializes sessions and 1:1 bookings of one business
        session.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:b, 0))"), {"b": str(business_id)})
        if session.scalar(select(GroupSession.id).where(
            GroupSession.business_id == business_id, GroupSession.status == "active",
            GroupSession.starts_at == starts,
        )):
            raise SessionError(409, "There is already a session at that hour")
        if session.scalar(select(Booking.id).where(
            Booking.business_id == business_id, Booking.status == "confirmed", Booking.session_id.is_(None),
            Booking.starts_at < ends, Booking.ends_at > starts,
        ).limit(1)):
            raise SessionError(409, "A booking already overlaps that hour")
        row = GroupSession(
            id=uuid.uuid4(), business_id=business_id, starts_at=starts, ends_at=ends, name=name,
            max_attendees=max_attendees, private=private,
        )
        session.add(row)
        try:
            session.flush()
        except IntegrityError:
            raise SessionError(409, "There is already a session at that hour") from None
        client = G.get_client()
        if client is not None:  # a Google failure rolls the session back: it must not exist only here
            try:
                row.external_ref = G.create_event(client, G.calendar_for(business_id), row, business.timezone)
            except CalendarError as exc:
                log.warning("could not create the calendar event: %s", exc)
                raise SessionError(502, "The calendar can't be reached right now") from None
        return _item(row, [], zone)


def update_session(
    business_id: uuid.UUID, sid: uuid.UUID, name: str | None = None,
    max_attendees: int | _Unset = UNSET, private: bool | _Unset = UNSET, now: datetime | None = None,
) -> dict:
    now = now or datetime.now(timezone.utc)
    if name is not None:
        name = _name(name)
    if not isinstance(max_attendees, _Unset):
        max_attendees = _max(max_attendees)
    with tenant_session(business_id) as session:
        business = _business(session, business_id)
        row = _get_session(session, business_id, sid, lock=True)
        _check_not_ended(row, now)
        attendees = _confirmed(session, business_id, [row.id])
        if isinstance(max_attendees, int) and max_attendees < len(attendees):
            raise SessionError(422, f"{len(attendees)} callers are booked — remove some first")
        if name is not None:
            row.name = name
        if not isinstance(max_attendees, _Unset):
            row.max_attendees = max_attendees
        if not isinstance(private, _Unset):
            row.private = bool(private)
        row.updated_at = now
        session.flush()
        item = _item(row, attendees, _zone(business.timezone))
        linked = row.external_ref is not None
    if linked:
        G.push_title(business_id, sid)
    return item


def _when(s: GroupSession, zone: ZoneInfo) -> str:
    return s.starts_at.astimezone(zone).strftime("%a %d %b, %H:%M")


def _text_callers(callers: list[tuple[str | None, str]], s_name: str, when: str, who: str) -> tuple[int, int]:
    """Send after the commit. -> (texted, no_phone). A failing send is logged, never raised."""
    texted = no_phone = 0
    for phone, name in callers:
        if not phone:
            no_phone += 1
            continue
        body = f"Hi {name}, your spot in {s_name} on {when} was cancelled by {who}."
        try:
            sms.send_sms(phone, body)
            texted += 1
        except Exception:
            log.exception("could not text %s about session %s", phone, s_name)
    return texted, no_phone


def cancel_session(business_id: uuid.UUID, sid: uuid.UUID, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    with tenant_session(business_id) as session:
        business = _business(session, business_id)
        row = _get_session(session, business_id, sid, lock=True)
        _check_not_ended(row, now)
        attendees = _confirmed(session, business_id, [row.id])
        for b in attendees:
            b.status, b.cancelled_at = "cancelled", now
        row.status, row.cancelled_at, row.updated_at = "cancelled", now, now
        client = G.get_client()
        if client is not None and row.external_ref:
            try:
                G.delete_event(client, G.calendar_for(business_id), row.external_ref)
            except CalendarError as exc:
                log.warning("could not delete the calendar event: %s", exc)
                raise SessionError(502, "The calendar can't be reached right now") from None
        callers = [(b.phone, b.name) for b in attendees]
        s_name, when = row.name, _when(row, _zone(business.timezone))
        who = _business_label(session, business_id, business)
    texted, no_phone = _text_callers(callers, s_name, when, who)
    return {"cancelled_bookings": len(callers), "texted": texted, "no_phone": no_phone}


def remove_attendee(
    business_id: uuid.UUID, sid: uuid.UUID, booking_id: uuid.UUID, now: datetime | None = None
) -> tuple[dict, bool]:
    now = now or datetime.now(timezone.utc)
    with tenant_session(business_id) as session:
        business = _business(session, business_id)
        row = _get_session(session, business_id, sid, lock=True)
        _check_not_ended(row, now)
        booking = session.scalars(
            select(Booking).where(
                Booking.business_id == business_id, Booking.id == booking_id,
                Booking.session_id == sid, Booking.status == "confirmed",
            ).with_for_update()
        ).first()
        if booking is None:
            raise SessionError(404, "booking not found in this session")
        booking.status, booking.cancelled_at = "cancelled", now
        session.flush()
        zone = _zone(business.timezone)
        item = session_item(session, business_id, row, zone)
        caller = (booking.phone, booking.name)
        s_name, when = row.name, _when(row, zone)
        who = _business_label(session, business_id, business)
        linked = row.external_ref is not None
    if linked:
        G.push_title(business_id, sid)
    texted, _ = _text_callers([caller], s_name, when, who)
    return item, texted == 1


# --- calls: list_sessions / join_session -----------------------------------------------------------
# Demo rule (Max, 2026-11-01): every session is visible and joinable for every business; business_id
# is stored (the attendee row takes the session's) but nothing filters on it yet.


def list_open(
    business_id: uuid.UUID, day: str | None = None, now: datetime | None = None, limit: int = 10
) -> list[dict]:
    """Public sessions that have not ended, soonest first: {ref, name, when, booked, max_attendees, full}.
    `day` (business-local YYYY-MM-DD) narrows to that day; without it the next 30 days."""
    now = now or datetime.now(timezone.utc)
    with tenant_session(business_id) as session:
        zone = _zone(_business(session, business_id).timezone)
        lo, hi = now, now + timedelta(days=30)
        if day:
            d = local_hour_start(day, 0, zone.key)
            lo, hi = max(now, d.astimezone(timezone.utc)), (d + timedelta(days=1)).astimezone(timezone.utc)
        rows = session.scalars(
            select(GroupSession).where(
                GroupSession.status == "active", GroupSession.private.is_(False),
                GroupSession.ends_at > lo, GroupSession.starts_at < hi,
            ).order_by(GroupSession.starts_at).limit(limit)
        ).all()
        out = []
        for r in rows:
            booked = G._booked(session, r.business_id, r.id)
            a, b = r.starts_at.astimezone(zone), r.ends_at.astimezone(zone)
            out.append({
                "ref": session_ref(r.id), "name": r.name, "booked": booked, "max_attendees": r.max_attendees,
                "full": booked >= r.max_attendees,
                "when": f"{a:%a %d %b}, {a:%H:%M}-{b:%H:%M}",
            })
        return out


def session_ref(sid: uuid.UUID) -> str:
    return str(sid)[:8].upper()


def join_session(
    business_id: uuid.UUID, ref: str, conversation_id: uuid.UUID | None, name: str, phone: str | None = None,
    now: datetime | None = None,
) -> dict:
    """Put a caller (the conversation) into a session until it is full. -> {ref, name, when, booked,
    max_attendees, booking_ref}. SessionError carries the reason (full, ended, private, unknown...)."""
    now = now or datetime.now(timezone.utc)
    ref = (ref or "").strip().lower()
    if len(ref) < 6 or not re.fullmatch(r"[0-9a-f-]+", ref):
        raise SessionError(422, "a session reference is needed")
    try:
        G.sync_before_booking(business_id)  # an event just deleted or moved in Google must not be booked
    except CalendarError as exc:
        log.warning("calendar sync before join failed: %s", exc)
        raise SessionError(503, "The calendar can't be reached right now") from None
    with tenant_session(business_id) as session:
        found = session.scalars(
            select(GroupSession).where(
                GroupSession.status == "active", GroupSession.ends_at > now, cast(GroupSession.id, Text).startswith(ref),
            ).limit(2)
        ).all()
        if len(found) != 1:
            raise SessionError(404, "session not found")
        owner, sid = found[0].business_id, found[0].id
    with tenant_session(owner) as session:
        # one lock per calendar (all writers to it queue up); without Google, per business as for bookings
        key = (G.calendar_for(owner) if G.get_client() else "") or str(owner)
        session.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:b, 0))"), {"b": key})
        row = _get_session(session, owner, sid, lock=True)
        _check_not_ended(row, now)
        if row.private:
            raise SessionError(403, "This session is private")
        if conversation_id is not None and session.scalar(select(Booking.id).where(
                Booking.business_id == owner, Booking.session_id == sid, Booking.status == "confirmed",
                Booking.notes == f"conversation {conversation_id}")):
            raise SessionError(409, "This caller already has a seat in that session")
        booked = G._booked(session, owner, sid)
        if booked >= row.max_attendees:
            raise SessionError(409, "That session is full")
        # the conversation FK needs the same business; across businesses (demo) the id goes into the notes
        linked = conversation_id if owner == business_id else None
        booking = Booking(
            business_id=owner, session_id=sid, type="session", starts_at=row.starts_at, ends_at=row.ends_at,
            name=(name or "").strip() or "Caller", phone=phone, conversation_id=linked,
            notes=f"conversation {conversation_id}" if conversation_id else None,
        )
        session.add(booking)
        session.flush()
        zone = _zone(_business(session, owner).timezone)
        result = {
            "ref": session_ref(sid), "name": row.name, "booked": booked + 1, "max_attendees": row.max_attendees,
            "when": f"{row.starts_at.astimezone(zone):%a %d %b, %H:%M}", "booking_ref": str(booking.id)[:8].upper(),
        }
        linked_event = row.external_ref is not None
    if linked_event:  # after the commit; a failed patch keeps the seat, the next sync retries it
        G.push_title(owner, sid)
    return result
