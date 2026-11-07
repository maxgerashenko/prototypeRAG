"""Dev endpoints for the test pages (stage 1, no login): list all businesses, and assign the
main Twilio number to one of them. Reads and writes across tenants, so it must move behind
the owner login (and RLS-aware admin access) in stage 2."""

import uuid
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import any_, func, select, text, update
from sqlalchemy.orm import Session
from app.config import get_settings
from app.db.session import SessionLocal
from app.db.models import Business, BusinessProfile, Location, Message
from app.ingest.profile import is_placeholder_name

router = APIRouter()

class LocationOut(BaseModel):
    id: uuid.UUID
    name: str | None
    url: str | None

class BusinessOut(BaseModel):
    id: uuid.UUID
    name: str
    website: str | None
    domain: str | None
    conversation_count: int
    default_location: LocationOut | None
    phone_numbers: list[str]

def display_name(business_name: str, profile_name: str | None) -> str:
    """Return profile name if valid, otherwise fall back to business name."""
    if not is_placeholder_name(profile_name):
        return profile_name
    return business_name

@router.get("/businesses")
def list_businesses() -> list[BusinessOut]:
    """List all businesses for the voice test page business picker."""
    conv_count_subq = (
        select(func.count(func.distinct(Message.conversation_id)))
        .where(Message.business_id == Business.id, Message.role == "user")
        .scalar_subquery()
    )
    with SessionLocal() as session:
        stmt = (
            select(
                Business.id, Business.name, Business.website, Business.domain, Business.phone_numbers,
                BusinessProfile.name, conv_count_subq,
                Location.id, Location.name, Location.url,
            )
            .outerjoin(BusinessProfile, BusinessProfile.business_id == Business.id)
            .outerjoin(Location, (Location.business_id == Business.id) & Location.is_default.is_(True))
            .order_by(Business.created_at)
        )
        # several selected columns are called "name" -> unpack by position (draft bug:
        # it read row.BusinessProfile_name, which doesn't exist)
        results = []
        for bid, name, website, domain, phones, profile_name, conv_count, loc_id, loc_name, loc_url in session.execute(stmt).all():
            default_location = LocationOut(id=loc_id, name=loc_name, url=loc_url) if loc_id is not None else None
            results.append(BusinessOut(
                id=bid, name=display_name(name, profile_name), website=website, domain=domain,
                conversation_count=conv_count or 0, default_location=default_location, phone_numbers=phones,
            ))
        return results


def lock_phone_number(session: Session, number: str) -> None:
    """Serialize every move of one phone number (issue #237). A transaction-scoped advisory
    lock keyed on the number, not a row lock: when nobody holds the number yet there is no
    row to lock, and two concurrent assigns to different businesses would both see "free"
    and both write it, leaving one number on two businesses (find_business then silently
    picks one). Take it first in the transaction; the holder query after it runs on a fresh
    READ COMMITTED snapshot, so it sees the move the previous lock owner committed."""
    session.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:k, 0))"), {"k": f"phone-number:{number}"})


def _main_number() -> str:
    number = get_settings().twilio_phone_number
    if not number:
        raise HTTPException(status_code=503, detail="TWILIO_PHONE_NUMBER is not set")
    return number


@router.post("/businesses/{business_id}/main-number")
def assign_main_number(business_id: uuid.UUID) -> dict:
    """Point the main Twilio number at this business. A dialled number must find exactly one
    business (app/voice/twilio_routes.py), so it is taken off every other business first."""
    number = _main_number()
    with SessionLocal() as session:
        lock_phone_number(session, number)
        if session.get(Business, business_id) is None:
            raise HTTPException(status_code=404, detail="business not found")
        session.execute(
            update(Business).where(Business.id != business_id, number == any_(Business.phone_numbers))
            .values(phone_numbers=func.array_remove(Business.phone_numbers, number))
        )
        session.execute(
            update(Business).where(Business.id == business_id, ~(number == any_(Business.phone_numbers)))
            .values(phone_numbers=func.array_append(Business.phone_numbers, number))
        )
        session.commit()
    return {"business_id": business_id, "number": number}


@router.delete("/businesses/{business_id}/main-number")
def unassign_main_number(business_id: uuid.UUID) -> dict:
    """Take the main number off this business; calls to it then hear "not in service"."""
    number = _main_number()
    with SessionLocal() as session:
        lock_phone_number(session, number)
        session.execute(
            update(Business).where(Business.id == business_id)
            .values(phone_numbers=func.array_remove(Business.phone_numbers, number))
        )
        session.commit()
    return {"business_id": business_id, "number": None}
