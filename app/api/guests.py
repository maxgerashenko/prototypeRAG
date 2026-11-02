"""Guest history and merge API (plan/feature-guest-identity.md steps 8 and 10; stage 1, no login yet).

GET  /businesses/{id}/guests      -> [{id, display_name, email, phone (last 4), booking_count, last_booking_at}]
GET  /guests/{id}                 -> the guest (the survivor, if it was merged) + bookings + sessions, newest first
POST /guests/{id}/merge {into}    -> the survivor; the owner merges two guests of the same business

The /guests/{id} routes carry no business id: the guest row says which business it belongs to,
and everything after that runs in tenant_session(that business) and filters by business_id.
Merging only moves the data here; Google event attendees are not updated (see the plan)."""

import uuid
from datetime import datetime

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, select

from app.db import tenant_session
from app.db.models import Booking, GroupSession, SessionGuest
from app.db.session import SessionLocal

router = APIRouter()


class MergeIn(BaseModel):
    into: uuid.UUID


def _mask(phone: str | None) -> str | None:
    """Last 4 digits only: '***2284'."""
    digits = "".join(c for c in (phone or "") if c.isdigit())
    return f"***{digits[-4:]}" if digits else None


def _guest_out(g: SessionGuest, phone_full: bool = False) -> dict:
    return {
        "id": str(g.id),
        "display_name": g.display_name,
        "email": g.email,
        "phone": g.phone if phone_full else _mask(g.phone),
        "source": g.source,
        "created_at": g.created_at,
    }


def _business_of(guest_id: uuid.UUID) -> uuid.UUID:
    """The business a guest belongs to (the only lookup without a business scope: the path has none)."""
    with SessionLocal() as s:
        bid = s.scalar(select(SessionGuest.business_id).where(SessionGuest.id == guest_id))
    if bid is None:
        raise HTTPException(status_code=404, detail="Guest not found")
    return bid


def _survivor(s, business_id: uuid.UUID, guest_id: uuid.UUID) -> SessionGuest:
    """The guest, or the one it was merged into (followed to the end; the hop count guards a bad cycle)."""
    g = s.scalar(select(SessionGuest).where(SessionGuest.business_id == business_id, SessionGuest.id == guest_id))
    for _ in range(50):
        if g is None:
            raise HTTPException(status_code=404, detail="Guest not found")
        if g.merged_into is None:
            return g
        g = s.scalar(
            select(SessionGuest).where(SessionGuest.business_id == business_id, SessionGuest.id == g.merged_into)
        )
    raise HTTPException(status_code=409, detail="Guest merge chain is broken")


@router.get("/businesses/{business_id}/guests")
def list_guests(business_id: uuid.UUID) -> list[dict]:
    with tenant_session(business_id) as s:
        guests = s.scalars(
            select(SessionGuest)
            .where(SessionGuest.business_id == business_id, SessionGuest.merged_into.is_(None))
            .order_by(SessionGuest.display_name, SessionGuest.id)
        ).all()
        stats = {
            gid: (n, last)
            for gid, n, last in s.execute(
                select(Booking.guest_id, func.count(Booking.id), func.max(Booking.starts_at))
                .where(
                    Booking.business_id == business_id,
                    Booking.guest_id.is_not(None),
                    Booking.status == "confirmed",
                )
                .group_by(Booking.guest_id)
            )
        }
        out = []
        for g in guests:
            n, last = stats.get(g.id, (0, None))
            out.append({
                "id": str(g.id),
                "display_name": g.display_name,
                "email": g.email,
                "phone": _mask(g.phone),
                "booking_count": n,
                "last_booking_at": last,
            })
        return out


@router.get("/guests/{guest_id}")
def get_guest(guest_id: uuid.UUID) -> dict:
    business_id = _business_of(guest_id)
    with tenant_session(business_id) as s:
        g = _survivor(s, business_id, guest_id)
        rows = s.execute(
            select(Booking, GroupSession.name)
            .outerjoin(
                GroupSession,
                (GroupSession.business_id == Booking.business_id) & (GroupSession.id == Booking.session_id),
            )
            .where(Booking.business_id == business_id, Booking.guest_id == g.id)
            .order_by(Booking.starts_at.desc(), Booking.id)
        ).all()
        bookings, sessions, seen = [], [], set()
        for b, session_name in rows:
            bookings.append({
                "id": str(b.id),
                "type": b.type,
                "status": b.status,
                "starts_at": b.starts_at,
                "ends_at": b.ends_at,
                "service": b.service,
                "party_size": b.party_size,
                "session_id": str(b.session_id) if b.session_id else None,
            })
            if b.session_id and b.session_id not in seen:
                seen.add(b.session_id)
                sessions.append({
                    "id": str(b.session_id), "name": session_name, "starts_at": b.starts_at, "ends_at": b.ends_at,
                    "booking_status": b.status,
                })
        return {**_guest_out(g), "requested_id": str(guest_id), "bookings": bookings, "sessions": sessions}


@router.post("/guests/{guest_id}/merge")
def merge_guest(guest_id: uuid.UUID, body: MergeIn) -> dict:
    business_id = _business_of(guest_id)
    with tenant_session(business_id) as s:
        # lock both rows in id order, so concurrent A->B and B->A merges cannot both pass and form a cycle
        ids = sorted({guest_id, body.into}, key=str)
        locked = {g.id: g for g in s.scalars(
            select(SessionGuest).where(SessionGuest.business_id == business_id, SessionGuest.id.in_(ids))
            .order_by(SessionGuest.id).with_for_update())}
        source = locked.get(guest_id)
        if source is None:
            raise HTTPException(status_code=404, detail="Guest not found")
        if source.merged_into is not None:
            raise HTTPException(status_code=409, detail="Guest is already merged")
        # only a guest of the same business is found; the target is followed to its survivor
        target = _survivor(s, business_id, body.into)
        if target.id == source.id:
            raise HTTPException(status_code=400, detail="Cannot merge a guest into itself")
        if target.id not in locked:
            s.scalar(select(SessionGuest).where(SessionGuest.id == target.id).with_for_update()
                     .execution_options(populate_existing=True))  # followed to a survivor: lock and re-read it
        if target.merged_into is not None:
            raise HTTPException(status_code=409, detail="Guest is already merged")
        shared = s.scalar(
            select(Booking.session_id).where(
                Booking.business_id == business_id, Booking.guest_id == source.id,
                Booking.status == "confirmed", Booking.session_id.is_not(None),
                Booking.session_id.in_(
                    select(Booking.session_id).where(
                        Booking.business_id == business_id, Booking.guest_id == target.id,
                        Booking.status == "confirmed", Booking.session_id.is_not(None))))
            .limit(1))
        if shared is not None:
            raise HTTPException(
                status_code=409,
                detail="Both guests have a seat in the same session; cancel one of the seats first, then merge")
        # the target is unmerged and so is the source: no cycle is possible. Guests merged earlier
        # into the source now point at the survivor too, so the chain stays one hop.
        for old in s.scalars(
            select(SessionGuest).where(SessionGuest.business_id == business_id, SessionGuest.merged_into == source.id)
        ):
            old.merged_into = target.id
        source.merged_into = target.id
        for b in s.scalars(select(Booking).where(Booking.business_id == business_id, Booking.guest_id == source.id)):
            b.guest_id = target.id
        s.flush()
        return _guest_out(target)
