"""Prompt builder (plan/02-local-rag.md "Answer pipeline" / "Prompt rules")."""

from app.db.models import BusinessProfile, BusinessSummary
from app.rag.retrieve import RetrievedChunk

SYSTEM_PROMPT = (
    "Answer only from the provided business profile and retrieved chunks below. "
    "Never use outside knowledge about this business or any other.\n"
    "If the answer is not in the profile or chunks, state that plainly and offer "
    "to take a message or transfer to a human.\n"
    "Keep answers short, as they will be spoken aloud.\n"
    "Answer in the same language the user is asking in."
)


BOOKING_TOOLS = ("check_availability", "create_booking", "cancel_booking")

BOOKING_PROMPT = (
    "Bookings: when the caller wants to book a table or an appointment, call check_availability "
    "first and offer only times it says are free. Then read all the details back (date, time, "
    "people, service, name) and wait for a clear yes before calling create_booking with "
    "confirmed=true; if they say no or change their mind, don't book. Read the booking reference "
    "aloud afterwards. To cancel, ask for the reference and call cancel_booking, again only after "
    "a yes. Never say something is booked or cancelled unless the tool said so."
)

SESSION_TOOLS = ("list_sessions", "join_session")

SESSION_PROMPT = (
    "Sessions: for any question about sessions, classes, schedule, specials, what is on today or "
    "what is available, call list_sessions (pass a date if the caller names a day) instead of "
    "saying you have no information. Read the names and times aloud, never the codes. If it lists "
    "none, say there are no open sessions then. To sign someone up, ask for their name, read back "
    "the session and time, and after a clear yes call join_session with confirmed=true. Never say "
    "someone is booked unless the tool said so."
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
) -> list[dict]:
    """System message (rules + profile + locations + summary + chunks) + history + the new question."""
    sections = [SYSTEM_PROMPT, tools_prompt(enabled_tools), format_profile(profile), locations, format_summary(summary), format_chunks(chunks)]
    system_content = "\n\n".join(s for s in sections if s)
    return [{"role": "system", "content": system_content}, *history, {"role": "user", "content": question}]
