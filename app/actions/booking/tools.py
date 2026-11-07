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
from datetime import date, datetime
from difflib import SequenceMatcher

from sqlalchemy import select

from app.actions.booking import sessions as sessions_
from app.actions.booking.internal import BookingBackend, BookingRequest, InternalBackend
from app.actions.registry import ActionContext, Tool, enabled_tool_names, register
from app.config import get_settings
from app.db import tenant_session
from app.db.models import BookingRule, Conversation, Message, heard_text, message_order

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


# names a model invents when the caller has not given one (seen in live calls: "John Doe", "Caller")
_PLACEHOLDER_NAMES = {
    "john doe", "jane doe", "caller", "caller unknown", "unknown", "unknown caller", "guest", "customer",
    "client", "name", "user", "test", "anonymous", "n/a", "none",
}
NAME_NEEDED = (
    "Error: not booked. The caller has not given their name: ask for their first and last name in one short "
    "question, never make one up."
)


LAST_NAME_NEEDED = (
    "Error: not booked. The caller gave only a first name: ask for their last name, then call join_session "
    "again with first_name and last_name."
)


READBACK_NEEDED = (
    "Error: not booked. The caller has not been asked yet. Read the session, its day and time and the name back "
    "and ask \"Shall I book it?\"; call join_session with confirmed=true only after their clear yes to that."
)


_NAME_QUESTION = re.compile(
    r"(first and last name|last name|surname|full name|what(?:'s| is) your name|(?:give|tell|confirm|provide|have) (?:me )?your name(?! as\b))", re.I
)


_DETAIL_QUESTION = re.compile(_NAME_QUESTION.pattern[:-1] + r"|phone number|your number|contact number)", re.I)


def _last_bot_text(ctx: ActionContext) -> str:
    """What the caller last heard from the bot in this conversation ("" without one)."""
    if ctx.conversation_id is None:
        return ""
    with tenant_session(ctx.business_id) as session:
        last = session.scalars(
            select(Message.content).where(
                Message.business_id == ctx.business_id, Message.conversation_id == ctx.conversation_id,
                Message.role == "assistant",
            ).order_by(*message_order())
        ).all()
    return heard_text(last[-1]).strip() if last else ""


def _read_back_asked(ctx: ActionContext, first: str, asked: re.Pattern = _NAME_QUESTION) -> bool:
    """The booking is real only after a read-back: if the bot's last message was asking for the name,
    "My name is Tom Baker" is the answer to that, not a yes to booking. No conversation (unit tests, API calls): not checked."""
    if ctx.conversation_id is None:
        return True
    with tenant_session(ctx.business_id) as session:
        last = session.scalars(
            select(Message.content).where(
                Message.business_id == ctx.business_id, Message.conversation_id == ctx.conversation_id,
                Message.role == "assistant",
            ).order_by(*message_order())
        ).all()
    if not last:
        return False
    sentences = [x for x in re.split(r"(?<=[.!?])\s+", heard_text(last[-1]).strip()) if x]
    return not (sentences and asked.search(sentences[-1]))  # a read-back may mention the name; only a closing request counts


def _is_placeholder(first: str, last: str = "") -> bool:
    full = " ".join(f"{first} {last}".lower().split())
    if any(c in full for c in "[]{}<>_") or "first name" in full or "last name" in full:
        return True  # an unfilled template: "[Caller's First Name]"
    return full in _PLACEHOLDER_NAMES or first.lower().strip() in _PLACEHOLDER_NAMES


_TABLE_WORDS = {"table", "a table", "table booking", "table reservation", "reservation", "dinner", "restaurant table"}


def _service(args: dict) -> str | None:
    """A service makes the booking an appointment; models sometimes pass "table" as the service."""
    service = args.get("service")
    if not isinstance(service, str) or service.strip().lower() in _TABLE_WORDS:
        return None
    return service


