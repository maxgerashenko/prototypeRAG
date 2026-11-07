"""POST /chat, GET /chat/stream (SSE), GET /debug/retrieve, GET /debug/prompt (plan/02-local-rag.md).

/debug/* (`debug_router`) is mounted only when DEBUG_ENDPOINTS=true, like DEV_RELOAD."""

import logging
import re
import uuid
from collections.abc import Generator

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.api.deps import UPSTREAM_ERRORS, require_business, upstream_unavailable
from app.config import get_settings
from app.db import tenant_session
from app.db.models import Conversation, Message, message_order
from app.rag.answer import answer_question, prepare_prompt, stream_answer
from app.rag.location import default_location_id
from app.rag.retrieve import retrieve

log = logging.getLogger(__name__)
_LINE_END = re.compile(r"\r\n|\r|\n")  # the three SSE line terminators

router = APIRouter()
# /debug/* lives on its own router so main.py mounts it only when DEBUG_ENDPOINTS is on.
debug_router = APIRouter()

MAX_QUESTION_LEN = 2000  # characters; a caller's question, not a document (review L1)
MAX_TOP_N = 20  # the debug endpoints' retrieval size cap (review M1)
ERROR_TEXT = "Sorry, I can't answer right now. Please try again in a moment."


class ChatRequest(BaseModel):
    business_id: uuid.UUID
    question: str = Field(min_length=1, max_length=MAX_QUESTION_LEN)
    conversation_id: uuid.UUID | None = None  # None -> start a new conversation


class ChatResponse(BaseModel):
    answer: str
    conversation_id: uuid.UUID
    sources: list[dict]
    # The exact messages sent to the LLM (system + history + question). Only with DEBUG_ENDPOINTS=true:
    # it holds the system prompt, business profile and retrieved chunks (review M1).
    prompt: list[dict] | None = None


def _prepare_conversation_and_history(
    business_id: uuid.UUID, conversation_id: uuid.UUID | None
) -> tuple[uuid.UUID, list[dict]]:
    """Load an existing conversation's prior user/assistant turns, or pick the id of a new one.

    A new conversation is only *named* here (a fresh uuid) -- its row is created by
    `_save_turn` together with the first turn, so an LLM/embedder failure leaves no empty
    conversation behind (review M2). An existing conversation_id must belong to this
    business -> 404 otherwise, checked *before* the LLM call (the composite FK on messages
    would reject it anyway, but only at save time, as a 500, after the answer was generated).
    """
    if conversation_id is None:
        return uuid.uuid4(), []

    with tenant_session(business_id) as session:
        exists = session.scalar(
            select(Conversation.id).where(Conversation.business_id == business_id, Conversation.id == conversation_id)
        )
        if exists is None:
            raise HTTPException(status_code=404, detail="conversation not found")
        rows = session.scalars(
            select(Message)
            .where(Message.business_id == business_id, Message.conversation_id == conversation_id)
            .order_by(*message_order())
        )
        history = [{"role": m.role, "content": m.content} for m in rows if m.role in ("user", "assistant")]
    return conversation_id, history


@router.post("/chat")
def chat_endpoint(req: ChatRequest) -> ChatResponse:
    """Plain `def`, not `async def`: nothing here is actually async (sync SQLAlchemy,
    sync openai client) -- FastAPI runs sync handlers in a threadpool, which is what
    blocking I/O needs; `async def` with no `await` would just block the event loop.
    """
    require_business(req.business_id)
    conversation_id, history = _prepare_conversation_and_history(req.business_id, req.conversation_id)
    with upstream_unavailable("language model or embedding server"):
        result = answer_question(
            req.business_id, req.question, history=history, top_n=5, conversation_id=conversation_id
        )
    _save_turn(req.business_id, conversation_id, req.question, result.answer)

    return ChatResponse(
        answer=result.answer,
        conversation_id=conversation_id,
        sources=[
            {"chunk_id": str(c.chunk_id), "section_heading": c.section_heading, "score": c.score, "source": c.source}
            for c in result.chunks
        ],
        prompt=result.messages if get_settings().debug_endpoints else None,
    )


