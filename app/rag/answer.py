"""Full answer pipeline: retrieve → build prompt → generate (plan/02-local-rag.md)."""

import re
import uuid
from dataclasses import dataclass
from datetime import datetime, time
from zoneinfo import ZoneInfo

from sqlalchemy import select

from app.config import get_settings
from app.db import tenant_session
from app.actions.booking import rules as R
from app.actions.booking.tools import spoken_sessions
from app.actions.booking.sessions import list_open
from app.db.models import Business, BusinessProfile, BusinessSummary
from app.llm import get_chat_client
from app.rag.location import default_location_id, format_locations
from app.rag.prompt import build_prompt
from app.rag.retrieve import RetrievedChunk, retrieve
from app.voice.tools import enabled_tool_names, run_tool, tools_for

MAX_TOOL_ROUNDS = 3  # LLM → tool → LLM ... before giving up on a turn (same cap as voice)
GIVE_UP_ANSWER = "Sorry, I couldn't finish that. Can I take a message?"
EMPTY_ANSWER = "Sorry, I didn't catch that. Could you say it again?"


NOT_BOOKED_ANSWER = "I haven't booked anything yet. Shall I go ahead and book it?"
CLAIM = re.compile(r"\b(booked|confirmed|reserved|you'?re all set|you are all set|signed up)\b", re.I)
NOT_A_CLAIM = re.compile(
    r"\b(not|n't|never|unable|fully|already|no|out|up|solid|full|sold|if|once|before|until|whether|would you like|shall|should|can i|could i|"
    r"let me|ready to|want me|to be|will be|please confirm|to confirm|so i can|then)\b|n’t", re.I)


def claims_booking(text: str) -> bool:
    """Does the reply say, in a plain statement, that something is booked/confirmed/reserved?"""
    for sentence in re.split(r"(?<=[.!?\n])\s+", text):
        if sentence.rstrip().endswith("?") or NOT_A_CLAIM.search(sentence):
            continue
        if CLAIM.search(sentence):
            return True
    return False


def guard_booking_claim(answer: str, messages: list[dict], start: int, history: list[dict] | None) -> str:
    """The model sometimes writes "booked, reference 3F9A…" without calling the tool. A first claim in
    the conversation with no "Booked" tool result in this turn is replaced by a question (or, when a tool
    refused this turn with a `Say exactly: "..."` sentence, by that sentence); a claim after
    an earlier one (a recap, "your booking is confirmed") is left alone."""
    if not claims_booking(answer):
        return answer
    if any(m.get("role") == "tool" and str(m.get("content") or "").startswith("Booked") for m in messages[start:]):
        return answer
    if any(m.get("role") == "assistant" and claims_booking(str(m.get("content") or "")) for m in history or []):
        return answer
    for m in reversed(messages[start:]):  # a refusal the tool worded for the caller beats the generic question
        said = re.search(r'Say exactly: "([^"]+)"', str(m.get("content") or "")) if m.get("role") == "tool" else None
        if said:
            return said[1]
    return NOT_BOOKED_ANSWER


FILLER_SENTENCE = re.compile(
    r"^(?:i\s+(?:apologi[sz]e|am sorry|'m sorry)|sorry|my apologies|apologies|let me (?:confirm|clarify|recap|double[- ]check))\b", re.I)


def drop_filler(text: str) -> str:
    """The first read-back is not a correction: leading sentences that apologise or say "let me
    confirm again" are cut, so the reply opens with the actual question. Never empties a reply."""
    sentences = re.split(r"(?<=[.!?])\s+", text.strip())
    kept = [x for x in sentences if not FILLER_SENTENCE.match(x)]
    return " ".join(kept) if kept else text


ASKS_NAME = re.compile(
    r"(first and last name|last name|surname|full name|what(?:'s| is) your name|"
    r"(?:have|get|take|tell me|give me|spell)\s+(?:me\s+)?(?:your|the)\s+name)", re.I)
