"""Full answer pipeline: retrieve → build prompt → generate (plan/02-local-rag.md)."""

import re
import uuid
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy import select

from app.config import get_settings
from app.db import tenant_session
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
    the conversation with no "Booked" tool result in this turn is replaced by a question; a claim after
    an earlier one (a recap, "your booking is confirmed") is left alone."""
    if not claims_booking(answer):
        return answer
    if any(m.get("role") == "tool" and str(m.get("content") or "").startswith("Booked") for m in messages[start:]):
        return answer
    if any(m.get("role") == "assistant" and claims_booking(str(m.get("content") or "")) for m in history or []):
        return answer
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
_TIME = re.compile(r"\b\d{1,2}:\d{2}\b|\b\d{1,2}\s?(?:am|pm)\b", re.I)
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


def now_note(session, business_id: uuid.UUID, enabled) -> str:
    """Today's date and time in the business's zone (the model has no clock) and, when sessions
    can be booked, the open ones with their codes: tool results are not kept in the history, so
    without this the model loses the codes between turns and invents them."""
    zone = ZoneInfo(session.scalar(select(Business.timezone).where(Business.id == business_id)) or "UTC")
    lines = [f"Now: {datetime.now(zone):%A %d %B %Y, %H:%M} ({zone.key})."]
    if "join_session" in enabled:
        rows = list_open(business_id)
        if rows:
            lines.append("Open sessions (for your reference: codes are for join_session only, never read them aloud; "
                         "do not recite this list unless the caller asks what is on):")
            lines += session_lines(rows)
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
    return AnswerResult(answer=answer, chunks=chunks, profile=profile, messages=prompt)


def stream_answer(
    business_id: uuid.UUID, question: str, history: list[dict] | None = None, top_n: int = 5,
    conversation_id: uuid.UUID | None = None,
):
    """Streaming: same retrieval/prompt step and tool loop; yields answer text piece by
    piece (text the model writes before a tool call is yielded too)."""
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
