"""Dev endpoint listing all businesses for the test pages (stage 1, no login).
Reads across tenants, so it must move behind the owner login (and RLS-aware admin access) in stage 3."""

import uuid
from fastapi import APIRouter
from pydantic import BaseModel
from sqlalchemy import select, func
from app.db.session import SessionLocal
from app.db.models import Business, BusinessProfile, Message

router = APIRouter()

class BusinessOut(BaseModel):
    id: uuid.UUID
    name: str
    website: str | None
    conversation_count: int

def display_name(business_name: str, profile_name: str | None) -> str:
    """Return profile name if valid, otherwise fall back to business name."""
    placeholders = {"business_name", "(pending)", "pending", ""}
    if profile_name and profile_name.strip().lower() not in placeholders:
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
            select(Business.id, Business.name, Business.website, BusinessProfile.name, conv_count_subq)
            .outerjoin(BusinessProfile, BusinessProfile.business_id == Business.id)
            .order_by(Business.created_at)
        )
        # both selected columns are called "name" -> unpack by position (draft bug:
        # it read row.BusinessProfile_name, which doesn't exist)
        return [
            BusinessOut(id=bid, name=display_name(name, profile_name), website=website, conversation_count=conv_count or 0)
            for bid, name, website, profile_name, conv_count in session.execute(stmt).all()
        ]
