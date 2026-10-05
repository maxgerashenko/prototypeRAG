"""Read-only endpoints behind the voice app at /web/ (DEC-38).

GET /businesses                                   business picker (name, category, chat count)
GET /businesses/{business_id}/conversations       past calls/chats, newest first, with preview
GET /businesses/{business_id}/conversations/{id}  one transcript

Only user/assistant messages are shown: tool and system rows are internal.

`/businesses` lists every business: stages 1–2 have one operator and no login. Stage 3
adds login (OPEN-12) and must scope it to the signed-in owner's businesses.
"""

import uuid
from collections import defaultdict
from datetime import datetime
from urllib.parse import urlparse

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, select

from app.db import tenant_session
from app.db.models import Business, BusinessProfile, Conversation, Message, message_order
from app.db.session import SessionLocal

router = APIRouter()

VISIBLE_ROLES = ("user", "assistant")
TITLE_MAX = 80


class BusinessOut(BaseModel):
    id: uuid.UUID
    name: str
    category: str
    conversation_count: int


class MessageOut(BaseModel):
    id: uuid.UUID
    role: str
    content: str
    created_at: datetime


class ConversationOut(BaseModel):
    id: uuid.UUID
    channel: str
    started_at: datetime
    ended_at: datetime | None
    title: str
    preview: str
    preview_role: str | None
    message_count: int


class ConversationDetail(ConversationOut):
    messages: list[MessageOut]


def _category(business: Business) -> str:
    """No category column yet: an explicit `settings.category`, else the website's host."""
    category = (business.settings or {}).get("category")
    if category:
        return str(category)
    host = urlparse(business.website or "").hostname or ""
    return host.removeprefix("www.") or "Business"


def _title(conversation: Conversation, messages: list[Message]) -> str:
    first_question = next((m.content.strip() for m in messages if m.role == "user" and m.content.strip()), "")
    if not first_question:
        return "Voice call" if conversation.channel == "voice" else "Chat"
    title = first_question.rstrip(".?!")
    return title if len(title) <= TITLE_MAX else title[: TITLE_MAX - 1].rstrip() + "…"


def _summary(conversation: Conversation, messages: list[Message]) -> dict:
    last = messages[-1] if messages else None
    return {
        "id": conversation.id,
        "channel": conversation.channel,
        "started_at": conversation.started_at,
        "ended_at": conversation.ended_at,
        "title": _title(conversation, messages),
        "preview": last.content if last else "",
        "preview_role": last.role if last else None,
        "message_count": len(messages),
    }


def _visible_messages(session, business_id: uuid.UUID, conversation_ids: list[uuid.UUID]) -> dict:
    by_conversation: dict[uuid.UUID, list[Message]] = defaultdict(list)
    if not conversation_ids:
        return by_conversation
    rows = session.scalars(
        select(Message)
        .where(
            Message.business_id == business_id,
            Message.conversation_id.in_(conversation_ids),
            Message.role.in_(VISIBLE_ROLES),
        )
        .order_by(*message_order())
    )
    for m in rows:
        by_conversation[m.conversation_id].append(m)
    return by_conversation


@router.get("/businesses", response_model=list[BusinessOut])
def list_businesses() -> list[dict]:
    counts = (
        select(Conversation.business_id, func.count().label("n"))
        .group_by(Conversation.business_id)
        .subquery()
    )
    with SessionLocal() as session:
        rows = session.execute(
            select(Business, BusinessProfile.name, func.coalesce(counts.c.n, 0))
            .outerjoin(BusinessProfile, BusinessProfile.business_id == Business.id)
            .outerjoin(counts, counts.c.business_id == Business.id)
        ).all()
    out = [
        {"id": b.id, "name": profile_name or b.name, "category": _category(b), "conversation_count": n}
        for b, profile_name, n in rows
    ]
    return sorted(out, key=lambda b: b["name"].casefold())


@router.get("/businesses/{business_id}/conversations", response_model=list[ConversationOut])
def list_conversations(business_id: uuid.UUID, limit: int = 50) -> list[dict]:
    with tenant_session(business_id) as session:
        conversations = list(
            session.scalars(
                select(Conversation)
                .where(Conversation.business_id == business_id)
                .order_by(Conversation.started_at.desc(), Conversation.id)
                .limit(max(1, min(limit, 200)))
            )
        )
        messages = _visible_messages(session, business_id, [c.id for c in conversations])
        return [_summary(c, messages[c.id]) for c in conversations]


@router.get("/businesses/{business_id}/conversations/{conversation_id}", response_model=ConversationDetail)
def get_conversation(business_id: uuid.UUID, conversation_id: uuid.UUID) -> dict:
    with tenant_session(business_id) as session:
        conversation = session.scalar(
            select(Conversation).where(Conversation.business_id == business_id, Conversation.id == conversation_id)
        )
        if conversation is None:
            raise HTTPException(status_code=404, detail="conversation not found")
        messages = _visible_messages(session, business_id, [conversation.id])[conversation.id]
        return {**_summary(conversation, messages), "messages": messages}