NAME_GIVEN_NUDGE = (
    "The caller already gave their first and last name{name}. Do not ask for the name again: use it, read the "
    "session, its day and time and the name back, and ask \"Shall I book it?\"."
)
_NAME_LEAD = re.compile(r"^\s*(?:(?:yes|yeah|sure|ok(?:ay)?)[,.!]?\s+)?(?:(?:my name is|my name's|name is|"
                        r"name's|it is|it's|i am|i'm|this is)\s+)", re.I)
_NAME_WORD = re.compile(r"[A-Za-z][A-Za-z'\u2019-]*")
NOT_NAME_WORDS = frozenset(
    "yes yeah yep no nope ok okay sure please thanks thank you hello hi hey the a an and or for to me my book "
    "booking sign up session sessions class one that this it is what which when how today tomorrow morning "
    "evening great fine good correct right sounds perfect at from in on not looking next last later now here there".split())


def given_name(text: str, not_names: set[str] | frozenset[str] = frozenset()) -> str | None:
    """The caller's line as a full name ("Abraham Lincoln", "my name is Abe Lincoln", "it's Abe Lincoln."), or
    None. Unless it follows "my name is", the words must be capitalized, so "sign me up" and "I'm at work" are not a name; words of
    session names (`not_names`) and common booking words never are."""
    m = _NAME_LEAD.match(text)
    rest = text[m.end():] if m else text
    rest = rest.strip().rstrip(".!")
    words = rest.split()
    if not 2 <= len(words) <= 3 or not all(_NAME_WORD.fullmatch(w) for w in words):
        return None
    strong = bool(m and re.match(r"\s*(?:\w+[,.!]?\s+)?(?:my name|name is|name's)", m.group(0), re.I))
    if not strong and not all(w[0].isupper() for w in words):  # "my name is x y" may be typed in lowercase
        return None
    blocked = NOT_NAME_WORDS | {n.lower() for n in not_names}
    if any(w.lower() in blocked for w in words):
        return None
    return " ".join(words)


def known_name(question: str, history: list[dict] | None, not_names=frozenset()) -> str | None:
    """A full name the caller gave in this line or an earlier one (latest first)."""
    for text in [question] + [str(m.get("content") or "") for m in reversed(history or []) if m.get("role") == "user"]:
        if name := given_name(text, not_names):
            return name
    return None


def repeats_name_question(answer: str, history: list[dict] | None, question: str = "",
                          not_names: set[str] | frozenset[str] = frozenset()) -> bool:
    """The reply asks for the name although this or an earlier caller line was a full name."""
    return bool(ASKS_NAME.search(answer)) and known_name(question, history, not_names) is not None


ASKS_LAST_NAME = re.compile(r"\b(last name|surname|family name)\b", re.I)
ASKS_FIRST_TOO = re.compile(r"\bfirst\b|full name", re.I)
SAYS_OWN_NAME = re.compile(r"\b(my name is|my name's|name is|call me|this is|i'm|i am)\s+[a-z]", re.I)
FIRST_AND_LAST_NUDGE = (
    "You do not have the caller's name yet. Ask for their first and last name in one short question; "
    "do not ask for only the last name."
)


def asks_only_last_name(answer: str, question: str, history: list[dict] | None) -> bool:
    """The reply asks for just the last name although the caller never gave a first name: no earlier reply
    asked for the name and no caller line said "my name is ...". Asked that way a live caller gave only
    "Lincoln", and the bot booked "Lincoln Lincoln"."""
    if not ASKS_LAST_NAME.search(answer) or ASKS_FIRST_TOO.search(answer):
        return False
    if any(m.get("role") == "assistant" and ASKS_NAME.search(str(m.get("content") or "")) for m in history or []):
        return False
    said = [question] + [str(m.get("content") or "") for m in history or [] if m.get("role") == "user"]
    return not any(SAYS_OWN_NAME.search(t) for t in said)


_SESSION_LINE = re.compile(r"^[0-9A-F]{8} · (.+?) · ", re.M)


def session_name_words(messages: list[dict]) -> set[str]:
    """Words of the open sessions' names in the prompt (now_note), which are never a caller's name."""
    system = str(messages[0].get("content") or "") if messages and messages[0].get("role") == "system" else ""
    return {w for name in _SESSION_LINE.findall(system) for w in name.split()}


