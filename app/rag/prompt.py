"""Prompt builder (plan/02-local-rag.md "Answer pipeline" / "Prompt rules")."""

from app.db.models import BusinessProfile, BusinessSummary
from app.rag.retrieve import RetrievedChunk

SYSTEM_PROMPT = (
    "Answer only from the provided business profile and retrieved chunks below. "
    "Never use outside knowledge about this business or any other.\n"
    "If the answer is not in the profile or chunks, state that plainly and offer "
    "to take a message or transfer to a human.\n"
    "If the question assumes something (a date, a reason, a change) that is absent from "
    "or contradicts the chunks, say you cannot confirm it; do not repeat it as true.\n"
    "Keep answers short, as they will be spoken aloud: one or two sentences, no lists or bullets. "
    "Do not repeat what you already said or what the caller already told you (name, chosen session, "
    "time) unless they ask; never ask for something they already gave. If the caller's words are "
    "garbled but close to one option, name that option and ask a yes/no question instead of "
    "listing everything; if several sessions share that name, offer only the soonest one in one short sentence "
    "(\"Do you mean <session name> on <day> at <time>?\") and never explain the mix-up.\n"
    "Answer in the same language the user is asking in."
)


BOOKING_TOOLS = ("check_availability", "create_booking", "cancel_booking")

BOOKING_PROMPT = (
    "Bookings: when the caller wants to book a table or an appointment, call check_availability "
    "first and offer only times it says are free. Then read all the details back (date, time, "
    "people, service, first and last name spelled out; ask for the last name if they gave only a first name) and wait for a clear yes before calling create_booking with "
    "confirmed=true; if they say no or change their mind, don't book. Read the booking reference "
    "aloud afterwards. To cancel, ask for the reference and call cancel_booking, again only after "
    "a yes. To change a booking, cancel it and book the new one, each only after a yes; there is no "
    "other way to move it. Say every time with AM or PM. Never say something is booked, changed or cancelled unless the tool said so."
)

SESSION_TOOLS = ("list_sessions", "join_session")

SESSION_PROMPT = (
    "Sessions: for any question about sessions, classes, schedule, specials, what is on today or "
    "what is available, call list_sessions (pass a date if the caller names a day) instead of "
    "saying you have no information. Read the names and start times aloud, never the codes. Say every time with AM or PM (\"10:00 AM\", \"6:30 PM\"), never on a 24-hour clock; \"10:00 AM to 11:00 AM\" is "
    "one session that starts at 10:00 AM. List the sessions only once: after that, unless the caller asks for the list "
    "again, name only the one session they mean; if they did not pick a day or time, that is the soonest one with that "
    "name (the first in the list, marked SOONEST in the open sessions), never a later day. If it lists "
    "none, say there are no open sessions then. "
    "Name only sessions that list_sessions returned or that are in the open-session list, with their real day and time; never make up a session, a time or a number of seats. Say \"full\" or \"fully booked\" only for a session marked FULL, never for one with free seats. If the caller names a session that is not in the list, it may be a private one they were invited to (private sessions are never listed or offered): call join_session at once with the name they said as `session` (first_name empty until you have it) and follow its answer: it asks for the name if the session exists, or gives the sentence to say if there is none. "
    "To sign someone up, ask for their first and last name (ask for the last name if they give only "
    "a first name), read back the session, day, time and the name in one short sentence, in this form: \"<session name> <day> from <start> to <end>, <First Last>. Shall I book it?\" (no seat counts, no apology, no \"again\"). Only after the caller's clear yes call "
    "join_session, with confirmed=true and the session code exactly as list_sessions gave it (never a name you made up). If you no longer have "
    "the code, call list_sessions again first; never pass just a session name for a listed session, because several sessions can share it (the one exception is a name the caller said that is not in the list, above). A clear yes to your "
    "read-back that included the name is the confirmation: do not ask \"Shall I book it?\" a second time. "
    "A yes given before you had the name does not count: once you have it, read everything back and ask again. "
    "So ask for the name first and \"Shall I book it?\" only once, with the name in it; never ask it before you have the name. Never apologise and never say \"let me confirm\": the first read-back is not a correction. Someone is booked only "
    "when the tool result starts with \"Booked\". If it starts with \"Not done\" or \"Error\", nothing was booked: do not say it was, "
    "and do not repeat the read-back as if it were done. Ask the caller to confirm, or say in plain words what went wrong (for example that there is no such session) and follow the tool's instruction; never blame a code or a technical issue. When a tool result says \"Say exactly\", say that sentence as written. When the answer is that there is no such session, say there is none and name the open ones with their day and start time; never say \"one of these\" without naming them. Put AM or PM after every time, including the end time (\"from 6:00 PM to 7:00 PM\")."
)


