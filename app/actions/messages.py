"""`take_message` (plan/04-actions.md): the fallback for anything the assistant can't
answer or do. Stores a row in `messages_for_owner`; notifying the owner comes later."""

from app.actions.registry import ActionContext, Tool, register
from app.db import tenant_session
from app.db.models import MessageForOwner

MAX_TEXT = 1000


def take_message(ctx: ActionContext, args: dict) -> str:
    text = args.get("message")
    if not isinstance(text, str) or not text.strip():
        return "Error: take_message needs a non-empty 'message'."
    name, phone = args.get("name"), args.get("phone")
    # caller ID is the default; a number the caller dictates only replaces it on request
    phone = phone.strip() if isinstance(phone, str) and phone.strip() else ctx.caller
    with tenant_session(ctx.business_id) as session:
        session.add(MessageForOwner(
            business_id=ctx.business_id, conversation_id=ctx.conversation_id,
            caller_name=name.strip() if isinstance(name, str) and name.strip() else None,
            caller_phone=phone, text=text.strip()[:MAX_TEXT],
            urgency="urgent" if args.get("urgency") == "urgent" else "normal",
        ))
    return "Message saved. Tell the caller the business will get it and call back."


register(Tool(
    name="take_message",
    description=(
        "Leave a message for the business owner. Use it when you can't answer a question, "
        "the caller wants someone to call back, or a transfer isn't possible. Ask for the "
        "caller's name and what the message is; read it back and get a yes before saving."
    ),
    parameters={
        "message": {"type": "string", "description": "What the caller wants the business to know or do."},
        "name": {"type": "string", "description": "The caller's name."},
        "phone": {
            "type": "string",
            "description": "Callback number, only if the caller gives a different one than they are calling from.",
        },
        "urgency": {"type": "string", "enum": ["normal", "urgent"], "description": "urgent only if the caller says so."},
    },
    required=("message",),
    side_effect=True,
    handler=take_message,
))