_CLOCK = r"\d{1,2}(?::\d{2})?\s?(?:am|pm)?"
_RANGE = re.compile(rf"({_CLOCK})\s?(?:to|until|-|\u2013)\s?{_CLOCK}", re.I)  # "10:00 to 11:00" is one session
_TIME = re.compile(r"\b\d{1,2}(?::\d{2})?\s?(?:am|pm)\b|\b\d{1,2}:\d{2}\b", re.I)
WANTS_LIST = re.compile(
    r"\b(list|all|options?|which|what (sessions|do you have|is on|else)|details?|tell me (more|about)|"
    r"available|schedule|other|else|again|repeat|anything|what'?s on|any other|monday|tuesday|wednesday|thursday|friday|"
    r"saturday|sunday|today|tomorrow)\b", re.I)
NO_RELIST_NUDGE = (
    "You already listed the sessions and the caller did not ask for the list again. Do not name several "
    "sessions or times: name only the one session the caller means (one day, one time), or ask one short question."
)


def session_times(text: str) -> set[str]:
    """The distinct start times a reply names ("10:00 to 11:00" counts once)."""
    return {t.lower().replace(" ", "") for t in _TIME.findall(_RANGE.sub(r"\1", text))}


def relists_sessions(answer: str, question: str, history: list[dict] | None) -> bool:
    """An earlier reply already listed several sessions, the caller did not ask for a list, and this
    reply lists several again (the open-session list in every prompt tempts the model to recite it)."""
    if len(session_times(answer)) < 2 or WANTS_LIST.search(question):
        return False
    return any(m.get("role") == "assistant" and len(session_times(str(m.get("content") or ""))) >= 2
               for m in history or [])


MAX_REPLY_WORDS = 40  # the prompt asks for one or two sentences; this is the backstop


def shorten(text: str, max_words: int = MAX_REPLY_WORDS) -> str:
    """A reply over the cap keeps its first sentences that fit, plus a closing question
    ("Shall I book it?") when there is one, so the caller still knows what to answer."""
    if len(text.split()) <= max_words:
        return text
    sentences = re.split(r"(?:(?<=[.!?])|(?<=[.!?][\"\u201d]))\s+", text.strip())
    last = sentences[-1] if len(sentences) > 1 and sentences[-1].endswith("?") else ""
    budget = max_words - len(last.split())
    kept: list[str] = []
    for sentence in sentences[: len(sentences) - (1 if last else 0)]:
        if kept and len(" ".join(kept + [sentence]).split()) > budget:
            break
        kept.append(sentence)
        if len(" ".join(kept).split()) >= budget:
            break
    result = " ".join(kept + ([last] if last else []))
    if len(result.split()) > max_words:  # one long sentence, or a long closing question
        if last and len(last.split()) <= max_words - 8:  # keep what is asked about, then the question
            head = " ".join(" ".join(kept).split()[: max_words - len(last.split())]).rstrip(",;:.")
            return f"{head}. {last}"
        return " ".join((last or result).split()[:max_words]).rstrip(",;:") + ("?" if last else ".")
    return result


def prompt_sessions(messages: list[dict]) -> list[dict]:
    """The open sessions listed in the system prompt (`session_lines`): [{"name", "when", "full"}], [] if none.
    Only messages[0] (the system prompt) is read: tool results and the caller's words never count."""
    system = str(messages[0].get("content") or "") if messages and messages[0].get("role") == "system" else ""
    rows = []
    for line in re.findall(r"^[0-9A-F]{8} · (.+)$", system, re.M):
        parts = line.split(" · ")
        if len(parts) >= 2:
            rows.append({"name": parts[0], "when": parts[1], "full": "FULL" in parts[2:]})
    return rows


