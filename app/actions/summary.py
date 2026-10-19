"""Post-conversation summary (plan/04-actions.md → Conversation summaries, DEC-24).

`summarize_conversation()` is called from `POST /twilio/status` once a call is over: it
runs inside that request, not as a background task, because Cloud Run throttles CPU once
the response is sent (R19). The result is stored on the `conversations` row.

Idempotent: Twilio may deliver the final status more than once, so a conversation with
`summarized_at` set is left alone. A conversation with no caller turns has nothing to
summarize and makes no LLM call. Any failure (model down, bad JSON) is logged and swallowed:
the status callback must still answer 204 so Twilio doesn't retry, and the transcript stays.
"""

import logging
import uuid
from datetime import datetime

from sqlalchemy import select

from app.db import tenant_session
from app.db.models import Conversation, Message, message_order
from app.llm import chat_json

log = logging.getLogger(__name__)

OUTCOMES = ("info_request", "booked", "message", "transferred", "unresolved")

SUMMARY_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "intent": {"type": "string"},
        "outcome": {"type": "string", "enum": list(OUTCOMES)},
        "follow_up": {"type": "boolean"},
    },
    "required": ["summary", "intent", "outcome", "follow_up"],
    "additionalProperties": False,
}

_SYSTEM_PROMPT = (
    "You summarize one finished phone call between a caller and a business's voice "
    "assistant, for the business owner, who skims many of these.\n"
    "- outcome: exactly one of\n"
    "  booked: a booking or appointment was made;\n"
    "  info_request: the caller wanted information (hours, prices, services, location) and got it;\n"
    "  message: the caller left a message for the business;\n"
    "  transferred: handed to a person;\n"
    "  unresolved: the assistant could not answer or do what the caller wanted.\n"
    "- summary: ONE line, at most 25 words, packed with the facts the owner needs: what the "
    "caller wanted plus every concrete detail from the call (service, date and time, party "
    "size, name, callback number, the question left unanswered). No filler such as 'the caller "
    "called to', no greetings, no opinions. Example: 'Booked massage Sat 14:00, 2 people, name "
    "Anna, +1 555 0100.' or 'Asked if dogs are allowed; not in my info.'\n"
    "- intent: what the caller wanted, 2-5 words, lowercase (e.g. 'book massage', 'opening hours').\n"
    "- follow_up: true if the owner should do something afterwards (call back, answer a "
    "question the assistant could not); always true for message and unresolved.\n"
    "Use only what the transcript says; do not invent details."
)

TRANSCRIPT_CHARS = 20000  # same order as other single-shot prompts; the tail matters most


def transcript_text(messages: list[tuple[str, str]]) -> str:
    """Caller/assistant turns as plain text; tool and system rows are not conversation."""
    names = {"user": "Caller", "assistant": "Assistant"}
    text = "\n".join(f"{names[role]}: {content}" for role, content in messages if role in names and content)
    return text[-TRANSCRIPT_CHARS:]


def summarize_conversation(business_id: uuid.UUID, conversation_id: uuid.UUID) -> bool:
    """Generate and store the summary. True if one was stored."""
    try:
        with tenant_session(business_id) as session:
            conversation = session.get(Conversation, conversation_id)
            if conversation is None or conversation.summarized_at is not None:
                return False
            rows = session.execute(
                select(Message.role, Message.content)
                .where(Message.conversation_id == conversation_id)
                .order_by(*message_order())
            ).all()
        if not any(role == "user" and content.strip() for role, content in rows):
            return False
        result = chat_json(
            [{"role": "system", "content": _SYSTEM_PROMPT},
             {"role": "user", "content": transcript_text([(r, c) for r, c in rows])}],
            SUMMARY_SCHEMA, "call_summary",
        )
        outcome = result.get("outcome")
        with tenant_session(business_id) as session:
            conversation = session.get(Conversation, conversation_id)
            if conversation is None or conversation.summarized_at is not None:
                return False
            conversation.summary = str(result.get("summary", "")).strip() or None
            conversation.intent = str(result.get("intent", "")).strip() or None
            conversation.outcome = outcome if outcome in OUTCOMES else "unresolved"
            conversation.follow_up = bool(result.get("follow_up"))
            conversation.summarized_at = datetime.now().astimezone()
        return True
    except Exception:
        log.exception("summary of conversation %s failed", conversation_id)
        return False
