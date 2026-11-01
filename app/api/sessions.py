"""Group sessions API for the Bookings screen (stage 1, no login yet; OPEN-19). All rules
live in app/actions/booking/sessions.py; this maps them to HTTP.

POST   /businesses/{id}/sessions                              {date, hour, name, max_attendees, private?} -> 201 item
PATCH  /businesses/{id}/sessions/{sid}                        {name?, max_attendees?, private?} -> item
DELETE /businesses/{id}/sessions/{sid}                        -> {cancelled_bookings, texted, no_phone}
DELETE /businesses/{id}/sessions/{sid}/attendees/{booking_id} -> item + {"texted": bool}"""

import uuid

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.actions.booking import sessions as S

router = APIRouter()


class SessionIn(BaseModel):
    date: str
    hour: int
    name: str
    max_attendees: int
    private: bool = False  # closed to the public: the assistant never books into it


class SessionPatch(BaseModel):
    name: str | None = None
    max_attendees: int | None = None
    private: bool | None = None


def _fail(exc: S.SessionError) -> HTTPException:
    return HTTPException(status_code=exc.status, detail=exc.message)


@router.post("/businesses/{business_id}/sessions", status_code=201)
def create_session(business_id: uuid.UUID, body: SessionIn) -> dict:
    try:
        return S.create_session(business_id, body.date, body.hour, body.name, body.max_attendees, body.private)
    except S.SessionError as exc:
        raise _fail(exc) from None


@router.patch("/businesses/{business_id}/sessions/{session_id}")
def update_session(business_id: uuid.UUID, session_id: uuid.UUID, body: SessionPatch) -> dict:
    try:
        seats = S.UNSET if body.max_attendees is None else body.max_attendees
        priv = S.UNSET if body.private is None else body.private
        return S.update_session(business_id, session_id, body.name, seats, priv)
    except S.SessionError as exc:
        raise _fail(exc) from None


@router.delete("/businesses/{business_id}/sessions/{session_id}")
def cancel_session(business_id: uuid.UUID, session_id: uuid.UUID) -> dict:
    try:
        return S.cancel_session(business_id, session_id)
    except S.SessionError as exc:
        raise _fail(exc) from None


@router.delete("/businesses/{business_id}/sessions/{session_id}/attendees/{booking_id}")
def remove_attendee(business_id: uuid.UUID, session_id: uuid.UUID, booking_id: uuid.UUID) -> dict:
    try:
        item, texted = S.remove_attendee(business_id, session_id, booking_id)
    except S.SessionError as exc:
        raise _fail(exc) from None
    return {**item, "texted": texted}