# A reply that says there is no such session / it is not there. Sentence level, apostrophes straight or curly.
REFUSES = re.compile(
    r"\bnot\s+(?:on|in)\s+(?:the|our)\s+(?:schedule|list)\b|\bno\s+such\b"
    r"|\bthere\s*(?:is|['\u2019]s|are)\s+no\b|\bthere\s+(?:isn['\u2019]t|aren['\u2019]t)\s+(?:a|an|any)\b"
    r"|\b(?:don['\u2019]t|do\s+not|doesn['\u2019]t|does\s+not|can['\u2019]t|cannot|couldn['\u2019]t)"
    r"\s+(?:have|see|find|offer|run)\b"
    r"|\bisn['\u2019]t\s+(?:a|an|on|in|available|scheduled|listed)\b"
    r"|\bnot\s+(?:available|scheduled|listed|there|one\s+of|among)\b", re.I)
NO_SESSIONS_AT_ALL = re.compile(r"\bno\s+(?:open\s+|other\s+|more\s+)?(?:sessions|classes)\b", re.I)  # "none on Sunday"
ASKS_FOR_SESSION = re.compile(r"\b(book|sign (me )?up|join|reserve|sessions?|class(es)?)\b", re.I)
# Words that do not tie a refusal to what the caller asked for (the refusal "I couldn't find a booking with that
# reference" answers "cancel my session booking" about a booking, not a session).
_FILLER = frozenset(
    "that this these those with from your have what when where which would could should about there their they them "
    "then than will just like want need please thank thanks sure okay yeah hello here some more also only very much "
    "into over does done make made know tell give take going able "
    "book books booked booking bookings session sessions class classes today tomorrow tonight morning afternoon "
    "evening noon week weekend monday tuesday wednesday thursday friday saturday sunday time times".split())


def _content_words(text: str, rows: list[dict]) -> set[str]:
    """Words of 4+ letters that are not filler and not part of an open session's name: what the caller
    actually named (a session that is not on the list), if anything."""
    in_names = {w for r in rows for w in re.findall(r"[a-z]+", r["name"].lower())}
    return {w for w in re.findall(r"[a-z]{4,}", text.lower()) if w not in _FILLER and w not in in_names}


def _sentences(text: str) -> list[str]:
    return re.split(r"(?<=[.!?])\s+", text.strip())


def _refusal_index(text: str, question: str, rows: list[dict]) -> int | None:
    """Index of the first sentence that says a specific session is not there (None if no sentence does):
    it must also share a content word with the caller's `question` ("Steam Bath" in both), so a refusal
    about something else ("I can't find a booking with that reference") is left alone."""
    asked = _content_words(question, rows)
    for i, sentence in enumerate(_sentences(text)):
        if REFUSES.search(sentence) and not NO_SESSIONS_AT_ALL.search(sentence) \
                and asked & _content_words(sentence, rows):
            return i
    return None


def _names_row(text: str, row: dict) -> bool:
    """Every word of the session's name is in `text` (the rule resolve_session uses)."""
    words = set(re.findall(r"[a-z0-9]+", text.lower()))
    name = set(re.findall(r"[a-z0-9]+", row["name"].lower()))
    return bool(name) and name <= words


def needs_session_offer(text: str, question: str, rows: list[dict]) -> bool:
    """The caller asked for a session that is not on the schedule and the reply refuses without naming
    a real one ("We don't have a Steam Bath session. Would you like one of those instead?"): True.
    False when no session is free, the caller named a real one, or the reply already names one."""
    return (
        any(not r["full"] for r in rows)
        and bool(ASKS_FOR_SESSION.search(question))
        and not any(_names_row(question, r) for r in rows)
        and _refusal_index(text, question, rows) is not None
        and not any(_names_row(text, r) for r in rows)
    )


def offer_open_sessions(answer: str, question: str, rows: list[dict], business_id: uuid.UUID, limit: int = 3) -> str:
    """Code, not the model, names the real sessions after a refusal: the refusal sentence, then
    "We have <name> <day> at <start>, ...". Fewer sessions while the reply is over MAX_REPLY_WORDS."""
    if not needs_session_offer(answer, question, rows):
        return answer
    sentences = _sentences(answer)
    cut = _refusal_index(answer, question, rows) + 1
    head = " ".join(sentences[:cut])
    asks = " ".join(x for x in sentences[cut:] if x.endswith("?")) or "Would you like one of those?"  # keep the model's question
    free = [r for r in rows if not r["full"]]
    while True:
        reply = f"{head} We have {spoken_sessions(business_id, free, limit)}. {asks}"
        if len(reply.split()) <= MAX_REPLY_WORDS or limit <= 1:
            return reply
        limit -= 1