def _quotable(text: str, limit: int = 60) -> str:
    """Text safe inside `Say exactly: "..."`: letters, digits, spaces and basic punctuation, no quote marks
    (a `"` would end the sentence early for `guard_booking_claim`'s regex and for the model)."""
    return " ".join(re.sub(r"[^\w\s.,'\u2019:;!?()&/+-]", " ", text or "").split())[:limit].strip()


def _unknown_service(business_id, service: str | None) -> str | None:
    """A refusal when `service` is not something this business offers, else None. Offered = a key of the
    business's `service_durations` (either name may contain the other: "massage" / "Swedish massage").
    A business with no services configured accepts any name, unless it has open sessions: then it books
    sessions, and "Steam Bath" is not a service of it (the bot once saved an appointment for it).
    Open sessions count only for a business with `join_session` on (else the demo rule's list, which holds
    every business's sessions, would be named to it). The reply sentence is built here, like `_no_such_session`."""
    wanted = (service or "").strip().lower()
    if not wanted:
        return None
    with tenant_session(business_id) as session:
        row = session.get(BookingRule, business_id)
    offered = sorted(k.strip() for k in (row.service_durations or {}) if k.strip()) if row else []
    if any(k.lower() in wanted or wanted in k.lower() for k in offered):
        return None
    rows = sessions_.list_open(business_id, seats=False) if "join_session" in enabled_tool_names(business_id) else []
    if not offered and not rows:
        return None  # nothing to check against: a free-text appointment
    reply = f"We don't offer {_quotable(service)}."
    if offered:
        reply += f" We offer {', '.join(_quotable(k) for k in offered[:5])}."
    if rows:
        reply += f" We have {spoken_sessions(business_id, rows)}."
    return f'Error: not booked. Say exactly: "{reply}" Never mention codes or a technical problem.'


def _request(ctx: ActionContext, args: dict, phone: str | None = None, check_service: bool = True) -> BookingRequest | str:
    for key in ("date", "time"):
        if not isinstance(args.get(key), str) or not args[key].strip():
            return f"Error: {key} is required."
    party = args.get("party_size")
    if party is not None and (isinstance(party, bool) or not isinstance(party, int)):
        return "Error: party_size must be a whole number."
    if check_service and (refusal := _unknown_service(ctx.business_id, _service(args))):
        return refusal
    return BookingRequest(
        day=args["date"], at=args["time"], party_size=party,
        service=_service(args),
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
    # the service was checked by the precheck, which the executor runs first: not a second time here
    request = _request(ctx, args, phone, check_service=False)
    if isinstance(request, str):
        return request
    if not request.name.strip() or _is_placeholder(request.name, request.last_name):
        return NAME_NEEDED
    if not _read_back_asked(ctx, request.name, _DETAIL_QUESTION):
        return READBACK_NEEDED
    result = backend.create(ctx.business_id, request)
    if not result.ok:
        return f"Error: not booked. {result.message}{_alternatives(result.alternatives)}"
    what = request.service or (f"table for {request.party_size}" if request.party_size else "booking")
    return (
        f"Booked {what} on {result.when}. Reference {result.reference}. "
        "Read the reference to the caller; no confirmation text message is sent."
    )


def create_booking_precheck(ctx: ActionContext, args: dict) -> str | None:
    """Refuse before the read-back what can never succeed: no phone, a missing field, a made-up name.
    No availability or backend call here."""
    phone = _phone(ctx, args)
    if phone is None:
        return "Error: the caller's phone number is needed (full international format, e.g. +15550100)."
    request = _request(ctx, args, phone)
    if isinstance(request, str):
        return request
    if not request.name.strip() or _is_placeholder(request.name, request.last_name):
        return NAME_NEEDED
    return None


def cancel_booking(ctx: ActionContext, args: dict) -> str:
    phone = _phone(ctx, args)
    ref = args.get("reference")
    if phone is None or not isinstance(ref, str):
        return "Error: the booking reference and the caller's phone number are needed."
    return backend.cancel(ctx.business_id, ref, phone)


def seats_text(r: dict) -> str:
    """Seats of a `list_open` row in words the model cannot misread: FULL only when no seat is left (P22: "8 of 8
    seats left" was read as 'fully booked')."""
    left = r["max_attendees"] - r["booked"]
    if r["full"]:
        return "FULL"
    if r["booked"] == 0:
        return "the 1 seat is free" if r["max_attendees"] == 1 else f"all {r['max_attendees']} seats free"
    return f"{left} seat left" if left == 1 else f"{left} seats left"


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
        + seats_text(r)
        for r in rows
    ]
    return "Group sessions (read the name and time to the caller, not the code):\n" + "\n".join(lines)


