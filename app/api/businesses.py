"""Dev endpoints for the test pages (stage 1, no login): list all businesses, and assign the
main Twilio number to one of them. Reads and writes across tenants, so it must move behind
the owner login (and RLS-aware admin access) in stage 3."""

import uuid
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import any_, func, select, update
from app.config import get_settings
from app.db.session import SessionLocal
from app.db.models import Business, BusinessProfile, Location, Message
from app.ingest.profile import is_placeholder_name
from app.voice.twilio_numbers import TwilioNotConfigured, list_account_numbers

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
        session.execute(
            update(Business).where(Business.id == business_id)
            .values(phone_numbers=func.array_remove(Business.phone_numbers, number))
        )
        session.commit()
    return {"business_id": business_id, "number": None}


# --- assign any of the account's numbers (issue #147) -----------------------------------

class AssignNumber(BaseModel):
    number: str
    # the number is held by another business: refused (409) unless the owner confirmed
    reassign: bool = False


@router.put("/businesses/{business_id}/numbers")
def assign_number(business_id: uuid.UUID, body: AssignNumber) -> dict:
    """Give this business one of the account's Twilio numbers. Stage 1 = one number per
    business (OPEN-21), so it replaces the business's numbers. A dialled number must find exactly
    one business, so a number held by another business is only moved with `reassign`."""
    number = body.number.strip()
    try:
        owned = {n.number for n in list_account_numbers()}
    except TwilioNotConfigured as e:
        raise HTTPException(status_code=503, detail=str(e))
    except Exception:
        raise HTTPException(status_code=502, detail="Could not list the Twilio numbers")
    if number not in owned:
        raise HTTPException(status_code=422, detail="not a number on this Twilio account")
    with SessionLocal() as session:
        if session.get(Business, business_id) is None:
            raise HTTPException(status_code=404, detail="business not found")
        holder = session.execute(
            select(Business.id, Business.name).where(Business.id != business_id, number == any_(Business.phone_numbers))
        ).first()
        if holder is not None and not body.reassign:
            raise HTTPException(status_code=409, detail=f"{number} is used by {holder.name}")
        session.execute(
            update(Business).where(Business.id != business_id, number == any_(Business.phone_numbers))
            .values(phone_numbers=func.array_remove(Business.phone_numbers, number))
        )
        session.execute(update(Business).where(Business.id == business_id).values(phone_numbers=[number]))
        session.commit()
    return {"business_id": business_id, "number": number, "taken_from": holder.name if holder else None}


@router.delete("/businesses/{business_id}/numbers")
def unassign_numbers(business_id: uuid.UUID) -> dict:
    """Take all numbers off this business; calls to them then hear "not in service"."""
    with SessionLocal() as session:
        if session.get(Business, business_id) is None:
            raise HTTPException(status_code=404, detail="business not found")
        session.execute(update(Business).where(Business.id == business_id).values(phone_numbers=[]))
        session.commit()
    return {"business_id": business_id, "number": None}
