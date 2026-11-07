"""Custom replies CRUD + indexing (plan/02-local-rag.md). An owner override gets
exactly one chunk (kind='custom_reply') -- not header-split like a scraped page,
since it's a short Q&A, not a long document.
"""

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import delete, select

from app.api.deps import require_business, upstream_unavailable
from app.config import get_settings
from app.db import tenant_session
from app.db.models import Chunk, CustomReply
from app.ingest.clean import content_hash
from app.llm import embed

router = APIRouter()


# Both texts are embedded and pasted into prompts: bound them (review L1).
MAX_QUESTION_LEN = 500
MAX_ANSWER_LEN = 4000


class CustomReplyCreate(BaseModel):
    question: str = Field(min_length=1, max_length=MAX_QUESTION_LEN)
    answer: str = Field(min_length=1, max_length=MAX_ANSWER_LEN)


class CustomReplyUpdate(BaseModel):
    question: str = Field(min_length=1, max_length=MAX_QUESTION_LEN)
    answer: str = Field(min_length=1, max_length=MAX_ANSWER_LEN)


class CustomReplyOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)  # lets model_validate() read a SQLAlchemy ORM object

    id: uuid.UUID
    question: str
    answer: str
    created_at: datetime
    updated_at: datetime


def _reindex_custom_reply(session, business_id: uuid.UUID, reply: CustomReply) -> None:
    """Replace this reply's one chunk (there should be at most one, deleted by filter not assumption)."""
    session.execute(delete(Chunk).where(Chunk.business_id == business_id, Chunk.custom_reply_id == reply.id))

    text = f"Q: {reply.question}\nA: {reply.answer}"
    embedding = embed([f"search_document: {text}"])[0]
    session.add(
        Chunk(
            business_id=business_id,
            custom_reply_id=reply.id,
            page_id=None,
            kind="custom_reply",
            chunk_index=0,
            section_heading=None,
            text=text,
            embedding=embedding,
            embed_model=get_settings().embed_model,
            content_hash=content_hash(text),
        )
    )
    session.flush()


@router.post("/businesses/{business_id}/custom-replies", response_model=CustomReplyOut,
             dependencies=[Depends(require_business)])
def create_custom_reply(business_id: uuid.UUID, payload: CustomReplyCreate) -> CustomReply:
    """Plain `def`: this blocks on a sync DB session + a sync embed() call, same
    reasoning as app/api/chat.py -- no real `await` here, so `async def` would only
    block FastAPI's event loop for no benefit.
    """
    with upstream_unavailable("embedding server"), tenant_session(business_id) as session:
        reply = CustomReply(business_id=business_id, question=payload.question, answer=payload.answer)
        session.add(reply)
        session.flush()
        _reindex_custom_reply(session, business_id, reply)
        session.refresh(reply)
        return reply


@router.get("/businesses/{business_id}/custom-replies", response_model=list[CustomReplyOut])
def list_custom_replies(business_id: uuid.UUID) -> list[CustomReply]:
    with tenant_session(business_id) as session:
        return list(
            session.scalars(select(CustomReply).where(CustomReply.business_id == business_id).order_by(CustomReply.created_at))
        )


def _get_or_404(session, business_id: uuid.UUID, reply_id: uuid.UUID) -> CustomReply:
    reply = session.scalar(
        select(CustomReply).where(CustomReply.business_id == business_id, CustomReply.id == reply_id)
    )
    if reply is None:
        raise HTTPException(status_code=404, detail="custom reply not found")
    return reply


@router.put("/businesses/{business_id}/custom-replies/{reply_id}", response_model=CustomReplyOut)
def update_custom_reply(business_id: uuid.UUID, reply_id: uuid.UUID, payload: CustomReplyUpdate) -> CustomReply:
    with upstream_unavailable("embedding server"), tenant_session(business_id) as session:
        reply = _get_or_404(session, business_id, reply_id)
        reply.question = payload.question
        reply.answer = payload.answer
        reply.updated_at = datetime.now(UTC)
        session.flush()
        _reindex_custom_reply(session, business_id, reply)
        session.refresh(reply)
        return reply


@router.delete("/businesses/{business_id}/custom-replies/{reply_id}")
def delete_custom_reply(business_id: uuid.UUID, reply_id: uuid.UUID) -> dict:
    """Deleting the reply cascades to its chunk via the composite FK -- no manual chunk delete needed."""
    with tenant_session(business_id) as session:
        reply = _get_or_404(session, business_id, reply_id)
        session.delete(reply)
    return {"deleted": True}
