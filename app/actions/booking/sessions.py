"""Group sessions (plan/feature-bookings-sessions.md): the owner adds a 1-hour session with a
name and a capacity; attendees are `bookings` rows with `session_id` set.

Rules are enforced here, not in the screen: create needs a future start, edit/cancel/remove
need a session that has not ended, one active session per hour, no confirmed 1:1 booking
overlapping the hour. `create_session` takes the same per-business advisory lock as
`InternalBackend.create`. Callers affected by a cancel/remove are texted (mock sender) only
after the transaction committed; a failing send never undoes the change."""

import logging
import uuid
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

from app.actions import sms
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
    # a session is always exactly 1 hour; the wall clock would be wrong on DST-change days
    end_min = min(start_min + 60, 24 * 60)
    return {
        "id": str(s.id), "date": a.date().isoformat(),
        "start_min": start_min, "end_min": end_min,
        "name": s.name, "max_attendees": s.max_attendees, "private": s.private,
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
            business_id=business_id, starts_at=starts, ends_at=ends, name=name, max_attendees=max_attendees,
            private=private,
        )
        session.add(row)
        try:
            session.flush()
        except IntegrityError:
            raise SessionError(409, "There is already a session at that hour") from None
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
        return _item(row, attendees, _zone(business.timezone))


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
    texted, _ = _text_callers([caller], s_name, when, who)
    return item, texted == 1
