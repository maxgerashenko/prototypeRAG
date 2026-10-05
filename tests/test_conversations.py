"""Conversation history API (app/api/conversations.py) and continuing a call
(CallSession(continue_from=...)). Needs Postgres; LLM/STT/TTS are faked."""

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import pytest

from tests.async_helpers import run_async
from fastapi.testclient import TestClient
from sqlalchemy import delete

from app.api.conversations import make_preview, make_title
from app.db import tenant_session
from app.db.models import Business, Conversation, Message
from app.main import app
from app.voice.session import CallSession

T0 = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)


def _msg(role, content, s=0):
    return Message(role=role, content=content, created_at=T0 + timedelta(seconds=s))


@pytest.fixture
def two_businesses():
    a, b = uuid.uuid4(), uuid.uuid4()
    with tenant_session(a) as s:
        s.add_all([Business(id=a, name="Alpha Spa"), Business(id=b, name="Beta Bakery")])
    convs = {}
    for bid, specs in ((a, [("talked", True), ("silent", False)]), (b, [("other", True)])):
        for key, talked in specs:
            with tenant_session(bid) as s:
                c = Conversation(business_id=bid, channel="voice", started_at=T0, ended_at=T0 + timedelta(seconds=95))
                s.add(c)
                s.flush()
                convs[key] = c.id
                s.add(Message(business_id=bid, conversation_id=c.id, role="assistant", content="Hi, you've reached us.", created_at=T0))
                if talked:
                    s.add_all([
                        Message(business_id=bid, conversation_id=c.id, role="user", content=f"Do you have a sauna {key}?", created_at=T0 + timedelta(seconds=10)),
                        Message(business_id=bid, conversation_id=c.id, role="assistant", content="Yes, two.", created_at=T0 + timedelta(seconds=14)),
                    ])
    yield a, b, convs
    with tenant_session(a) as s:
        s.execute(delete(Business).where(Business.id.in_([a, b])))


def test_title_and_preview_helpers():
    assert make_title([_msg("assistant", "Hi"), _msg("user", "  Where are you located? ")]) == "Where are you located"
    assert make_title([_msg("assistant", "Hi")]) == "Voice call"
    long = make_title([_msg("user", "x" * 80)])
    assert len(long) == 60 and long.endswith("…")
    assert make_preview([_msg("assistant", "Hi"), _msg("user", "Bye.")]) == "You: Bye."
    assert make_preview([]) == ""


def test_list_hides_silent_calls_and_other_businesses(two_businesses):
    a, b, convs = two_businesses
    rows = TestClient(app).get(f"/businesses/{a}/conversations").json()
    assert [r["id"] for r in rows] == [str(convs["talked"])]
    r = rows[0]
    assert r["title"] == "Do you have a sauna talked"
    assert r["preview"] == "Yes, two."
    assert r["duration_s"] == 95 and r["message_count"] == 3


def test_detail_and_cross_tenant_404(two_businesses):
    a, b, convs = two_businesses
    client = TestClient(app)
    d = client.get(f"/businesses/{a}/conversations/{convs['talked']}").json()
    assert [(m["role"], m["at_s"]) for m in d["messages"]] == [("assistant", 0), ("user", 10), ("assistant", 14)]
    # B's conversation through A's URL -> 404, never B's data
    assert client.get(f"/businesses/{a}/conversations/{convs['other']}").status_code == 404


def test_businesses_count_only_conversations_with_user_messages(two_businesses):
    a, b, _ = two_businesses
    rows = {r["id"]: r for r in TestClient(app).get("/businesses").json()}
    assert rows[str(a)]["conversation_count"] == 1
    assert rows[str(b)]["conversation_count"] == 1


def test_continue_from_loads_history_and_greets_with_topic(two_businesses, monkeypatch):
    a, b, convs = two_businesses
    events = []

    async def send_audio(pcm):
        pass

    async def send_event(e):
        events.append(e)

    async def run(continue_from):
        s = CallSession(a, 16000, send_audio, send_event, continue_from=continue_from,
                        synthesize=lambda t, r: b"")
        await s.start()
        await s._reply_task
        await s.close()
        return s

    s = run_async(run(convs["talked"]))
    assert events[0]["text"].startswith("Welcome back to Alpha Spa. I'm an AI assistant.")
    assert "Do you have a sauna talked" in events[0]["text"]
    assert [m["role"] for m in s._history] == ["assistant", "user", "assistant", "assistant"]
    assert s.conversation_id != convs["talked"]  # a new conversation row

    events.clear()
    s = run_async(run(convs["other"]))  # another business's conversation: ignored
    assert events[0]["text"].startswith("Hi, you've reached Alpha Spa.")
    assert s._history == [{"role": "assistant", "content": events[0]["text"]}]


# --- question/answer ordering ------------------------------------------------------
# A turn's question and answer are saved in one transaction, so both get the same
# created_at (now() = transaction start). The rows below are inserted answer-first in one
# transaction on purpose; every reader must still put the question first (message_order()).

@pytest.fixture
def tied_turn():
    bid = uuid.uuid4()
    with tenant_session(bid) as s:
        s.add(Business(id=bid, name="Tie Test"))
    with tenant_session(bid) as s:
        c = Conversation(business_id=bid, channel="voice", started_at=T0)
        s.add(c)
        s.flush()
        cid = c.id
    with tenant_session(bid) as s:
        # ids chosen so the old tiebreak (ORDER BY created_at, id) always puts the answer first
        s.add(Message(id=uuid.UUID(int=1), business_id=bid, conversation_id=cid, role="assistant", content="From 10am."))
        s.add(Message(id=uuid.UUID(int=2**128 - 1), business_id=bid, conversation_id=cid, role="user",
                      content="When are you open?"))
    yield bid, cid
    with tenant_session(bid) as s:
        s.execute(delete(Business).where(Business.id == bid))


def test_transcript_puts_question_before_answer_of_the_same_turn(tied_turn):
    bid, cid = tied_turn
    d = TestClient(app).get(f"/businesses/{bid}/conversations/{cid}").json()
    assert [m["content"] for m in d["messages"]] == ["When are you open?", "From 10am."]
    row = TestClient(app).get(f"/businesses/{bid}/conversations").json()[0]
    assert row["title"] == "When are you open" and row["preview"] == "From 10am."


def test_chat_history_puts_question_before_answer(tied_turn):
    from app.api.chat import _prepare_conversation_and_history

    bid, cid = tied_turn
    _, history = _prepare_conversation_and_history(bid, cid)
    assert history == [{"role": "user", "content": "When are you open?"}, {"role": "assistant", "content": "From 10am."}]


def test_continue_from_history_puts_question_before_answer(tied_turn):
    bid, cid = tied_turn

    async def run():
        async def send_audio(pcm):
            pass

        async def send_event(e):
            pass

        s = CallSession(bid, 16000, send_audio, send_event, continue_from=cid, synthesize=lambda t, r: b"")
        await s.start()
        await s._reply_task
        await s.close()
        return s

    s = run_async(run())
    assert [m["content"] for m in s._history[:2]] == ["When are you open?", "From 10am."]