@router.get("/chat/stream", dependencies=[Depends(require_business)])
def chat_stream_endpoint(
    business_id: uuid.UUID,
    question: str = Query(min_length=1, max_length=MAX_QUESTION_LEN),
    conversation_id: uuid.UUID | None = None,
) -> StreamingResponse:
    """SSE stream of answer text. Conversation/history is prepared synchronously first;
    the turn is saved once the stream completes (a client that disconnects mid-answer
    leaves no half-saved turn). If retrieval or the LLM fails after the headers went out,
    the stream ends with an `error` event and `[DONE]`, and nothing is saved.

    The question travels in the query string, so it lands in access logs; prefer POST /chat
    for anything sensitive (review L1).
    """
    conversation_id, history = _prepare_conversation_and_history(business_id, conversation_id)

    def event_generator() -> Generator[str, None, None]:
        yield _sse(str(conversation_id), event="conversation_id")  # so the client can continue the thread
        pieces: list[str] = []
        try:
            for piece in stream_answer(
                business_id, question, history=history, top_n=5, conversation_id=conversation_id
            ):
                pieces.append(piece)
                yield _sse(piece)
            _save_turn(business_id, conversation_id, question, "".join(pieces))
        except UPSTREAM_ERRORS:
            log.warning("chat stream failed for business %s", business_id, exc_info=True)
            yield _sse(ERROR_TEXT, event="error")
        except Exception:
            log.exception("chat stream failed for business %s", business_id)
            yield _sse(ERROR_TEXT, event="error")
        yield _sse("[DONE]")

    return StreamingResponse(event_generator(), media_type="text/event-stream")


def _sse(data: str, event: str | None = None) -> str:
    """One SSE frame. A line break inside `data` must become a new `data:` line -- written
    raw, it would end the field and the client would silently drop the rest (answers
    with lists or paragraphs). SSE treats LF, CR and CRLF all as line ends, so split on
    every one of them (a lone CR used to cut the text off). The client joins `data:`
    lines back with newlines."""
    head = f"event: {event}\n" if event else ""
    return head + "".join(f"data: {line}\n" for line in _LINE_END.split(data)) + "\n"


def _save_turn(business_id: uuid.UUID, conversation_id: uuid.UUID, question: str, answer: str) -> None:
    """Save the turn, creating the conversation first if this is its first turn."""
    with tenant_session(business_id) as session:
        if session.get(Conversation, conversation_id) is None:
            session.add(Conversation(id=conversation_id, business_id=business_id, channel="chat"))
            session.flush()
        session.add(Message(business_id=business_id, conversation_id=conversation_id, role="user", content=question))
        session.add(Message(business_id=business_id, conversation_id=conversation_id, role="assistant", content=answer))


@debug_router.get("/debug/retrieve", dependencies=[Depends(require_business)])
def debug_retrieve_endpoint(
    business_id: uuid.UUID,
    q: str = Query(min_length=1, max_length=MAX_QUESTION_LEN),
    top_n: int = Query(5, ge=1, le=MAX_TOP_N),
) -> dict:
    """Inspect retrieval quality independently of generation (manual testing, plan's own note)."""
    with tenant_session(business_id) as session:
        chunks = retrieve(session, business_id, q, top_n, location_id=default_location_id(session, business_id))
    return {
        "chunks": [
            {"chunk_id": str(c.chunk_id), "section_heading": c.section_heading, "text": c.text,
             "kind": c.kind, "score": c.score, "source": c.source}
            for c in chunks
        ]
    }


@debug_router.get("/debug/prompt", dependencies=[Depends(require_business)])
def debug_prompt_endpoint(
    business_id: uuid.UUID,
    q: str = Query(min_length=1, max_length=MAX_QUESTION_LEN),
    conversation_id: uuid.UUID | None = None,
    top_n: int = Query(5, ge=1, le=MAX_TOP_N),
) -> dict:
    """The full prompt /chat would send for this question, without calling the LLM.

    With a conversation_id its stored turns are included as history; without one, no
    conversation is created (read-only, unlike /chat).
    """
    history: list[dict] = []
    if conversation_id is not None:
        _, history = _prepare_conversation_and_history(business_id, conversation_id)
    _, _, messages = prepare_prompt(business_id, q, history, top_n)
    return {"messages": messages}
