"""Per-business enabled tools (#151): booking tools are offered to the model in chat and
voice, and run by the executor, only for businesses that list them in
`businesses.settings["enabled_tools"]`. Postgres only; the LLM and Google are faked."""

import uuid

import pytest
from sqlalchemy import delete

from app.actions import executor
from app.actions.registry import ActionContext, enabled_tool_names, tools_for
from app.db import tenant_session
from app.db.models import Business
from app.rag import answer
from app.rag.prompt import BOOKING_PROMPT, SYSTEM_PROMPT, build_prompt, tools_prompt
from app.voice import session as session_mod
from app.voice.tools import TOOLS
from tests.async_helpers import run_async
from tests.test_rag import FakeLLM
from tests.test_voice_session import _make_session

BOOKING = ["check_availability", "create_booking", "cancel_booking"]


def _names(tools):
    return {t["function"]["name"] for t in tools}


def _make_business(settings) -> uuid.UUID:
    bid = uuid.uuid4()
    with tenant_session(bid) as s:
        s.add(Business(id=bid, name="Spa", timezone="America/New_York", settings=settings))
    return bid


@pytest.fixture
def make_business():
    ids = []

    def make(settings):
        ids.append(_make_business(settings))
        return ids[-1]

    yield make
    for bid in ids:
        with tenant_session(bid) as s:
            s.execute(delete(Business).where(Business.id == bid))


def test_booking_tools_are_not_in_the_always_on_list():
    assert not (_names(TOOLS) & set(BOOKING))
    assert {"search_business_info", "take_message", "transfer_to_human"} <= _names(TOOLS)


def test_default_business_gets_exactly_the_always_on_tools(make_business):
    bid = make_business({})
    assert tools_for(bid) is TOOLS


def test_enabled_booking_tools_are_added(make_business):
    bid = make_business({"enabled_tools": BOOKING})
    tools = tools_for(bid)
    assert _names(tools) == _names(TOOLS) | set(BOOKING)
    create = next(t for t in tools if t["function"]["name"] == "create_booking")
    assert "confirmed" in create["function"]["parameters"]["required"]


def test_one_enabled_tool_does_not_enable_the_others(make_business):
    bid = make_business({"enabled_tools": ["check_availability"]})
    assert _names(tools_for(bid)) - _names(TOOLS) == {"check_availability"}


@pytest.mark.parametrize("value", ["create_booking", {"create_booking": True}, [None, 3, "nope", "search_business_info"], None])
def test_malformed_or_unknown_setting_means_off(make_business, value):
    bid = make_business({"enabled_tools": value})
    assert enabled_tool_names(bid) == frozenset()
    assert tools_for(bid) is TOOLS


def test_executor_refuses_a_disabled_tool_even_if_the_model_calls_it(make_business):
    off = make_business({})
    out = executor.execute(ActionContext(off), "create_booking",
                           {"date": "2099-11-05", "time": "14:00", "name": "A", "confirmed": True})
    assert out == "Error: create_booking is not available for this business."
    on = make_business({"enabled_tools": BOOKING})
    out = executor.execute(ActionContext(on), "check_availability", {"time": "07:00"})
    assert "not available for this business" not in out  # reached the handler (which wants a date)


def test_enabling_is_per_business(make_business):
    a = make_business({"enabled_tools": BOOKING})
    b = make_business({})
    assert enabled_tool_names(a) and not enabled_tool_names(b)


def test_prompt_mentions_bookings_only_when_enabled():
    assert tools_prompt(frozenset()) == "" and tools_prompt({"search_business_info"}) == ""
    assert tools_prompt(frozenset(BOOKING)) == BOOKING_PROMPT
    off = build_prompt(None, [], [], "hi")[0]["content"]
    on = build_prompt(None, [], [], "hi", enabled_tools=frozenset(BOOKING))[0]["content"]
    assert "create_booking" not in off and off.startswith(SYSTEM_PROMPT)
    assert "check_availability" in on and "confirmed=true" in on


def test_chat_offers_the_enabled_tools_only(make_business, monkeypatch):
    on = make_business({"enabled_tools": BOOKING})
    off = make_business({})
    llm = FakeLLM("ok", "ok", "ok", "ok")
    monkeypatch.setattr(answer, "get_chat_client", lambda: llm)
    monkeypatch.setattr(answer, "prepare_prompt", lambda b, q, history=None, top_n=5: (None, [], [{"role": "user", "content": q}]))
    answer.answer_question(on, "book?")
    "".join(answer.stream_answer(on, "book?"))
    answer.answer_question(off, "book?")
    "".join(answer.stream_answer(off, "book?"))
    offered = [_names(r["tools"]) & set(BOOKING) for r in llm.requests]
    assert offered == [set(BOOKING), set(BOOKING), set(), set()]


def _start(bid, monkeypatch):
    s, _, _ = _make_session(bid, FakeLLM(), monkeypatch)

    async def run():
        await s.start()
        await s.close()

    run_async(run())
    return s


def test_voice_call_gets_tools_and_prompt_for_an_enabled_business(make_business, monkeypatch):
    s = _start(make_business({"enabled_tools": BOOKING}), monkeypatch)
    assert BOOKING_PROMPT in s._system_prompt
    assert set(BOOKING) <= _names(s._tools)


def test_voice_call_without_the_setting_is_unchanged(make_business, monkeypatch):
    s = _start(make_business({}), monkeypatch)
    assert BOOKING_PROMPT not in s._system_prompt
    assert s._tools is session_mod.TOOLS
