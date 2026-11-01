"""Booking tools for the LLM (plan/04-actions.md → Tools): check_availability,
create_booking, cancel_booking. Importing this module registers them.

One `create_booking` covers tables and appointments (a `service` makes it an appointment),
so a small model has fewer tools to choose between. Rules run in code (`rules.py`): the
caller's phone comes from caller ID (`conversations.caller`) when there is one; only
without it (text chat) is the number the model passes used, and it must be a full
international number. There is no SMS confirmation: the model reads the reference back to the
caller (SMS was dropped from #52, see plan/04-actions.md).

All three are `optional`: offered (chat and voice) and executable only for businesses
whose `settings["enabled_tools"]` lists them (#151). `app/voice/tools.py` imports this module.
"""

import re

from app.actions.booking import sessions as sessions_
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
        from app.actions.booking.google_client import client_from_settings

        return GoogleCalendarBackend(client_from_settings(), settings.google_calendar_id)
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


def list_sessions(ctx: ActionContext, args: dict) -> str:
    day = args.get("date")
    try:
        rows = sessions_.list_open(ctx.business_id, day.strip() if isinstance(day, str) and day.strip() else None)
    except sessions_.SessionError as exc:
        return f"Error: {exc.message}."
    if not rows:
        return "No group sessions are open for booking" + (f" on {day}." if day else " in the next 30 days.")
    lines = [
        f"{r['ref']} · {r['name']} · {r['when']} · "
        + ("FULL" if r["full"] else f"{r['max_attendees'] - r['booked']} of {r['max_attendees']} seats left")
        for r in rows
    ]
    return "Group sessions (read the name and time to the caller, not the code):\n" + "\n".join(lines)


def join_session(ctx: ActionContext, args: dict) -> str:
    ref = args.get("session")
    if not isinstance(ref, str) or not ref.strip():
        return "Error: the session code from list_sessions is needed."
    name = args.get("name") if isinstance(args.get("name"), str) else ""
    if not name.strip():
        return "Error: a name for the booking is needed."
    try:
        r = sessions_.join_session(ctx.business_id, ref, ctx.conversation_id, name, _phone(ctx, args))
    except sessions_.SessionError as exc:
        return f"Error: not booked. {exc.message}."
    return (
        f"Booked {name.strip()} into {r['name']} on {r['when']} ({r['booked']} of {r['max_attendees']} seats taken). "
        f"Reference {r['booking_ref']}. Read the reference to the caller; no confirmation text message is sent."
    )


register(Tool(
    name="list_sessions",
    description="List open group sessions (classes, shared sessions) with their time and seats left, before offering one to the caller.",
    parameters={"date": _DATE},
    required=(),
    handler=list_sessions,
    optional=True,
))
register(Tool(
    name="join_session",
    description=(
        "Book the caller into a group session from list_sessions until it is full. "
        "Read the session, time and name back and get a yes first."
    ),
    parameters={
        "session": {"type": "string", "description": "Session code from list_sessions, e.g. 3F9A1C2B."},
        "name": {"type": "string", "description": "Name the seat is under."},
        "phone": {"type": "string", "description": "Only if the caller's number is not known: full international number."},
    },
    required=("session", "name"),
    side_effect=True,
    handler=join_session,
    optional=True,
))
register(Tool(
    name="check_availability",
    description="Check whether a table or appointment is free at a date and time, before offering it to the caller.",
    parameters={"date": _DATE, "time": _TIME, "party_size": _PARTY, "service": _SERVICE},
    required=("date", "time"),
    handler=check_availability,
    optional=True,
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
    optional=True,
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
    optional=True,
))
