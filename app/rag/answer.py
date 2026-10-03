"""Full answer pipeline: retrieve → build prompt → generate (plan/02-local-rag.md)."""

import uuid
from dataclasses import dataclass

from app.config import get_settings
from app.db import tenant_session
from app.db.models import BusinessProfile
from app.llm import chat, get_chat_client
from app.rag.prompt import build_prompt
from app.rag.retrieve import RetrievedChunk, retrieve


@dataclass(frozen=True)
class AnswerResult:
    answer: str
    chunks: list[RetrievedChunk]  # what was retrieved, for debug/display
    profile: BusinessProfile | None


def answer_question(
    business_id: uuid.UUID, question: str, history: list[dict] | None = None, top_n: int = 5
) -> AnswerResult:
    """Non-streaming: retrieve, build the prompt, call the LLM once, return the full answer."""
    with tenant_session(business_id) as session:
        profile = session.get(BusinessProfile, business_id)
        chunks = retrieve(session, business_id, question, top_n)
        messages = build_prompt(profile, chunks, history or [], question)
    answer = chat(messages)
    return AnswerResult(answer=answer, chunks=chunks, profile=profile)


def stream_answer(business_id: uuid.UUID, question: str, history: list[dict] | None = None, top_n: int = 5):
    """Streaming: same retrieval/prompt step, then yields answer text piece by piece.

    The DB transaction closes before generation starts — it isn't held open for the
    whole duration of LLM streaming.
    """
    with tenant_session(business_id) as session:
        profile = session.get(BusinessProfile, business_id)
        chunks = retrieve(session, business_id, question, top_n)
        messages = build_prompt(profile, chunks, history or [], question)

    stream = get_chat_client().chat.completions.create(
        model=get_settings().llm_model, messages=messages, stream=True
    )
    for piece in stream:
        content = piece.choices[0].delta.content
        if content is not None:
            yield content
