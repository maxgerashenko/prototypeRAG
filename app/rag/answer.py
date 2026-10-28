"""Full answer pipeline: retrieve → build prompt → generate (plan/02-local-rag.md)."""

import uuid
from dataclasses import dataclass

from app.config import get_settings
from app.db import tenant_session
from app.db.models import BusinessProfile, BusinessSummary
from app.llm import get_chat_client
from app.rag.location import default_location_id, format_locations
from app.rag.prompt import build_prompt
from app.rag.retrieve import RetrievedChunk, retrieve
from app.voice.tools import TOOLS, run_tool

MAX_TOOL_ROUNDS = 3  # LLM → tool → LLM ... before giving up on a turn (same cap as voice)
GIVE_UP_ANSWER = "Sorry, I couldn't finish that. Can I take a message?"


@dataclass(frozen=True)
class AnswerResult:
    answer: str
    chunks: list[RetrievedChunk]  # what was retrieved, for debug/display
    profile: BusinessProfile | None
    messages: list[dict]  # the exact prompt sent to the LLM, for debug/display


def prepare_prompt(
    business_id: uuid.UUID, question: str, history: list[dict] | None = None, top_n: int = 5
) -> tuple[BusinessProfile | None, list[RetrievedChunk], list[dict]]:
    """Retrieve and build the prompt, without calling the LLM.

    Shared by answering and `/debug/prompt`, so the debug view shows exactly what the
    model gets. The DB transaction closes here — it isn't held open during generation.
    """
    with tenant_session(business_id) as session:
        profile = session.get(BusinessProfile, business_id)
        summary = session.get(BusinessSummary, business_id)
        location_id = default_location_id(session, business_id)  # DEC-49
        chunks = retrieve(session, business_id, question, top_n, location_id=location_id)
        messages = build_prompt(
            profile, chunks, history or [], question, summary,
            locations=format_locations(session, business_id, location_id),
        )
    return profile, chunks, messages


def _tool_call_message(content: str | None, calls: list[dict]) -> dict:
    return {
        "role": "assistant",
        "content": content or None,
        "tool_calls": [
            {"id": c["id"], "type": "function", "function": {"name": c["name"], "arguments": c["arguments"]}}
            for c in calls
        ],
    }


def _run_calls(business_id: uuid.UUID, conversation_id: uuid.UUID | None, calls: list[dict]) -> list[dict]:
    """Run each call through the executor; `business_id`/`conversation_id` come from the
    request, never from the model's arguments."""
    return [
        {"role": "tool", "tool_call_id": c["id"],
         "content": run_tool(business_id, c["name"], c["arguments"], conversation_id)}
        for c in calls
    ]


def answer_question(
    business_id: uuid.UUID, question: str, history: list[dict] | None = None, top_n: int = 5,
    conversation_id: uuid.UUID | None = None,
) -> AnswerResult:
    """Non-streaming: retrieve, build the prompt, then LLM → tools → LLM until the model
    answers in text (at most MAX_TOOL_ROUNDS rounds)."""
    profile, chunks, messages = prepare_prompt(business_id, question, history, top_n)
    prompt = list(messages)  # `messages` grows with tool turns; the debug view shows the prompt as sent first
    settings = get_settings()
    client = get_chat_client()
    answer = GIVE_UP_ANSWER
    for _ in range(MAX_TOOL_ROUNDS):
        response = client.chat.completions.create(
            model=settings.llm_model, messages=messages, tools=TOOLS,
            reasoning_effort=settings.chat_reasoning_effort,
        )
        message = response.choices[0].message
        if not message.tool_calls:
            answer = message.content or ""  # NOT NULL column downstream
            break
        calls = [
            {"id": tc.id, "name": tc.function.name, "arguments": tc.function.arguments}
            for tc in message.tool_calls
        ]
        messages.append(_tool_call_message(message.content, calls))
        messages.extend(_run_calls(business_id, conversation_id, calls))
    return AnswerResult(answer=answer, chunks=chunks, profile=profile, messages=prompt)


def stream_answer(
    business_id: uuid.UUID, question: str, history: list[dict] | None = None, top_n: int = 5,
    conversation_id: uuid.UUID | None = None,
):
    """Streaming: same retrieval/prompt step and tool loop; yields answer text piece by
    piece (text the model writes before a tool call is yielded too)."""
    _, _, messages = prepare_prompt(business_id, question, history, top_n)

    settings = get_settings()
    client = get_chat_client()
    for _ in range(MAX_TOOL_ROUNDS):
        stream = client.chat.completions.create(
            model=settings.llm_model, messages=messages, tools=TOOLS, stream=True,
            reasoning_effort=settings.chat_reasoning_effort,
        )
        text = ""
        calls: dict[int, dict] = {}
        for piece in stream:
            if not piece.choices:
                continue
            delta = piece.choices[0].delta
            for tc in delta.tool_calls or []:
                call = calls.setdefault(tc.index, {"id": "", "name": "", "arguments": ""})
                if tc.id:
                    call["id"] = tc.id
                if tc.function and tc.function.name:
                    call["name"] += tc.function.name
                if tc.function and tc.function.arguments:
                    call["arguments"] += tc.function.arguments
            if delta.content is not None:
                text += delta.content
                yield delta.content
        if not calls:
            return
        ordered = [calls[i] for i in sorted(calls)]
        messages.append(_tool_call_message(text, ordered))
        messages.extend(_run_calls(business_id, conversation_id, ordered))
    yield GIVE_UP_ANSWER
