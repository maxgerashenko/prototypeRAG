"""The Twilio account's phone numbers for the Settings number picker (DEC-52).

GET /twilio/numbers lists the numbers the account owns, each with the business that holds
it. Only these can be assigned (PUT /businesses/{id}/number); buying one stays out (DEC-34).
"""

import logging
import uuid

from fastapi import APIRouter
from sqlalchemy import select

from app.api.businesses import display_name
from app.config import get_settings
from app.db.models import Business, BusinessProfile
from app.db.session import SessionLocal

log = logging.getLogger(__name__)
router = APIRouter()


def account_numbers() -> list[dict]:
    """Numbers on the Twilio account; `routed` = its voice URL ends with /twilio/voice."""
    settings = get_settings()
    if not settings.twilio_account_sid or not settings.twilio_auth_token:
        raise RuntimeError("TWILIO_ACCOUNT_SID / TWILIO_AUTH_TOKEN are not set")
    from twilio.rest import Client

    client = Client(settings.twilio_account_sid, settings.twilio_auth_token)
    result = []
    for n in client.incoming_phone_numbers.list(limit=100):
        caps = n.capabilities or {}
        voice_path = (n.voice_url or "").split("?", 1)[0].rstrip("/")
        result.append({
            "number": n.phone_number,
            "friendly_name": n.friendly_name,
            "voice": bool(caps.get("voice")),
            "sms": bool(caps.get("sms")),
            "routed": voice_path.endswith("/twilio/voice"),
        })
    return result


def holders(numbers: list[str]) -> dict[str, tuple[uuid.UUID, str]]:
    """number -> (business id, display name) for the numbers a business holds."""
    wanted = set(numbers)
    found: dict[str, tuple[uuid.UUID, str]] = {}
    with SessionLocal() as session:
        rows = session.execute(
            select(Business.id, Business.name, Business.phone_numbers, BusinessProfile.name)
            .outerjoin(BusinessProfile, BusinessProfile.business_id == Business.id)
            .order_by(Business.created_at)
        ).all()
        for bid, name, phones, profile_name in rows:
            for phone in phones or []:
                if phone in wanted and phone not in found:
                    found[phone] = (bid, display_name(name, profile_name))
    return found


def safe_error(exc: Exception) -> str:
    """Text for the browser: Twilio's short message or HTTP status only -- `str(TwilioRestException)`
    carries the request URI (with the Account SID) and terminal colour codes."""
    msg = getattr(exc, "msg", None)
    status = getattr(exc, "status", None)
    if isinstance(msg, str) and msg:
        return msg
    if status:
        return f"HTTP {status}"
    return str(exc) or type(exc).__name__  # not a Twilio API error: our own text, e.g. credentials not set


@router.get("/twilio/numbers")
def list_numbers() -> dict:
    """The account's numbers with the business holding each. Reads across tenants like
    app/api/businesses.py (needs an RLS-exempt path in stage 3). Twilio errors come back
    as `error` so the picker can show them."""
    try:
        numbers = account_numbers()
        taken = holders([n["number"] for n in numbers])
    except Exception as exc:
        log.warning("could not list Twilio numbers: %s", exc)
        return {"numbers": [], "error": safe_error(exc)}
    for n in numbers:
        holder = taken.get(n["number"])
        n["business_id"] = holder[0] if holder else None
        n["business_name"] = holder[1] if holder else None
    numbers.sort(key=lambda n: n["number"])
    return {"numbers": numbers, "error": None}
