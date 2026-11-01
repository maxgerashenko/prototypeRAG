"""Bookings API for the Bookings screen (stage 1, no login yet):

GET /businesses/{id}/bookings?start=YYYY-MM-DD&end=YYYY-MM-DD  confirmed bookings whose local
start day is in [start, end] (at most 62 days), plus the hours the day view should show.

Times are returned in the business timezone as minutes since local midnight, so the screen
needs no timezone maths. `by_assistant` = the booking came out of a conversation (a call or
chat); a booking without one was made by hand (SQL). Events typed straight into Google Calendar
come in through the calendar sync as `sessions` with source "google" (#149/#167). `busy` is the switch for
other businesses' events as grey "Busy" blocks once calendars are per business: always [] in the demo. A booking that runs past local midnight is
cut at 24:00 (the day view shows one day)."""

import uuid
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import select

from app.actions.booking import rules as R
from app.actions.booking.internal import _load_rules
from app.actions.booking.sessions import sessions_in_range
from app.db import tenant_session
from app.db.models import Booking, Business

router = APIRouter()

MAX_RANGE_DAYS = 62
DEFAULT_OPEN, DEFAULT_CLOSE = 8, 20  # day view range when the business has no hours rule


def _zone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo("UTC")


def _day(value: str, field: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise HTTPException(status_code=422, detail=f"{field} must look like 2026-11-05") from None


def open_hours(rules: R.Rules) -> dict | None:
    """Earliest opening and latest closing hour over the week, or None without hours."""
    windows = [w for day in (rules.hours or {}).values() for w in day]
    if not windows:
        return None
    spans = [(R._minutes(a), R._minutes(b)) for a, b in windows]
    lo = min(a for a, _ in spans)
    hi = max(b if b > a else 24 * 60 for a, b in spans)  # an overnight window (18:00-02:00) runs to midnight
    hi = min(hi, 24 * 60)
    return {"open": lo // 60, "close": -(-hi // 60)}  # close rounded up to a full hour


@router.get("/businesses/{business_id}/bookings")
def list_bookings(
    business_id: uuid.UUID,
    start: str = Query(...),
    end: str = Query(...),
) -> dict:
    first, last = _day(start, "start"), _day(end, "end")
    if last < first or (last - first).days >= MAX_RANGE_DAYS:
        raise HTTPException(status_code=422, detail=f"end must be on or after start, within {MAX_RANGE_DAYS} days")
    with tenant_session(business_id) as session:
        business = session.get(Business, business_id)
        if business is None:
            raise HTTPException(status_code=404, detail="business not found")
        zone = _zone(business.timezone)
        rules = _load_rules(session, business_id)
        # local midnight; a zone whose midnight doesn't exist (DST gap at 00:00) is off by an hour
        lo = datetime.combine(first, time.min, tzinfo=zone)
        hi = datetime.combine(last + timedelta(days=1), time.min, tzinfo=zone)
        rows = session.scalars(
            select(Booking).where(
                Booking.business_id == business_id, Booking.status == "confirmed",
                Booking.session_id.is_(None), Booking.starts_at >= lo, Booking.starts_at < hi,
            ).order_by(Booking.starts_at)
        ).all()
        out = []
        for b in rows:
            s, e = b.starts_at.astimezone(zone), b.ends_at.astimezone(zone)
            start_min = s.hour * 60 + s.minute
            # wall-clock end, so a DST change inside the booking doesn't shift the block
            end_min = e.hour * 60 + e.minute + (e.date() - s.date()).days * 24 * 60
            out.append({
                "id": str(b.id), "date": s.date().isoformat(),
                "start_min": start_min, "end_min": min(end_min, 24 * 60),
                "name": b.name, "service": b.service, "party_size": b.party_size,
                "phone": b.phone, "notes": b.notes, "by_assistant": b.conversation_id is not None,
            })
        sessions = sessions_in_range(session, business_id, lo, hi, zone)
    return {"timezone": zone.key, "hours": open_hours(rules), "bookings": out, "sessions": sessions, "busy": []}
