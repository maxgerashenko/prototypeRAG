"""Shared tool definitions for both voice modes (DEC-30, plan/03-voice-channel.md).

RAG is a tool here, not a fixed pre-retrieval step: the LLM decides when to search. The
same JSON-schema definitions go to the OpenAI-compatible `tools=[...]` in pipeline mode
and to Gemini Live function declarations in stage 2, so switching modes doesn't change
business logic. Part 4 action tools (bookings, ...) are appended to `TOOLS` later.
"""

import json
import uuid

from app.db import tenant_session
from app.rag.prompt import format_chunks
from app.rag.retrieve import retrieve

# T1b (V16): 3, not 5 -- the LLM's post-search time grows with the tool result's length
# (~3.7 s measured with 5); re-run the eval set when changing it
SEARCH_TOP_N = 3

TOOLS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "search_business_info",
            "description": (
                "Search this business's website and owner-written answers. Use it for any "
                "question not already answered by the business profile."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "What to look up, as a short question or keywords."},
                },
                "required": ["query"],
            },
        },
    },
]


def search_business_info(business_id: uuid.UUID, query: str) -> str:
    """Hybrid retrieval (DEC-12) for one business, formatted like the chat prompt's chunks."""
    with tenant_session(business_id) as session:
        chunks = retrieve(session, business_id, query, SEARCH_TOP_N)
    return format_chunks(chunks)


def run_tool(business_id: uuid.UUID, name: str, arguments: str | dict) -> str:
    """Execute one tool call from the LLM. `business_id` comes from the call session,
    never from the model's arguments — the model can't reach another business's data.

    Returns the tool result as text; unknown tools / bad arguments return an error
    string the model can see instead of raising mid-call.
    """
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments or "{}")
        except json.JSONDecodeError:
            return f"Error: arguments for {name} are not valid JSON."
    if name == "search_business_info":
        query = arguments.get("query")
        if not isinstance(query, str) or not query.strip():
            return "Error: search_business_info needs a non-empty 'query'."
        return search_business_info(business_id, query)
    return f"Error: unknown tool {name!r}."
