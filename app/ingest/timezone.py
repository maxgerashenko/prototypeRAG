"""Timezone per location from its address (V22, plan/07-knowledge-quality.md §3).

The LLM proposes an IANA name, code validates it with `zoneinfo` -- an invalid or
non-geographic answer is dropped, never stored (the voice prompt does
`ZoneInfo(business.timezone)`, so a bad value would break every call). A location's
timezone is only filled while it's empty, so an owner's correction is never overwritten.
`businesses.timezone` follows the default location (DEC-37).
"""

import uuid
from zoneinfo import available_timezones

from sqlalchemy import select

from app.db import tenant_session
from app.db.models import Business, BusinessProfile, Location
from app.llm import chat_json

TIMEZONE_SCHEMA = {
    "type": "object",
    "properties": {"timezone": {"type": ["string", "null"]}},
    "required": ["timezone"],
    "additionalProperties": False,
}

_SYSTEM_PROMPT = (
    "Return the IANA time zone name (e.g. America/New_York, Europe/Riga) for the given "
    "street address. Use null if the address doesn't say where it is precisely enough."
)


def valid_timezone(name: str | None) -> str | None:
    """`name` if it's a geographic IANA zone ("Region/City"), else None. "UTC", "EST" and
    "Etc/GMT+5" are real zones but never the answer for a street address."""
    if not isinstance(name, str):
        return None
    name = name.strip()
    if "/" not in name or name.startswith("Etc/") or name not in available_timezones():
        return None
    return name


def timezone_from_address(address: str) -> str | None:
    messages = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": address},
    ]
    return valid_timezone(chat_json(messages, TIMEZONE_SCHEMA, "timezone").get("timezone"))


def assign_timezones(business_id: uuid.UUID) -> int:
    """Fill `locations.timezone` where it's empty and an address is known, then copy the
    default location's timezone to `businesses.timezone`. The default location falls back
    to the profile address (a single-location site with no footer address list gets a
    location without one). Returns how many locations got a timezone."""
    with tenant_session(business_id) as session:
        profile = session.get(BusinessProfile, business_id)
        profile_address = profile.address if profile else None
        todo = {
            loc.id: loc.address or (profile_address if loc.is_default else None)
            for loc in session.scalars(
                select(Location).where(Location.business_id == business_id, Location.timezone.is_(None))
            )
        }

    # LLM calls outside the DB session -- don't hold a connection while the model thinks
    found: dict[uuid.UUID, str] = {}
    for location_id, address in todo.items():
        if address:
            tz = timezone_from_address(address)
            if tz:
                found[location_id] = tz

    with tenant_session(business_id) as session:
        for location_id, tz in found.items():
            loc = session.get(Location, location_id)
            if loc is not None and loc.timezone is None:
                loc.timezone = tz
        default_tz = session.scalar(
            select(Location.timezone).where(Location.business_id == business_id, Location.is_default)
        )
        business = session.get(Business, business_id)
        if business is not None and valid_timezone(default_tz) and business.timezone != default_tz:
            business.timezone = default_tz
    return len(found)
