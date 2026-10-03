"""POST /chat, GET /chat/stream (SSE), GET /debug/retrieve (plan/02-local-rag.md)."""

import uuid
from collections.abc import Generator

from fastapi import APIRouter, HTTPException
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

    An existing conversation_id must belong to this business -> 404 otherwise, checked
    *before* the LLM call (the composite FK on messages would reject it anyway, but
    only at save time, as a 500, after the answer was already generated).
    """
    if conversation_id is None:
        with tenant_session(business_id) as session:
            conversation = Conversation(business_id=business_id, channel="chat")
            session.add(conversation)
            session.flush()
            conversation_id = conversation.id

    with tenant_session(business_id) as session:
        exists = session.scalar(
            select(Conversation.id).where(Conversation.business_id == business_id, Conversation.id == conversation_id)
        )
        if exists is None:
            raise HTTPException(status_code=404, detail="conversation not found")
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
    _save_turn(req.business_id, conversation_id, req.question, result.answer)

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
    """SSE stream of answer text. Conversation/history is prepared synchronously first;
    the turn is saved once the stream completes (a client that disconnects mid-answer
    leaves no half-saved turn)."""
    conversation_id, history = _prepare_conversation_and_history(business_id, conversation_id)

    def event_generator() -> Generator[str, None, None]:
        yield _sse(str(conversation_id), event="conversation_id")  # so the client can continue the thread
        pieces: list[str] = []
        for piece in stream_answer(business_id, question, history=history, top_n=5):
            pieces.append(piece)
            yield _sse(piece)
        _save_turn(business_id, conversation_id, question, "".join(pieces))
        yield _sse("[DONE]")

    return StreamingResponse(event_generator(), media_type="text/event-stream")


def _sse(data: str, event: str | None = None) -> str:
    """One SSE frame. A newline inside `data` must become a new `data:` line -- written
    raw, it would end the field and the client would silently drop the rest (answers
    with lists or paragraphs). The client joins `data:` lines back with newlines."""
    head = f"event: {event}\n" if event else ""
    return head + "".join(f"data: {line}\n" for line in data.split("\n")) + "\n"


def _save_turn(business_id: uuid.UUID, conversation_id: uuid.UUID, question: str, answer: str) -> None:
    with tenant_session(business_id) as session:
        session.add(Message(business_id=business_id, conversation_id=conversation_id, role="user", content=question))
        session.add(Message(business_id=business_id, conversation_id=conversation_id, role="assistant", content=answer))


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
