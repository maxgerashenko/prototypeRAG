"""Post-call summary tests (app/actions/summary.py, DEC-24). Postgres only: the LLM call
is faked, so these run without LM Studio."""

import uuid

import pytest
from sqlalchemy import delete

from app.actions import summary
from app.db import tenant_session
from app.db.models import Business, Conversation, Message


@pytest.fixture
def business_id():
    bid = uuid.uuid4()
    with tenant_session(bid) as s:
        s.add(Business(id=bid, name="Spa"))
    yield bid
    with tenant_session(bid) as s:
        s.execute(delete(Business).where(Business.id == bid))


def _conversation(bid, turns):
    with tenant_session(bid) as s:
        c = Conversation(business_id=bid, channel="voice", call_sid="CA" + uuid.uuid4().hex)
        s.add(c)
        s.flush()
        for role, content in turns:
            s.add(Message(business_id=bid, conversation_id=c.id, role=role, content=content))
        return c.id


def _fake_llm(monkeypatch, result):
    calls = []

    def fake(messages, schema, name="result"):
        calls.append(messages)
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(summary, "chat_json", fake)
    return calls


GOOD = {"summary": "Asked about opening hours.", "intent": "opening hours", "outcome": "answered",
        "follow_up": False}
TURNS = [("user", "When do you open?"), ("tool", "{...}"), ("assistant", "At nine.")]


def test_transcript_text_keeps_only_caller_and_assistant_turns():
    text = summary.transcript_text([("system", "x"), ("user", "Hi"), ("tool", "t"), ("assistant", "Hello")])
    assert text == "Caller: Hi\nAssistant: Hello"


def test_stores_summary_and_is_idempotent(business_id, monkeypatch):
    cid = _conversation(business_id, TURNS)
    calls = _fake_llm(monkeypatch, GOOD)
    assert summary.summarize_conversation(business_id, cid) is True
    with tenant_session(business_id) as s:
        c = s.get(Conversation, cid)
        assert (c.summary, c.intent, c.outcome, c.follow_up) == ("Asked about opening hours.", "opening hours", "answered", False)
        assert c.summarized_at is not None
    assert "Caller: When do you open?" in calls[0][1]["content"] and "tool" not in calls[0][1]["content"].lower()
    # a repeated status callback doesn't call the model again
    assert summary.summarize_conversation(business_id, cid) is False
    assert len(calls) == 1


def test_unknown_outcome_becomes_unresolved(business_id, monkeypatch):
    cid = _conversation(business_id, TURNS)
    _fake_llm(monkeypatch, {**GOOD, "outcome": "weird"})
    assert summary.summarize_conversation(business_id, cid) is True
    with tenant_session(business_id) as s:
        assert s.get(Conversation, cid).outcome == "unresolved"


def test_no_caller_turns_makes_no_llm_call(business_id, monkeypatch):
    cid = _conversation(business_id, [("assistant", "Hello, how can I help?")])
    calls = _fake_llm(monkeypatch, GOOD)
    assert summary.summarize_conversation(business_id, cid) is False
    assert calls == []


def test_llm_failure_is_swallowed_and_retryable(business_id, monkeypatch):
    cid = _conversation(business_id, TURNS)
    _fake_llm(monkeypatch, RuntimeError("LM Studio down"))
    assert summary.summarize_conversation(business_id, cid) is False
    with tenant_session(business_id) as s:
        assert s.get(Conversation, cid).summarized_at is None
    _fake_llm(monkeypatch, GOOD)
    assert summary.summarize_conversation(business_id, cid) is True
