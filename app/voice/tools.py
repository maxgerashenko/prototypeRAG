"""Shared tool definitions for both voice modes (DEC-30, plan/03-voice-channel.md).

RAG is a tool here, not a fixed pre-retrieval step: the LLM decides when to search. The
same JSON-schema definitions go to the OpenAI-compatible `tools=[...]` in pipeline mode
and to Gemini Live function declarations in stage 2, so switching modes doesn't change
business logic. Part 4 action tools (bookings, ...) register in `app/actions/registry.py`;
`TOOLS` is the registry's schema list and `run_tool` goes through the executor.
"""

import uuid

from app.actions.executor import execute
from app.actions.registry import TOOLS, ActionContext, Tool, register
from app.db import tenant_session
from app.rag.location import default_location_id
from app.rag.prompt import format_chunks
from app.rag.retrieve import retrieve

# T1b (V16): 3, not 5 -- the LLM's post-search time grows with the tool result's length
# (~3.7 s measured with 5); re-run the eval set when changing it
SEARCH_TOP_N = 3


def search_business_info(business_id: uuid.UUID, query: str) -> str:
    """Hybrid retrieval (DEC-12) for one business, formatted like the chat prompt's chunks."""
    with tenant_session(business_id) as session:
        chunks = retrieve(
            session, business_id, query, SEARCH_TOP_N, location_id=default_location_id(session, business_id)
        )  # DEC-48
    return format_chunks(chunks)


register(Tool(
    name="search_business_info",
    description=(
        "Search this business's website and owner-written answers. Use it for any "
        "question not already answered by the business profile."
    ),
    parameters={"query": {"type": "string", "description": "What to look up, as a short question or keywords."}},
    required=("query",),
    handler=lambda ctx, args: (
        search_business_info(ctx.business_id, args["query"])
        if isinstance(args.get("query"), str) and args["query"].strip()
        else "Error: search_business_info needs a non-empty 'query'."
    ),
))

__all__ = ["TOOLS", "run_tool", "search_business_info"]


def run_tool(
    business_id: uuid.UUID, name: str, arguments: str | dict, conversation_id: uuid.UUID | None = None
) -> str:
    """Execute one tool call from the LLM via the executor. `business_id` and
    `conversation_id` come from the call session, never from the model's arguments.

    Returns the tool result as text; unknown tools / bad arguments return an error
    string the model can see instead of raising mid-call.
    """
    return execute(ActionContext(business_id, conversation_id), name, arguments)
