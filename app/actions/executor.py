"""Runs tool calls from the LLM: validation, confirmation guard, action log.

Rules enforced here in code, not only in the prompt (plan/04-actions.md):
- unknown tool / bad arguments → an error string the model can see, never an exception;
- a side-effect tool runs only with `confirmed=true`, otherwise the model is told to read
  the details back and ask for an explicit yes;
- every side-effect call (also refused and failed ones) is written to `action_log`.
Read-only tools (search) are not logged: one DB write per search would add latency to the
caller's turn, and their calls already show in the voice-turn timing log.
"""

import json
import logging

from app.actions.registry import ActionContext, enabled_tool_names, get_tool
from app.db import tenant_session
from app.db.models import ActionLog

log = logging.getLogger(__name__)


def _log_action(ctx: ActionContext, tool: str, arguments: dict, result: str, status: str) -> None:
    try:
        with tenant_session(ctx.business_id) as session:
            session.add(ActionLog(
                business_id=ctx.business_id, conversation_id=ctx.conversation_id,
                tool=tool, arguments=arguments, result=result, status=status,
            ))
    except Exception:  # an audit-log failure must not break the call, but must be visible
        log.exception("action_log write failed for %s", tool)


def execute(ctx: ActionContext, name: str, arguments: str | dict) -> str:
    """Execute one tool call; returns the tool result text for the model."""
    tool = get_tool(name)
    if tool is None:
        return f"Error: unknown tool {name!r}."
    if tool.optional and name not in enabled_tool_names(ctx.business_id):
        return f"Error: {name} is not available for this business."  # the model may call tools it was never offered
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments or "{}")
        except json.JSONDecodeError:
            return f"Error: arguments for {name} are not valid JSON."
    if not isinstance(arguments, dict):
        return f"Error: arguments for {name} must be a JSON object."

    if not tool.side_effect:
        return tool.handler(ctx, arguments)

    confirmed = arguments.pop("confirmed", False) is True
    if not confirmed:
        result = (
            f"Not done: {name} changes something. Read all details back to the caller, "
            "ask for an explicit yes, then call it again with confirmed=true."
        )
        _log_action(ctx, name, arguments, result, "needs_confirmation")
        return result
    try:
        result = tool.handler(ctx, arguments)
    except Exception as exc:
        log.exception("tool %s failed", name)
        result = f"Error: {name} failed ({type(exc).__name__})."
        _log_action(ctx, name, arguments, result, "error")
        return result
    status = "error" if result.startswith("Error") else "ok"
    _log_action(ctx, name, arguments, result, status)
    return result
