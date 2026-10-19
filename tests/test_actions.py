"""Tool registry + executor (plan/04-actions.md): confirmation guard, action log.

No Postgres needed: the log write is faked.
"""

import uuid

import pytest

from app.actions import executor
from app.actions.registry import TOOLS, ActionContext, Tool, get_tool, register

BID = uuid.uuid4()
CTX = ActionContext(BID, None)
CALLS: list = []

register(Tool(
    name="test_book",
    description="test tool",
    parameters={"name": {"type": "string"}},
    required=("name",),
    side_effect=True,
    handler=lambda ctx, args: CALLS.append(args) or f"booked {args['name']}",
))
register(Tool(name="test_boom", description="x", parameters={}, side_effect=True,
              handler=lambda ctx, args: 1 / 0))


@pytest.fixture(autouse=True)
def logged(monkeypatch):
    CALLS.clear()
    rows: list = []
    monkeypatch.setattr(executor, "_log_action", lambda ctx, tool, args, result, status: rows.append((tool, args, status)))
    return rows


def test_side_effect_schema_gets_required_confirmed():
    fn = next(t["function"] for t in TOOLS if t["function"]["name"] == "test_book")
    assert fn["parameters"]["properties"]["confirmed"]["type"] == "boolean"
    assert fn["parameters"]["required"] == ["name", "confirmed"]
    assert "confirmed" not in get_tool("test_boom").parameters  # the registered tool is not mutated


def test_duplicate_registration_is_rejected():
    with pytest.raises(ValueError):
        register(get_tool("test_book"))


def test_unconfirmed_side_effect_is_refused_and_logged(logged):
    for args in ({"name": "Anna"}, {"name": "Anna", "confirmed": False}, {"name": "Anna", "confirmed": "true"}):
        assert executor.execute(CTX, "test_book", args).startswith("Not done")
    assert CALLS == []
    assert [r[2] for r in logged] == ["needs_confirmation"] * 3


def test_confirmed_side_effect_runs_and_is_logged_without_flag(logged):
    assert executor.execute(CTX, "test_book", '{"name": "Anna", "confirmed": true}') == "booked Anna"
    assert CALLS == [{"name": "Anna"}]
    assert logged == [("test_book", {"name": "Anna"}, "ok")]


def test_handler_exception_becomes_error_result(logged):
    assert executor.execute(CTX, "test_boom", {"confirmed": True}).startswith("Error")
    assert logged[-1][2] == "error"


def test_unknown_tool_and_bad_arguments(logged):
    assert executor.execute(CTX, "nope", {}).startswith("Error")
    assert executor.execute(CTX, "test_book", "{not json").startswith("Error")
    assert executor.execute(CTX, "test_book", "[1]").startswith("Error")
    assert logged == []
