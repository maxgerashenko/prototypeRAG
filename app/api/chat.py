"""POST /chat, GET /chat/stream (SSE), GET /debug/retrieve (plan/02-local-rag.md)."""

import uuid
from collections.abc import Generator

from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import select

from app.db import tenant_session
from app.db.models import Conversation, Message
from app.rag.answer import answer_question, stream_answer
from app.rag.retrieve import retrieve

router = APIRouter()


class ChatRequest(BaseModel):
    business_id: uuid.UUID
    question: str
    conversation_id: uuid.UUID | None = None  # None -> start a new conversation


class ChatResponse(BaseModel):
    answer: str
    conversation_id: uuid.UUID
    sources: list[dict]


def _prepare_conversation_and_history(
    business_id: uuid.UUID, conversation_id: uuid.UUID | None
) -> tuple[uuid.UUID, list[dict]]:
    """Create a conversation if needed, then load its prior user/assistant turns.

    Note: doesn't yet validate an existing conversation_id belongs to this business —
    acceptable for stage 1 (deferred hardening), not for a later stage with real
    callers.
    """
    if conversation_id is None:
        with tenant_session(business_id) as session:
            conversation = Conversation(business_id=business_id, channel="chat")
            session.add(conversation)
            session.flush()
            conversation_id = conversation.id

    with tenant_session(business_id) as session:
        rows = session.scalars(
            select(Message)
            .where(Message.business_id == business_id, Message.conversation_id == conversation_id)
            .order_by(Message.created_at)
        )
        history = [{"role": m.role, "content": m.content} for m in rows if m.role in ("user", "assistant")]
    return conversation_id, history


@router.post("/chat")
def chat_endpoint(req: ChatRequest) -> ChatResponse:
    """Plain `def`, not `async def`: nothing here is actually async (sync SQLAlchemy,
    sync openai client) -- FastAPI runs sync handlers in a threadpool, which is what
    blocking I/O needs; `async def` with no `await` would just block the event loop.
    """
    conversation_id, history = _prepare_conversation_and_history(req.business_id, req.conversation_id)
    result = answer_question(req.business_id, req.question, history=history, top_n=5)

    with tenant_session(req.business_id) as session:
        session.add(Message(
            business_id=req.business_id, conversation_id=conversation_id, role="user", content=req.question,
        ))
        session.add(Message(
            business_id=req.business_id, conversation_id=conversation_id, role="assistant", content=result.answer,
        ))

    return ChatResponse(
        answer=result.answer,
        conversation_id=conversation_id,
        sources=[
            {"chunk_id": str(c.chunk_id), "section_heading": c.section_heading, "score": c.score, "source": c.source}
            for c in result.chunks
        ],
    )


@router.get("/chat/stream")
def chat_stream_endpoint(business_id: uuid.UUID, question: str, conversation_id: uuid.UUID | None = None) -> StreamingResponse:
    """SSE stream of answer text. Conversation/history is prepared synchronously first."""
    conversation_id, history = _prepare_conversation_and_history(business_id, conversation_id)

    def event_generator() -> Generator[str, None, None]:
        for piece in stream_answer(business_id, question, history=history, top_n=5):
            yield f"data: {piece}\n\n"
        yield "data: [DONE]\n\n"
        # TODO: save user/assistant Message rows once streaming is done. Needs the
        # full answer text accumulated first (not available until the client already
        # has it piece by piece) -- not implemented yet, follow-up for this part.

    return StreamingResponse(event_generator(), media_type="text/event-stream")


@router.get("/debug/retrieve")
def debug_retrieve_endpoint(business_id: uuid.UUID, q: str, top_n: int = 5) -> dict:
    """Inspect retrieval quality independently of generation (manual testing, plan's own note)."""
    with tenant_session(business_id) as session:
        chunks = retrieve(session, business_id, q, top_n)
    return {
        "chunks": [
            {"chunk_id": str(c.chunk_id), "section_heading": c.section_heading, "text": c.text,
             "kind": c.kind, "score": c.score, "source": c.source}
            for c in chunks
        ]
    }
