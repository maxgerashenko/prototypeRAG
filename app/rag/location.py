"""Which location a conversation is about (DEC-48, resolves OPEN-23).

To the caller each location is its own business: answers use that location's facts and
chunks plus the business-wide ones (`location_id IS NULL`: what the business is, its
policies, that it has other locations). The location is fixed by the entry point, never
guessed per question. Until an entry point can choose one (phone number -> location is
OPEN-21, stage 3), every conversation is about the business's default location.
"""

import uuid

from sqlalchemy import select

from app.db.models import Location


def default_location_id(session, business_id: uuid.UUID) -> uuid.UUID | None:
    """The default location, or None for a business without locations (no filter then)."""
    return session.scalar(
        select(Location.id).where(Location.business_id == business_id, Location.is_default.is_(True))
    )


def format_locations(session, business_id: uuid.UUID, location_id: uuid.UUID | None) -> str:
    """Prompt block naming this conversation's location and the business's other ones,
    so the assistant can say the others exist and where they are without ever answering
    for them. "" when the business has fewer than two named locations."""
    rows = session.scalars(
        select(Location).where(Location.business_id == business_id, Location.name.isnot(None)).order_by(Location.name)
    ).all()
    if len(rows) < 2:
        return ""

    def line(loc: Location) -> str:
        return f"{loc.name} ({loc.address})" if loc.address else loc.name

    current = next((loc for loc in rows if loc.id == location_id), None)
    others = [loc for loc in rows if loc is not current]
    lines = []
    if current is not None:
        lines.append(f"This conversation is about the location: {line(current)}.")
    lines.append("Other locations of this business: " + "; ".join(line(loc) for loc in others) + ".")
    lines.append(
        "Answer only for this location. If asked about another location, say it exists and give its "
        "address if known; never use this location's hours, prices or services for it, and tell the "
        "caller to ask that location directly."
        if current is not None
        else "Answer for the business as a whole; if a question depends on the location, say which "
        "locations exist and ask which one is meant."
    )
    return "\n".join(lines)