def tools_prompt(enabled: frozenset[str] | set[str]) -> str:
    """Prompt rules for the optional tools this business enabled (#151); "" when there are none.
    Only tools that are really offered are described, so the model isn't told about others."""
    parts = [
        text for names, text in ((BOOKING_TOOLS, BOOKING_PROMPT), (SESSION_TOOLS, SESSION_PROMPT))
        if any(t in enabled for t in names)
    ]
    return "\n\n".join(parts)


def format_profile(profile: BusinessProfile | None) -> str:
    """Known facts, one per line, skipping unset fields. No internal ids (business_id/place_id)."""
    if profile is None:
        return "No business profile available."
    lines = []
    if profile.name:
        lines.append(f"Name: {profile.name}")
    if profile.address:
        lines.append(f"Address: {profile.address}")
    if profile.phone:
        lines.append(f"Phone: {profile.phone}")
    if profile.email:
        lines.append(f"Email: {profile.email}")
    if profile.opening_hours:
        lines.append(f"Hours: {profile.opening_hours}")
    if profile.booking_policy:
        lines.append(f"Booking policy: {profile.booking_policy}")
    if profile.price_range:
        lines.append(f"Price range: {profile.price_range}")
    if profile.languages:
        lines.append(f"Languages: {', '.join(profile.languages)}")
    return "\n".join(lines) if lines else "No business profile available."


SUMMARY_MAX_CHARS = 480  # plan 07 §6: the summary costs at most ~120 tokens of every prompt


def format_summary(summary: BusinessSummary | None) -> str:
    """The business summary (plan 07 §6), or "" when there's none. Not a source of facts:
    guest themes are opinions and the model is told to attribute them. Highlights and
    themes are dropped from the end until the block fits SUMMARY_MAX_CHARS."""
    if summary is None:
        return ""
    highlights = list(summary.highlights or [])
    themes = list(summary.guest_themes or [])

    def render() -> list[str]:
        lines = []
        if summary.one_liner:
            lines.append(f"About the business: {summary.one_liner}")
        if highlights:
            lines.append(f"Highlights: {'; '.join(highlights)}")
        if themes:
            lines.append(f"Guests often mention: {'; '.join(themes)}")
        if summary.tone:
            lines.append(f"Brand tone: {summary.tone}")
        return lines

    lines = render()
    while len("\n".join(lines)) > SUMMARY_MAX_CHARS and (highlights or themes):
        (highlights if len(highlights) >= len(themes) else themes).pop()
        lines = render()
    if not lines:
        return ""
    rule = (
        "Business summary (for what the place is like and the tone to use; not a source of "
        "prices, hours or other facts. What guests mention is their opinion: say \"guests "
        "often say…\", never state it as fact):"
    )
    return "\n".join([rule, *lines])


def format_chunks(chunks: list[RetrievedChunk]) -> str:
    """Retrieved chunks, numbered, with their section heading and text."""
    if not chunks:
        return "No matching information found in the knowledge base."
    parts = [f"[{i}] {c.section_heading or '(no heading)'}\n{c.text}" for i, c in enumerate(chunks, start=1)]
    return "\n\n".join(parts)


def build_prompt(
    profile: BusinessProfile | None,
    chunks: list[RetrievedChunk],
    history: list[dict],
    question: str,
    summary: BusinessSummary | None = None,
    locations: str = "",
    enabled_tools: frozenset[str] | set[str] = frozenset(),
    now_note: str = "",
) -> list[dict]:
    """System message (rules + profile + locations + summary + chunks) + history + the new question."""
    sections = [SYSTEM_PROMPT, now_note, tools_prompt(enabled_tools), format_profile(profile), locations, format_summary(summary), format_chunks(chunks)]
    system_content = "\n\n".join(s for s in sections if s)
    return [{"role": "system", "content": system_content}, *history, {"role": "user", "content": question}]
