"""Tool registry (plan/04-actions.md → Code layout).

One place that knows every tool the LLM may call: its JSON schema (the same one goes to
the OpenAI-compatible `tools=[...]` in pipeline mode and to Gemini Live declarations in
stage 2, DEC-30) and the Python handler behind it. The executor (`executor.py`) is the
only thing that calls handlers.

A tool with `side_effect=True` (booking, SMS, message to the owner, ...) gets a required
`confirmed` boolean added to its schema; the executor refuses to run it until the model
passes `confirmed=true`, i.e. after reading the details back and hearing a "yes".

A tool with `optional=True` (bookings, sessions) is offered to the model, and run by the
executor, only for businesses that list its name in `businesses.settings["enabled_tools"]`.
`TOOLS` holds the always-on tools only; `tools_for(business_id)` adds the enabled optional ones.
"""

import uuid
from collections.abc import Callable
from dataclasses import dataclass

from sqlalchemy import select


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
    optional: bool = False  # off unless the business lists it in settings["enabled_tools"]

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
# Always-on tools only; `register` appends to it. Callers use `tools_for(business_id)`,
# which returns this very list when the business enabled no optional tool.
TOOLS: list[dict] = []
_OPTIONAL_SCHEMAS: dict[str, dict] = {}


def register(tool: Tool) -> Tool:
    if tool.name in _REGISTRY:
        raise ValueError(f"tool {tool.name!r} is already registered")
    _REGISTRY[tool.name] = tool
    if tool.optional:
        _OPTIONAL_SCHEMAS[tool.name] = tool.schema()
    else:
        TOOLS.append(tool.schema())
    return tool


def get_tool(name: str) -> Tool | None:
    return _REGISTRY.get(name)


def enabled_tool_names(business_id: uuid.UUID) -> frozenset[str]:
    """Optional tools this business turned on: `businesses.settings["enabled_tools"]`, a
    list of tool names. Missing, malformed or unknown names mean "off"."""
    from app.db import tenant_session  # lazy: keeps the registry importable without a DB
    from app.db.models import Business

    with tenant_session(business_id) as session:
        settings = session.scalar(select(Business.settings).where(Business.id == business_id)) or {}
    names = settings.get("enabled_tools")
    if not isinstance(names, list):
        return frozenset()
    return frozenset(n for n in names if isinstance(n, str) and n in _OPTIONAL_SCHEMAS)


def tools_for(business_id: uuid.UUID) -> list[dict]:
    """Schemas to send as `tools=` for this business: `TOOLS` plus its enabled optional tools."""
    enabled = enabled_tool_names(business_id)
    if not enabled:
        return TOOLS
    return [*TOOLS, *(_OPTIONAL_SCHEMAS[n] for n in sorted(enabled))]
