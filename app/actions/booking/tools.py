"""Booking tools for the LLM (plan/04-actions.md → Tools): check_availability,
create_booking, cancel_booking. Importing this module registers them.

One `create_booking` covers tables and appointments (a `service` makes it an appointment),
so a small model has fewer tools to choose between. Rules run in code (`rules.py`): the
caller's phone comes from caller ID (`conversations.caller`) when there is one; only
without it (text chat) is the number the model passes used, and it must be a full
international number. There is no SMS confirmation: the model reads the reference back to the
caller (SMS was dropped from #52, see plan/04-actions.md).

Not wired into the live voice loop yet: `app/voice/tools.py` doesn't import this module,
and the per-business enabled-tools setting is part of that wiring (plan/04 tasks).
"""

import re

from app.actions.booking.internal import BookingBackend, BookingRequest, InternalBackend
from app.actions.registry import ActionContext, Tool, register
from app.config import get_settings
from app.db import tenant_session
from app.db.models import Conversation

E164 = re.compile(r"^\+[1-9]\d{7,14}$")


def normalize_phone(raw: str | None) -> str | None:
    """'+1 (555) 010-0100' -> '+15550100100'; None if it isn't a full international number."""
    if not raw:
        return None
    digits = re.sub(r"[\s\-().]", "", raw.strip())
    return digits if E164.match(digits) else None


def make_backend() -> BookingBackend:
    """`BOOKING_BACKEND` picks the backend (DEC-48); the default is the Postgres-only one."""
    settings = get_settings()
    if settings.booking_backend == "google_calendar":
        from app.actions.booking.google_calendar import GoogleCalendarBackend
        from app.actions.booking.google_client import GoogleCalendarClient

        return GoogleCalendarBackend(
            GoogleCalendarClient(settings.google_calendar_credentials_file), settings.google_calendar_id
        )
    return InternalBackend()


backend: BookingBackend = make_backend()

_DATE = {"type": "string", "description": "Date as YYYY-MM-DD, in the business's local time."}
_TIME = {"type": "string", "description": "Start time as HH:MM, 24-hour, business local time."}
_PARTY = {"type": "integer", "description": "Number of people (tables and groups)."}
_SERVICE = {"type": "string", "description": "Service or treatment name, for appointments."}


def _phone(ctx: ActionContext, args: dict) -> str | None:
    """Caller ID first (never the model's guess); the model's number only without one."""
    if ctx.conversation_id is not None:
        with tenant_session(ctx.business_id) as session:
            conversation = session.get(Conversation, ctx.conversation_id)
            if conversation is not None and normalize_phone(conversation.caller):
                return normalize_phone(conversation.caller)
    return normalize_phone(args.get("phone")) if isinstance(args.get("phone"), str) else None


def _request(ctx: ActionContext, args: dict, phone: str | None = None) -> BookingRequest | str:
    for key in ("date", "time"):
        if not isinstance(args.get(key), str) or not args[key].strip():
            return f"Error: {key} is required."
    party = args.get("party_size")
    if party is not None and (isinstance(party, bool) or not isinstance(party, int)):
        return "Error: party_size must be a whole number."
    return BookingRequest(
        day=args["date"], at=args["time"], party_size=party,
        service=args.get("service") if isinstance(args.get("service"), str) else None,
        name=str(args.get("name") or ""), phone=phone,
        notes=args.get("notes") if isinstance(args.get("notes"), str) else None,
        conversation_id=ctx.conversation_id,
    )


def _alternatives(alts: list[str]) -> str:
    return f" Free times that day: {', '.join(alts)}." if alts else ""


def check_availability(ctx: ActionContext, args: dict) -> str:
    request = _request(ctx, args)
    if isinstance(request, str):
        return request
    result = backend.availability(ctx.business_id, request)
    if result.ok:
        return "Available."
    return f"Not available: {result.reason}{_alternatives(result.alternatives)}"


def create_booking(ctx: ActionContext, args: dict) -> str:
    phone = _phone(ctx, args)
    if phone is None:
        return "Error: the caller's phone number is needed (full international format, e.g. +15550100)."
    request = _request(ctx, args, phone)
    if isinstance(request, str):
        return request
    result = backend.create(ctx.business_id, request)
    if not result.ok:
        return f"Error: not booked. {result.message}{_alternatives(result.alternatives)}"
    what = request.service or (f"table for {request.party_size}" if request.party_size else "booking")
    return (
        f"Booked {what} on {result.when}. Reference {result.reference}. "
        "Read the reference to the caller; no confirmation text message is sent."
    )


def cancel_booking(ctx: ActionContext, args: dict) -> str:
    phone = _phone(ctx, args)
    ref = args.get("reference")
    if phone is None or not isinstance(ref, str):
        return "Error: the booking reference and the caller's phone number are needed."
    return backend.cancel(ctx.business_id, ref, phone)


register(Tool(
    name="check_availability",
    description="Check whether a table or appointment is free at a date and time, before offering it to the caller.",
    parameters={"date": _DATE, "time": _TIME, "party_size": _PARTY, "service": _SERVICE},
    required=("date", "time"),
    handler=check_availability,
))
register(Tool(
    name="create_booking",
    description=(
        "Book a table or an appointment. Check availability first, read all details back "
        "(date, time, people, service, name) and get a yes."
    ),
    parameters={
        "date": _DATE, "time": _TIME, "party_size": _PARTY, "service": _SERVICE,
        "name": {"type": "string", "description": "Name the booking is under."},
        "phone": {"type": "string", "description": "Only if the caller's number is not known: full international number."},
        "notes": {"type": "string", "description": "Anything else the caller asked for."},
    },
    required=("date", "time", "name"),
    side_effect=True,
    handler=create_booking,
))
register(Tool(
    name="cancel_booking",
    description="Cancel a booking the caller made, by its reference (given when it was booked).",
    parameters={
        "reference": {"type": "string", "description": "Booking reference, e.g. 3F9A1C2B."},
        "phone": {"type": "string", "description": "Only if the caller's number is not known."},
    },
    required=("reference",),
    side_effect=True,
    handler=cancel_booking,
))
