"""Tool registry (plan/04-actions.md → Code layout).

One place that knows every tool the LLM may call: its JSON schema (the same one goes to
the OpenAI-compatible `tools=[...]` in pipeline mode and to Gemini Live declarations in
stage 2, DEC-30) and the Python handler behind it. The executor (`executor.py`) is the
only thing that calls handlers.

A tool with `side_effect=True` (booking, SMS, message to the owner, ...) gets a required
`confirmed` boolean added to its schema; the executor refuses to run it until the model
passes `confirmed=true`, i.e. after reading the details back and hearing a "yes".
"""

import uuid
from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True)
class ActionContext:
    """What a handler gets besides the model's arguments. All values come from the call
    session, never from the model, so it can't reach another business's data."""

    business_id: uuid.UUID
    conversation_id: uuid.UUID | None = None
    call_sid: str | None = None  # Twilio phone calls only; None in chat and browser calls
    caller: str | None = None  # caller ID, not what the model heard


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    parameters: dict  # JSON-schema "properties" of the arguments (without `confirmed`)
    handler: Callable[[ActionContext, dict], str]
    required: tuple[str, ...] = ()
    side_effect: bool = False

    def schema(self) -> dict:
        properties = dict(self.parameters)
        required = list(self.required)
        if self.side_effect:
            properties["confirmed"] = {
                "type": "boolean",
                "description": (
                    "true only after you read all details back to the caller and they "
                    "said yes; otherwise leave false."
                ),
            }
            required.append("confirmed")
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": {"type": "object", "properties": properties, "required": required},
            },
        }


_REGISTRY: dict[str, Tool] = {}
# Same list object the voice session passes as `tools=`; `register` appends to it.
TOOLS: list[dict] = []


def register(tool: Tool) -> Tool:
    if tool.name in _REGISTRY:
        raise ValueError(f"tool {tool.name!r} is already registered")
    _REGISTRY[tool.name] = tool
    TOOLS.append(tool.schema())
    return tool


def get_tool(name: str) -> Tool | None:
    return _REGISTRY.get(name)