_CODE = re.compile(r"[0-9a-fA-F-]{6,}")
_CLOCK = re.compile(r"(\d{1,2})(?::(\d{2}))?\s*([ap])?\.?m?\b", re.IGNORECASE)


_WHEN_START = re.compile(r"(\d{1,2}):(\d{2})\s*([ap]m)?", re.IGNORECASE)


def _starts_at(when: str) -> str:
    """'Mon 02 Nov, 10:00 AM to 11:00 AM' (or '3:00 PM', or 24-hour '15:00-16:00') -> 'HH:MM' on a 24-hour clock."""
    found = _WHEN_START.search(when.split(",")[-1])
    if not found:
        return ""
    hour, minute, ap = int(found[1]), found[2], (found[3] or "").lower()
    if ap:
        hour = hour % 12 + (12 if ap == "pm" else 0)
    return f"{hour:02d}:{minute}"


def _mentions_time(text: str, start: str) -> bool:
    """Does `text` name the 'HH:MM' start, as '10:00', '10 am', '3 pm' or '15:00'?"""
    if not start:
        return False
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


_WHEN_DAY = re.compile(r"^\w{3} (\d{1,2}) (\w{3}),\s*(\d{1,2}:\d{2}\s*[AP]M)", re.IGNORECASE)


def _business_today(business_id) -> date | None:
    """Today in the business's time zone (None if it cannot be read: the day is then said as a date)."""
    try:
        from zoneinfo import ZoneInfo

        from app.db.models import Business

        with tenant_session(business_id) as session:
            zone = session.scalar(select(Business.timezone).where(Business.id == business_id))
        return datetime.now(ZoneInfo(zone or "UTC")).date() if zone else None
    except Exception:
        return None


def _spoken_start(when: str, today: date | None) -> str:
    """'Fri 06 Nov, 10:00 AM to 11:00 AM' -> 'tomorrow at 10:00 AM' / 'on Friday 6 November at 10:00 AM'
    (start time only, always with AM/PM). Text that does not look like that is returned as it is."""
    m = _WHEN_DAY.match(when)
    if not m:
        return when
    try:
        base = today or date.today()
        day = datetime.strptime(f"{m[1]} {m[2]} {base.year}", "%d %b %Y").date()
        if (base - day).days > 180:
            day = day.replace(year=day.year + 1)
    except ValueError:
        return when
    start = re.sub(r"\s+", " ", m[3].upper())
    if today and day == today:
        return f"today at {start}"
    if today and (day - today).days == 1:
        return f"tomorrow at {start}"
    return f"on {day:%A} {day.day} {day:%B} at {start}"


def spoken_sessions(business_id, rows: list[dict], limit: int = 5) -> str:
    """'Sauna Polish tomorrow at 10:00 AM, Sauna German tomorrow at 3:00 PM': the first `limit` open sessions
    as they are said aloud (start times only, AM/PM). Reads the business's time zone (one query)."""
    today = _business_today(business_id)
    return ", ".join(f"{_quotable(r['name'], 80)} {_spoken_start(r['when'], today)}" for r in rows[:limit])


def _no_such_session(business_id, said: str | None, rows: list[dict]) -> str:
    """`said` is what the caller asked for, or None when the model passed a code (never shown to the caller);
    `rows` the open sessions (list_open). The reply sentence is built here, so the model need not paraphrase it
    (it once said "one of these instead" without naming any, or dropped the AM/PM of a time)."""
    asked = f"session called '{_quotable(said)}'" if said and _quotable(said) else "session with that code"
    if rows:
        have = spoken_sessions(business_id, rows)
        reply = f"There is no {asked} on the schedule. We have {have}."
    else:
        reply = f"There is no {asked} on the schedule. There are no open sessions right now."
    return f'Error: not booked. Say exactly: "{reply}" Never mention codes or a technical problem.'


