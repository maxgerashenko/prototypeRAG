"""Read endpoints behind the voice app (app/api/conversations.py) — local Postgres needed."""

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete

from app.api.chat import _prepare_conversation_and_history
from app.db import tenant_session
from app.db.models import Business, BusinessProfile, Conversation, Message
from app.main import app

client = TestClient(app)
T0 = datetime(2026, 10, 1, 9, 0, tzinfo=timezone.utc)


def _make_business(**kwargs) -> uuid.UUID:
    bid = uuid.uuid4()
    with tenant_session(bid) as s:
        s.add(Business(id=bid, **kwargs))
    return bid


@pytest.fixture
def businesses():
    made: list[uuid.UUID] = []

    def make(**kwargs) -> uuid.UUID:
        bid = _make_business(**{"name": f"zz-test {uuid.uuid4()}", **kwargs})
        made.append(bid)
        return bid

    yield make
    for bid in made:
        with tenant_session(bid) as s:
            s.execute(delete(Business).where(Business.id == bid))  # cascades


def _add_conversation(bid, started_at, messages, channel="voice", ended_at=None, same_tx=False) -> uuid.UUID:
    """messages: (role, content) pairs, each in its own transaction (distinct now()), or
    all in ONE transaction with same_tx=True -> identical created_at, like a saved turn."""
    with tenant_session(bid) as s:
        c = Conversation(business_id=bid, channel=channel, started_at=started_at, ended_at=ended_at)
        s.add(c)
        s.flush()
        cid = c.id
        if same_tx:
            for role, content in messages:
                s.add(Message(business_id=bid, conversation_id=cid, role=role, content=content))
    if not same_tx:
        for role, content in messages:
            with tenant_session(bid) as s:
                s.add(Message(business_id=bid, conversation_id=cid, role=role, content=content))
    return cid


def _by_id(rows, bid):
    return next(r for r in rows if r["id"] == str(bid))


def test_list_businesses_name_category_and_count(businesses):
    plain = businesses(website="https://www.abathhouse.com/williamsburg")
    tagged = businesses(settings={"category": "Spa"})
    bare = businesses()
    with tenant_session(tagged) as s:
        s.add(BusinessProfile(business_id=tagged, name="aaa-test Profile Name"))
    _add_conversation(plain, T0, [("user", "hi")])
    _add_conversation(plain, T0, [])

    rows = client.get("/businesses").json()
    assert _by_id(rows, plain)["category"] == "abathhouse.com"  # host, www. stripped
    assert _by_id(rows, plain)["conversation_count"] == 2
    assert _by_id(rows, tagged) == {"id": str(tagged), "name": "aaa-test Profile Name", "category": "Spa",
                                    "conversation_count": 0}  # profile name wins
    assert _by_id(rows, bare)["category"] == "Business"
    names = [r["name"] for r in rows]
    assert names == sorted(names, key=str.casefold)


def test_list_conversations_newest_first_with_title_and_preview(businesses):
    bid = businesses()
    old = _add_conversation(bid, T0, [("assistant", "Hi, how can I help?"), ("user", "When are you open?"),
                                      ("assistant", "From 10am.")], ended_at=T0 + timedelta(minutes=2))
    new = _add_conversation(bid, T0 + timedelta(days=1), [("assistant", "Hi!"), ("user", "Do you sell gift cards.")])
    rows = client.get(f"/businesses/{bid}/conversations").json()

    assert [r["id"] for r in rows] == [str(new), str(old)]
    first = rows[1]
    assert first["title"] == "When are you open"  # first question, end punctuation dropped
    assert first["preview"] == "From 10am." and first["preview_role"] == "assistant"
    assert first["message_count"] == 3
    assert first["ended_at"].startswith("2026-10-01T09:02")
    assert rows[0]["title"] == "Do you sell gift cards"
    assert rows[0]["preview_role"] == "user"
    assert rows[0]["ended_at"] is None


def test_conversation_without_questions_and_long_titles(businesses):
    bid = businesses()
    empty_voice = _add_conversation(bid, T0, [])
    greeting_only = _add_conversation(bid, T0 + timedelta(seconds=1), [("assistant", "Hi!")], channel="chat")
    long_q = _add_conversation(bid, T0 + timedelta(seconds=2), [("user", "x" * 200 + "?")])
    rows = {r["id"]: r for r in client.get(f"/businesses/{bid}/conversations").json()}

    assert rows[str(empty_voice)] | {"id": None, "started_at": None} == {
        "id": None, "channel": "voice", "started_at": None, "ended_at": None,
        "title": "Voice call", "preview": "", "preview_role": None, "message_count": 0}
    assert rows[str(greeting_only)]["title"] == "Chat"
    assert len(rows[str(long_q)]["title"]) == 80 and rows[str(long_q)]["title"].endswith("…")


def test_tool_and_system_messages_are_hidden(businesses):
    bid = businesses()
    cid = _add_conversation(bid, T0, [("system", "prompt"), ("user", "q"), ("tool", "{}"), ("assistant", "a")])
    summary = client.get(f"/businesses/{bid}/conversations").json()[0]
    assert summary["message_count"] == 2
    detail = client.get(f"/businesses/{bid}/conversations/{cid}").json()
    assert [m["role"] for m in detail["messages"]] == ["user", "assistant"]


def test_limit(businesses):
    bid = businesses()
    for i in range(3):
        _add_conversation(bid, T0 + timedelta(minutes=i), [])
    assert len(client.get(f"/businesses/{bid}/conversations?limit=2").json()) == 2


def test_transcript_orders_question_before_answer_saved_in_same_transaction(businesses):
    """Both rows get the same now(); the answer is inserted first here on purpose."""
    bid = businesses()
    cid = _add_conversation(bid, T0, [("assistant", "From 10am."), ("user", "When are you open?")],
                            same_tx=True)
    detail = client.get(f"/businesses/{bid}/conversations/{cid}").json()
    assert [m["content"] for m in detail["messages"]] == ["When are you open?", "From 10am."]
    assert detail["title"] == "When are you open"
    assert set(detail["messages"][0]) == {"id", "role", "content", "created_at"}


def test_chat_history_orders_question_before_answer(businesses):
    bid = businesses()
    cid = _add_conversation(bid, T0, [("assistant", "A1"), ("user", "Q1")], channel="chat",
                            same_tx=True)
    _, history = _prepare_conversation_and_history(bid, cid)
    assert history == [{"role": "user", "content": "Q1"}, {"role": "assistant", "content": "A1"}]


def test_conversations_are_isolated_per_business(businesses):
    mine, other = businesses(), businesses()
    theirs = _add_conversation(other, T0, [("user", "secret")])
    assert client.get(f"/businesses/{mine}/conversations").json() == []
    assert client.get(f"/businesses/{mine}/conversations/{theirs}").status_code == 404
    assert client.get(f"/businesses/{mine}/conversations/{uuid.uuid4()}").status_code == 404
    assert client.get("/businesses/not-a-uuid/conversations").status_code == 422
