"""V19 for mode C (app/voice/browser_ws.py): `conversations.ended_at` is set when the browser
drops, even if closing the session is cancelled, and a startup sweep closes calls left open by
a killed process. Real Postgres; the session's STT/TTS/LLM are never reached (no audio is sent)."""

import asyncio
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import delete, select

from app.db import tenant_session
from app.db.models import Business, Conversation, Message
from app.voice import browser_ws
from app.voice.session import CallSession
from tests.async_helpers import run_async


@pytest.fixture
def business():
    bid = uuid.uuid4()
    with tenant_session(bid) as s:
        s.add(Business(id=bid, name="Zebra Spa"))
    yield bid
    with tenant_session(bid) as s:
        s.execute(delete(Business).where(Business.id == bid))


def _ended_at(bid, cid):
    with tenant_session(bid) as s:
        return s.get(Conversation, cid).ended_at


def _add_conversation(bid, **kw):
    cid = uuid.uuid4()
    with tenant_session(bid) as s:
        s.add(Conversation(id=cid, business_id=bid, channel="voice", **kw))
    return cid


class FakeWS:
    """Receives one scripted outcome, then behaves like a dead socket."""

    def __init__(self, outcome):
        self.outcome = outcome
        self.sent = []

    async def accept(self): ...
    async def send_json(self, event): self.sent.append(event)
    async def send_bytes(self, pcm): ...
    async def close(self, code=1000, reason=""): ...

    async def receive(self):
        if isinstance(self.outcome, BaseException):
            raise self.outcome
        return self.outcome


def _run_handler(bid, ws, monkeypatch, close_raises=None):
    if close_raises is not None:
        async def broken_close(self):
            raise close_raises
        monkeypatch.setattr(CallSession, "close", broken_close)
    return run_async(browser_ws.browser_voice(ws, business_id=bid, mode="vad", continue_from=None))


def _only_conversation(bid):
    with tenant_session(bid) as s:
        return s.scalars(select(Conversation.id).where(Conversation.business_id == bid)).one()


def test_clean_disconnect_sets_ended_at(business, monkeypatch):
    _run_handler(business, FakeWS({"type": "websocket.disconnect"}), monkeypatch)
    assert _ended_at(business, _only_conversation(business)) is not None


def test_dropped_socket_sets_ended_at(business, monkeypatch):
    with pytest.raises(RuntimeError):
        _run_handler(business, FakeWS(RuntimeError("socket gone")), monkeypatch)
    assert _ended_at(business, _only_conversation(business)) is not None


def test_cancelled_session_close_still_sets_ended_at(business, monkeypatch):
    with pytest.raises(asyncio.CancelledError):
        _run_handler(business, FakeWS({"type": "websocket.disconnect"}), monkeypatch,
                     close_raises=asyncio.CancelledError())
    assert _ended_at(business, _only_conversation(business)) is not None


def test_sweep_closes_orphaned_browser_calls_only(business):
    start = datetime.now(timezone.utc) - timedelta(hours=1)
    orphan = _add_conversation(business, caller="browser", started_at=start)
    with tenant_session(business) as s:
        s.add(Message(business_id=business, conversation_id=orphan, role="user", content="hi",
                      created_at=start + timedelta(minutes=2)))
    twilio = _add_conversation(business, caller="+1555", call_sid="CA" + uuid.uuid4().hex)
    done_at = start + timedelta(minutes=5)
    done = _add_conversation(business, caller="browser", started_at=start, ended_at=done_at)

    browser_ws.end_orphaned_browser_conversations()

    assert _ended_at(business, orphan) == start + timedelta(minutes=2)  # last message
    assert _ended_at(business, twilio) is None  # the Twilio status callback owns these
    assert _ended_at(business, done) == done_at