def resolve_session(business_id, text: str, hint: str = "") -> tuple[str | None, str]:
    """The model sometimes passes the session's name and time instead of its code (the codes are
    not in the call's history from one turn to the next). Look the session up in the open list:
    -> (code, "") when exactly one matches, else (None, message listing the candidates).
    `hint` is what the bot last said (its read-back names the session and the time the caller agreed
    to): it narrows the match, so a bare "Sauna Polish" cannot slip to a differently named later session."""
    # not the default 10: a real later session must still resolve; no seat counts, a name look-up needs none
    rows = sessions_.list_open(business_id, limit=200, seats=False)
    words = set(re.findall(r"[a-z0-9]+", f"{text} {hint}".lower()))
    named = [r for r in rows if set(re.findall(r"[a-z0-9]+", r["name"].lower())) <= words]
    timed = [r for r in named if _mentions_time(f"{text} {hint}", _starts_at(r["when"]))]
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
    if not found:
        return None, _no_such_session(business_id, text, rows)
    listing = "; ".join(f"{r['ref']} · {r['name']} · {r['when']}" for r in found[:5])
    return None, f"Error: not booked. That did not match exactly one session. Ask the caller which one: {listing}."


def _join_check(ctx: ActionContext, args: dict) -> tuple[str, str, str] | str:
    """What join_session can know before any read-back: -> (code, first, last), or the refusal text.
    Order: session given -> session exists -> name given -> not a placeholder -> last name given."""
    ref = args.get("session")
    if not isinstance(ref, str) or not ref.strip():
        return "Error: the session code from list_sessions is needed."
    ref = ref.strip()
    if _CODE.fullmatch(ref):
        if sessions_.find_open(ctx.business_id, ref) is None:
            return _no_such_session(ctx.business_id, None, sessions_.list_open(ctx.business_id, seats=False))
    else:
        ref, problem = resolve_session(ctx.business_id, ref, _last_bot_text(ctx))
        if ref is None:
            return problem
    text = lambda key: args[key].strip() if isinstance(args.get(key), str) else ""  # noqa: E731
    first, last = text("first_name") or text("name"), text("last_name")
    if not first or _is_placeholder(first, last):
        return NAME_NEEDED
    if not last and len(first.split()) < 2:
        return LAST_NAME_NEEDED
    return ref, first, last


def join_session_precheck(ctx: ActionContext, args: dict) -> str | None:
    """Refuse before the read-back: no such session, no real name. (The read-back check stays in the handler.)"""
    checked = _join_check(ctx, args)
    return checked if isinstance(checked, str) else None


def join_session(ctx: ActionContext, args: dict) -> str:
    checked = _join_check(ctx, args)
    if isinstance(checked, str):
        return checked
    ref, first, last = checked
    if not _read_back_asked(ctx, first.split()[0]):
        return READBACK_NEEDED
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
        "session": {"type": "string", "description": "The session's 8-character code exactly as list_sessions printed it; never invent one."},
        "first_name": {"type": "string", "description": "Caller's first name."},
        "last_name": {"type": "string", "description": "Caller's last name. Ask for it if they gave only a first name."},
        "phone": {"type": "string", "description": "Only if the caller's number is not known: full international number."},
    },
    required=("session", "first_name"),
    side_effect=True,
    handler=join_session,
    precheck=join_session_precheck,
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
    precheck=create_booking_precheck,
    optional=True,
))
register(Tool(
    name="cancel_booking",
    description="Cancel a booking the caller made, by its reference (given when it was booked).",
    parameters={
        "reference": {"type": "string", "description": "The booking reference the caller read out or was given (8 characters)."},
        "phone": {"type": "string", "description": "Only if the caller's number is not known."},
    },
    required=("reference",),
    side_effect=True,
    handler=cancel_booking,
    optional=True,
))
