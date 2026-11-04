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
from difflib import SequenceMatcher

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
        name=str(args.get("first_name") or args.get("name") or ""),
        last_name=str(args.get("last_name") or ""), phone=phone,
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


_CODE = re.compile(r"[0-9a-fA-F-]{6,}")
_CLOCK = re.compile(r"(\d{1,2})(?::(\d{2}))?\s*([ap])?\.?m?\b", re.IGNORECASE)


def _starts_at(when: str) -> str:
    """'Mon 02 Nov, 10:00-11:00' -> '10:00'."""
    return when.split(",")[-1].strip().split("-")[0]


def _mentions_time(text: str, start: str) -> bool:
    """Does `text` name the 'HH:MM' start, as '10:00', '10 am', '3 pm' or '15:00'?"""
    hour, minute = (int(x) for x in start.split(":"))
    for h, m, ap in _CLOCK.findall(text):
        h, m = int(h), int(m or 0)
        if m != minute or h > 24:
            continue
        if ap:
            h = h % 12 + (12 if ap.lower() == "p" else 0)
            if h == hour:
                return True
        elif h == hour or (h <= 12 and h % 12 + 12 == hour):
            return True
    return False


def _best_guess(rows: list[dict], text: str) -> dict | None:
    """Speech-to-text garbles names ("sound of Polish session" for "Sauna Polish"). The open
    session whose name words are, on average, closest to the caller's words (>= 0.7), the
    soonest one if several share that name and a mentioned start time narrows it; None if
    nothing is close."""
    words = re.findall(r"[a-z0-9]+", text.lower())
    if not words:
        return None

    def score(name: str) -> float:
        tokens = re.findall(r"[a-z0-9]+", name.lower())
        return sum(max(SequenceMatcher(None, t, w).ratio() for w in words) for t in tokens) / len(tokens) if tokens else 0.0

    scored = [(score(r["name"]), r) for r in rows]
    top = max((s for s, _ in scored), default=0.0)
    if top < 0.7:
        return None
    best = [r for s, r in scored if s >= top - 1e-9]
    if len({r["name"] for r in best}) > 1:
        return None
    return next((r for r in best if _mentions_time(text, _starts_at(r["when"]))), best[0])


def resolve_session(business_id, text: str) -> tuple[str | None, str]:
    """The model sometimes passes the session's name and time instead of its code (the codes are
    not in the call's history from one turn to the next). Look the session up in the open list:
    -> (code, "") when exactly one matches, else (None, message listing the candidates)."""
    rows = sessions_.list_open(business_id)
    words = set(re.findall(r"[a-z0-9]+", text.lower()))
    named = [r for r in rows if set(re.findall(r"[a-z0-9]+", r["name"].lower())) <= words]
    timed = [r for r in named if _mentions_time(text, _starts_at(r["when"]))]
    found = timed or named if len(named) > 1 else named
    if len(found) == 1:
        return found[0]["ref"], ""
    guess = _best_guess(rows, text)
    if guess:
        return None, (
            "Error: not booked. The caller's words are close to one session but not exact. Do not list the "
            f"others. Ask one yes/no question: \"{guess['name']} on {guess['when']}, shall I book it?\" "
            f"If they say yes, call join_session again with session={guess['ref']} and confirmed=true."
        )
    options = found or rows
    listing = "; ".join(f"{r['ref']} · {r['name']} · {r['when']}" for r in options[:5]) or "none are open"
    return None, (
        "Error: not booked. That did not match exactly one session. "
        + ("Ask the caller which one: " if found else "Open sessions: ") + listing + "."
    )


def join_session(ctx: ActionContext, args: dict) -> str:
    ref = args.get("session")
    if not isinstance(ref, str) or not ref.strip():
        return "Error: the session code from list_sessions is needed."
    if not _CODE.fullmatch(ref.strip()):
        ref, problem = resolve_session(ctx.business_id, ref)
        if ref is None:
            return problem
    text = lambda key: args[key].strip() if isinstance(args.get(key), str) else ""  # noqa: E731
    first, last = text("first_name") or text("name"), text("last_name")
    if not first:
        return "Error: a name for the booking is needed."
    try:
        r = sessions_.join_session(
            ctx.business_id, ref, ctx.conversation_id, first, _phone(ctx, args), last_name=last
        )
    except sessions_.SessionError as exc:
        return f"Error: not booked. {exc.message}."
    return (
        f"Booked {r['guest']} into {r['name']} on {r['when']} ({r['booked']} of {r['max_attendees']} seats taken). "
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
        "Read the session, time and the name (spelled out) back and get a yes first."
    ),
    parameters={
        "session": {"type": "string", "description": "Session code from list_sessions, e.g. 3F9A1C2B."},
        "first_name": {"type": "string", "description": "Caller's first name."},
        "last_name": {"type": "string", "description": "Caller's last name. Ask for it if they gave only a first name."},
        "phone": {"type": "string", "description": "Only if the caller's number is not known: full international number."},
    },
    required=("session", "first_name"),
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
        "(date, time, people, service, name spelled out) and get a yes."
    ),
    parameters={
        "date": _DATE, "time": _TIME, "party_size": _PARTY, "service": _SERVICE,
        "first_name": {"type": "string", "description": "Caller's first name."},
        "last_name": {"type": "string", "description": "Caller's last name. Ask for it if they gave only a first name."},
        "phone": {"type": "string", "description": "Only if the caller's number is not known: full international number."},
        "notes": {"type": "string", "description": "Anything else the caller asked for."},
    },
    required=("date", "time", "first_name"),
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
