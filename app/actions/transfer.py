"""`transfer_to_human` (plan/03-voice-channel.md, #50): hand a phone call to a person.

The tool only checks that a transfer is possible and records it in `action_log`; the
session then lets the bot's last sentence play and closes the media stream. Twilio then
continues with the `<Redirect>` that follows `<Connect>` in the call's TwiML
(`POST /twilio/transfer`, app/voice/twilio_routes.py), which finds the transfer here
(`transfer_requested`) and answers with `<Dial>` -- so no Twilio REST credentials are
needed and the decision survives a restart or another instance.

The number is `businesses.settings["transfer_number"]` (E.164); there is no settings UI yet.
"""

import re
import uuid

from sqlalchemy import select

from app.actions.registry import ActionContext, Tool, register
from app.db import tenant_session
from app.db.models import ActionLog, Business, Conversation

TRANSFER_STARTED = "Transfer started."
E164 = re.compile(r"^\+[1-9]\d{6,14}$")


def transfer_number(business_id: uuid.UUID) -> str | None:
    """The business's transfer number, or None if unset or not a valid E.164 number."""
    with tenant_session(business_id) as session:
        business = session.get(Business, business_id)
        number = (business.settings or {}).get("transfer_number") if business else None
    return number.strip() if isinstance(number, str) and E164.match(number.strip()) else None


def transfer_requested(business_id: uuid.UUID, call_sid: str) -> bool:
    """Did the bot start a transfer on this call? (an `ok` transfer_to_human in the action log)"""
    with tenant_session(business_id) as session:
        found = session.scalars(
            select(ActionLog.id)
            .join(Conversation, (Conversation.id == ActionLog.conversation_id)
                  & (Conversation.business_id == ActionLog.business_id))
            .where(ActionLog.business_id == business_id, Conversation.call_sid == call_sid,
                   ActionLog.tool == "transfer_to_human", ActionLog.status == "ok")
            .limit(1)
        ).first()
    return found is not None


def transfer_to_human(ctx: ActionContext, args: dict) -> str:
    if not ctx.call_sid:
        return "Error: transfers only work on phone calls. Offer to take a message instead."
    if transfer_number(ctx.business_id) is None:
        return "Error: nobody is set up to take transfers. Offer to take a message instead."
    return (
        f"{TRANSFER_STARTED} Tell the caller in one short sentence that you are connecting them "
        "now. Say nothing else and ask no question."
    )


register(Tool(
    name="transfer_to_human",
    description=(
        "Connect the caller to a person at the business. Use it when the caller asks for a "
        "human, or has a problem you can't solve. Ask first whether they want to be "
        "transferred. If it fails, offer take_message."
    ),
    parameters={"reason": {"type": "string", "description": "Why the caller needs a person, in a few words."}},
    side_effect=True,
    handler=transfer_to_human,
))
