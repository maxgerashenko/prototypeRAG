"""Read-only conversation history for the voice test page (lists and transcripts per business).
Every query runs in tenant_session(business_id) AND filters by business_id (multi-tenant rule);
stage 1 has no login, like the rest of the dev pages.

`source` says where a conversation came from, for the history's icons: "twilio" when it
went through Twilio (a phone call or a Voice SDK call, both store `call_sid`), "web"
otherwise (the browser voice app or the chat page)."""

import uuid
from datetime import datetime
from typing import Literal
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select
from app.db import tenant_session
from app.db.models import Conversation, Message, message_order

router = APIRouter()

Source = Literal["twilio", "web"]

class ConversationSummary(BaseModel):
    id: uuid.UUID
    channel: str
    source: Source
    title: str
    preview: str
    started_at: datetime
    duration_s: int
    message_count: int

class MessageOut(BaseModel):
    role: str
    content: str
    at_s: int

class ConversationDetail(BaseModel):
    id: uuid.UUID
    channel: str
    source: Source
    title: str
    started_at: datetime
    duration_s: int
    messages: list[MessageOut]

def make_title(messages: list[Message]) -> str:
    for m in messages:
        if m.role == "user":
            t = m.content.strip()
            if t and t[-1] in ".?!":
                t = t[:-1]
            if len(t) > 60:
                t = t[:59] + "…"
            return t
    return "Voice call"

def make_preview(messages: list[Message]) -> str:
    if not messages:
        return ""
    last = messages[-1]
    content = last.content
    if last.role == "user":
        content = "You: " + content
    if len(content) > 120:
        content = content[:119] + "…"
    return content

def source_of(conversation: Conversation) -> Source:
    return "twilio" if conversation.call_sid else "web"

def duration_s(conversation: Conversation, messages: list[Message]) -> int:
    if conversation.ended_at is not None:
        end = conversation.ended_at
    elif messages:
        end = messages[-1].created_at
    else:
        end = conversation.started_at
    delta = end - conversation.started_at
    return max(0, int(delta.total_seconds()))

@router.get("/businesses/{business_id}/conversations")
def list_conversations(business_id: uuid.UUID, limit: int = Query(50, ge=1, le=200)) -> list[ConversationSummary]:
    with tenant_session(business_id) as session:
        convs_stmt = select(Conversation).where(Conversation.business_id == business_id).order_by(Conversation.started_at.desc())
        convs = session.execute(convs_stmt).scalars().all()
        ids = [c.id for c in convs]
        if not ids:
            return []
        msgs_stmt = select(Message).where(
            Message.business_id == business_id,
            Message.conversation_id.in_(ids),
            Message.role.in_(["user", "assistant"])
        ).order_by(*message_order())
        msgs = session.execute(msgs_stmt).scalars().all()
        msg_map = {}
        for m in msgs:
            msg_map.setdefault(m.conversation_id, []).append(m)
        results = []
        for c in convs:
            c_msgs = msg_map.get(c.id, [])
            if not any(m.role == "user" for m in c_msgs):
                continue
            results.append(ConversationSummary(
                id=c.id,
                channel=c.channel,
                source=source_of(c),
                title=make_title(c_msgs),
                preview=make_preview(c_msgs),
                started_at=c.started_at,
                duration_s=duration_s(c, c_msgs),
                message_count=len(c_msgs)
            ))
            if len(results) >= limit:
                break
        return results

@router.get("/businesses/{business_id}/conversations/{conversation_id}")
def get_conversation_detail(business_id: uuid.UUID, conversation_id: uuid.UUID) -> ConversationDetail:
    with tenant_session(business_id) as session:
        conv_stmt = select(Conversation).where(Conversation.id == conversation_id, Conversation.business_id == business_id)
        conv = session.execute(conv_stmt).scalars().first()
        if conv is None:
            raise HTTPException(404, "conversation not found")
        msgs_stmt = select(Message).where(
            Message.conversation_id == conversation_id,
            Message.business_id == business_id,
            Message.role.in_(["user", "assistant"])
        ).order_by(*message_order())
        msgs = session.execute(msgs_stmt).scalars().all()
        message_outs = [
            MessageOut(
                role=m.role,
                content=m.content,
                at_s=max(0, int((m.created_at - conv.started_at).total_seconds()))
            )
            for m in msgs
        ]
        return ConversationDetail(
            id=conv.id,
            channel=conv.channel,
            source=source_of(conv),
            title=make_title(msgs),
            started_at=conv.started_at,
            duration_s=duration_s(conv, msgs),
            messages=message_outs
        )