# "6:00 PM to 7:00" -> "... to 7:00 PM": the model drops the AM/PM of an end time now and then.
_END_TIME = re.compile(
    r"\b(?P<sh>\d{1,2})(?::(?P<sm>\d{2}))?\s?(?P<mer>[ap])\.?m\b\.?\s*(?:to|until|till|-|\u2013)\s*"
    r"(?P<end>(?P<eh>\d{1,2}):(?P<em>\d{2})(?!\d)|(?P<bh>\d{1,2})(?=[.,;!?](?!\d)|$))(?!\s?[ap]\.?m\b)(?!:)",
    re.I | re.M)


def fix_end_times(text: str) -> str:
    """Puts the missing AM/PM on the end of a time range whose start has one: the start's meridiem, or the
    other when that would end the range before it starts; an end past 12 is read as 24-hour. Only the end
    changes; "6 PM to 8 people", "10:00 to 11:00" and complete ranges are left alone."""
    def fix(m: re.Match) -> str:
        sh, sm = int(m["sh"]), int(m["sm"] or 0)
        eh, em = int(m["eh"] or m["bh"]), int(m["em"] or 0)
        if not (1 <= sh <= 12 and sm < 60 and em < 60 and 1 <= eh <= 23):
            return m[0]
        start = sh % 12 * 60 + sm + (720 if m["mer"].lower() == "p" else 0)
        if eh > 12:
            end = eh * 60 + em
        else:
            end = eh % 12 * 60 + em + (720 if m["mer"].lower() == "p" else 0)
            if end <= start:
                end = eh % 12 * 60 + em + (0 if m["mer"].lower() == "p" else 720)
        clock = R.clock(time(end // 60 % 24, end % 60))
        return m[0][: m.start("end") - m.start()] + clock + m[0][m.end("end") - m.start():]
    return _END_TIME.sub(fix, text)


def turn_fallback(messages: list[dict], start: int, default: str) -> str:
    """What to say when the model ends a turn without words. If a booking or cancellation went
    through in this turn (tool results after `messages[start:]`), say that, with its reference:
    a live call booked the caller and then said "couldn't find that". Otherwise `default`."""
    for m in reversed(messages[start:]):
        content = str(m.get("content") or "")
        if m.get("role") == "tool" and content.startswith(("Booked", "Cancelled")):
            return content.split(" Read the reference")[0].strip()
    return default


@dataclass(frozen=True)
class AnswerResult:
    answer: str
    chunks: list[RetrievedChunk]  # what was retrieved, for debug/display
    profile: BusinessProfile | None
    messages: list[dict]  # the exact prompt sent to the LLM, for debug/display


def session_lines(rows: list[dict]) -> list[str]:
    """One line per open session (rows soonest first). When several share a name, the first is marked
    SOONEST: a caller who says "the Polish session" without a day means that one, and the model picked a
    later one from an unmarked list."""
    counts: dict[str, int] = {}
    for r in rows:
        counts[r["name"]] = counts.get(r["name"], 0) + 1
    seen: set[str] = set()
    out = []
    for r in rows:
        mark = " · SOONEST of this name" if counts[r["name"]] > 1 and r["name"] not in seen else ""
        seen.add(r["name"])
        out.append(f"{r['ref']} · {r['name']} · {r['when']}" + (" · FULL" if r["full"] else "") + mark)
    return out


def open_sessions_note(business_id: uuid.UUID, enabled) -> str:
    """The open sessions with their codes when sessions can be booked ("" otherwise, or when none are open):
    tool results are not kept in the history, so without this the model loses the codes between turns and
    invents them (or whole sessions)."""
    if "join_session" not in enabled:
        return ""
    rows = list_open(business_id)
    if not rows:
        return ""
    return "\n".join([
        "Open sessions (for your reference: codes are for join_session only, never read them aloud; "
        "do not recite this list unless the caller asks what is on; private sessions are never listed, so when a caller "
        "names a session that is not here, call join_session with the name they said as `session` (first_name empty "
        "until you have it) and follow its answer: it books or asks for the name if the session exists, and gives the "
        "exact sentence to say if there is none):",
        *session_lines(rows),
    ])


def now_note(session, business_id: uuid.UUID, enabled) -> str:
    """Today's date and time in the business's zone (the model has no clock) and, when sessions
    can be booked, the open ones with their codes (`open_sessions_note`)."""
    zone = ZoneInfo(session.scalar(select(Business.timezone).where(Business.id == business_id)) or "UTC")
    lines = [f"Now: {datetime.now(zone):%A %d %B %Y}, {R.clock(datetime.now(zone))} ({zone.key})."]
    sessions_note = open_sessions_note(business_id, enabled)
    if sessions_note:
        lines.append(sessions_note)
    return "\n".join(lines)


def prepare_prompt(
    business_id: uuid.UUID, question: str, history: list[dict] | None = None, top_n: int = 5
) -> tuple[BusinessProfile | None, list[RetrievedChunk], list[dict]]:
    """Retrieve and build the prompt, without calling the LLM.

    Shared by answering and `/debug/prompt`, so the debug view shows exactly what the
    model gets. The DB transaction closes here — it isn't held open during generation.
    """
    enabled = enabled_tool_names(business_id)
    with tenant_session(business_id) as session:
        profile = session.get(BusinessProfile, business_id)
        summary = session.get(BusinessSummary, business_id)
        location_id = default_location_id(session, business_id)  # DEC-49
        chunks = retrieve(session, business_id, question, top_n, location_id=location_id)
        messages = build_prompt(
            profile, chunks, history or [], question, summary,
            locations=format_locations(session, business_id, location_id), enabled_tools=enabled,
            now_note=now_note(session, business_id, enabled),
        )
    return profile, chunks, messages


def _tool_call_message(content: str | None, calls: list[dict]) -> dict:
    return {
        "role": "assistant",
        "content": content or None,
        "tool_calls": [
            {"id": c["id"], "type": "function", "function": {"name": c["name"], "arguments": c["arguments"]}}
            for c in calls
        ],
    }


def _run_calls(business_id: uuid.UUID, conversation_id: uuid.UUID | None, calls: list[dict]) -> list[dict]:
    """Run each call through the executor; `business_id`/`conversation_id` come from the
    request, never from the model's arguments."""
    return [
        {"role": "tool", "tool_call_id": c["id"],
         "content": run_tool(business_id, c["name"], c["arguments"], conversation_id)}
        for c in calls
    ]


def answer_question(
    business_id: uuid.UUID, question: str, history: list[dict] | None = None, top_n: int = 5,
    conversation_id: uuid.UUID | None = None,
) -> AnswerResult:
    """Non-streaming: retrieve, build the prompt, then LLM → tools → LLM until the model
    answers in text (at most MAX_TOOL_ROUNDS rounds)."""
    profile, chunks, messages = prepare_prompt(business_id, question, history, top_n)
    prompt = list(messages)  # `messages` grows with tool turns; the debug view shows the prompt as sent first
    settings = get_settings()
    client = get_chat_client()
    tools = tools_for(business_id)  # #151: bookings only for businesses that enabled them
    start = len(messages)
    answer = turn_fallback(messages, start, GIVE_UP_ANSWER)
    empty_retried = False
    rounds = 0
    while rounds < MAX_TOOL_ROUNDS:  # the one retry after an empty reply doesn't use up a round
        response = client.chat.completions.create(
            model=settings.llm_model, messages=messages, tools=tools,
            reasoning_effort=settings.chat_reasoning_effort,
        )
        message = response.choices[0].message
        if not message.tool_calls:
            if (message.content or "").strip():
                answer = shorten(drop_filler(message.content))
            elif not empty_retried:  # an empty reply is a lost answer: ask once more
                empty_retried = True
                continue
            else:
                answer = turn_fallback(messages, start, EMPTY_ANSWER)
            break
        rounds += 1
        calls = [
            {"id": tc.id, "name": tc.function.name, "arguments": tc.function.arguments}
            for tc in message.tool_calls
        ]
        messages.append(_tool_call_message(message.content, calls))
        messages.extend(_run_calls(business_id, conversation_id, calls))
        answer = turn_fallback(messages, start, GIVE_UP_ANSWER)
    not_names = session_name_words(messages)
    name = known_name(question, history, not_names)
    checks = [  # (reply is wrong, nudge, is this retried reply still wrong): each fixed with at most one retry
        (repeats_name_question(answer, history, question, not_names),
         NAME_GIVEN_NUDGE.format(name=f" ({name})" if name else ""), lambda t: bool(ASKS_NAME.search(t))),
        (asks_only_last_name(answer, question, history), FIRST_AND_LAST_NUDGE,
         lambda t: asks_only_last_name(t, question, history)),
        (relists_sessions(answer, question, history), NO_RELIST_NUDGE,
         lambda t: relists_sessions(t, question, history)),
    ]
    nudges = [nudge for wrong, nudge, _ in checks if wrong]
    if nudges:  # one more try, with the instructions spelled out; text only, no tools
        retry = client.chat.completions.create(
            model=settings.llm_model, messages=messages + [{"role": "system", "content": " ".join(nudges)}],
            reasoning_effort=settings.chat_reasoning_effort,
        ).choices[0].message
        text = (retry.content or "").strip()
        if text and not any(wrong and still(text) for wrong, _, still in checks):
            answer = shorten(drop_filler(text))
    answer = guard_booking_claim(answer, messages, start, history)
    if answer not in (GIVE_UP_ANSWER, EMPTY_ANSWER, NOT_BOOKED_ANSWER):
        answer = offer_open_sessions(answer, question, prompt_sessions(messages), business_id)
        answer = fix_end_times(answer)
    return AnswerResult(answer=answer, chunks=chunks, profile=profile, messages=prompt)


def stream_answer(
    business_id: uuid.UUID, question: str, history: list[dict] | None = None, top_n: int = 5,
    conversation_id: uuid.UUID | None = None,
):
    """Streaming: same retrieval/prompt step and tool loop; yields answer text piece by
    piece (text the model writes before a tool call is yielded too).
    There are no reply-text guards here (booking claim, session offer, end times): they need the whole reply."""
    _, _, messages = prepare_prompt(business_id, question, history, top_n)

    settings = get_settings()
    client = get_chat_client()
    tools = tools_for(business_id)
    start = len(messages)
    empty_retried = False
    rounds = 0
    while rounds < MAX_TOOL_ROUNDS:  # the one retry after an empty reply doesn't use up a round
        stream = client.chat.completions.create(
            model=settings.llm_model, messages=messages, tools=tools, stream=True,
            reasoning_effort=settings.chat_reasoning_effort,
        )
        text = ""
        calls: dict[int, dict] = {}
        for piece in stream:
            if not piece.choices:
                continue
            delta = piece.choices[0].delta
            for tc in delta.tool_calls or []:
                call = calls.setdefault(tc.index, {"id": "", "name": "", "arguments": ""})
                if tc.id:
                    call["id"] = tc.id
                if tc.function and tc.function.name:
                    call["name"] += tc.function.name
                if tc.function and tc.function.arguments:
                    call["arguments"] += tc.function.arguments
            if delta.content is not None:
                text += delta.content
                yield delta.content
        if not calls:
            if text.strip():
                return
            if not empty_retried:  # nothing was yielded this round: ask once more
                empty_retried = True
                continue
            yield turn_fallback(messages, start, EMPTY_ANSWER)
            return
        rounds += 1
        ordered = [calls[i] for i in sorted(calls)]
        messages.append(_tool_call_message(text, ordered))
        messages.extend(_run_calls(business_id, conversation_id, ordered))
    yield turn_fallback(messages, start, GIVE_UP_ANSWER)
