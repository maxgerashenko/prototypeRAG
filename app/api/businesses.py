"""Dev endpoint listing all businesses for the test pages (stage 1, no login).
Reads across tenants, so it must move behind the owner login (and RLS-aware admin access) in stage 3."""

import uuid
from fastapi import APIRouter
from pydantic import BaseModel
from sqlalchemy import select, func
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
                Business.id, Business.name, Business.website, Business.domain,
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
        for bid, name, website, domain, profile_name, conv_count, loc_id, loc_name, loc_url in session.execute(stmt).all():
            default_location = LocationOut(id=loc_id, name=loc_name, url=loc_url) if loc_id is not None else None
            results.append(BusinessOut(
                id=bid, name=display_name(name, profile_name), website=website, domain=domain,
                conversation_count=conv_count or 0, default_location=default_location,
            ))
        return results
