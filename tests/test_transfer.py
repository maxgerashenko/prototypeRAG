"""take_message + transfer_to_human (#50). Real Postgres and Twilio's RequestValidator;
faked: STT/TTS/LLM. A real transfer (`<Dial>` to a phone) is only checked on the Mac."""

import asyncio
import threading
import time
import uuid
import xml.etree.ElementTree as ET
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select
from twilio.request_validator import RequestValidator

from app.actions import executor
from app.actions.transfer import TRANSFER_CANCELLED, cancel_transfer, transfer_number, transfer_requested
from app.db import tenant_session
from app.db.models import ActionLog, Business, Conversation, MessageForOwner
from app.main import app
from app.voice import session as session_mod
from app.voice.tools import TOOLS, run_tool
from tests.async_helpers import run_async
from tests.test_twilio import AUTH_TOKEN, PUBLIC, settings  # noqa: F401  (fixture)
from tests.test_voice_session import FakeTranscriber, RATE, _chunk, _make_session, _tool_call_delta, _with_hang_up, short_synthesize

BOSS = "+15551230000"


@pytest.fixture
def business():
    bid = uuid.uuid4()
    with tenant_session(bid) as s:
        s.add(Business(id=bid, name="Zebra Spa", settings={"transfer_number": BOSS}))
    yield bid
    with tenant_session(bid) as s:
        s.execute(delete(Business).where(Business.id == bid))


def _conversation(business, call_sid=None) -> uuid.UUID:
    with tenant_session(business) as s:
        c = Conversation(business_id=business, channel="voice", call_sid=call_sid)
        s.add(c)
        s.flush()
        return c.id


def _messages(business):
    with tenant_session(business) as s:
        return s.scalars(select(MessageForOwner).where(MessageForOwner.business_id == business)).all()


def _log_transfer(business, conversation, status="ok"):
    with tenant_session(business) as s:
        s.add(ActionLog(business_id=business, conversation_id=conversation, tool="transfer_to_human",
                        arguments={}, result="x", status=status))


def test_tools_are_registered_with_confirmation():
    names = {t["function"]["name"]: t["function"] for t in TOOLS}
    for name in ("take_message", "transfer_to_human"):
        assert "confirmed" in names[name]["parameters"]["required"]
    assert names["take_message"]["parameters"]["required"] == ["message", "confirmed"]


def test_take_message_needs_confirmation_then_saves_with_caller_id(business):
    cid = _conversation(business)
    args = {"message": "Please call me about my booking", "name": "Anna", "urgency": "urgent"}
    assert run_tool(business, "take_message", args, cid, None, "+15557778888").startswith("Not done")
    assert _messages(business) == []
    assert run_tool(business, "take_message", {**args, "confirmed": True}, cid, None, "+15557778888").startswith("Message saved")
    (row,) = _messages(business)
    assert (row.caller_name, row.caller_phone, row.urgency, row.status, row.conversation_id) == (
        "Anna", "+15557778888", "urgent", "new", cid)
    assert row.text == "Please call me about my booking" and row.recording_url is None


def test_take_message_dictated_number_wins_and_empty_message_is_an_error(business):
    out = run_tool(business, "take_message", {"message": "hi", "phone": "+15550001111", "confirmed": True},
                   None, None, "+15557778888")
    assert out.startswith("Message saved") and _messages(business)[0].caller_phone == "+15550001111"
    assert run_tool(business, "take_message", {"message": "  ", "confirmed": True}).startswith("Error")


@pytest.mark.parametrize("value, expected", [(BOSS, BOSS), (" +15551230000 ", BOSS), ("555-1234", None), (None, None), (5, None)])
def test_transfer_number_is_validated(business, value, expected):
    with tenant_session(business) as s:
        s.get(Business, business).settings = {"transfer_number": value}
    assert transfer_number(business) == expected


def test_transfer_tool_checks_channel_and_number(business):
    cid = _conversation(business, "CA" + uuid.uuid4().hex)
    ok = run_tool(business, "transfer_to_human", {"confirmed": True}, cid, "CA1", "+1555")
    assert ok.startswith("Transfer started.")
    assert transfer_requested(business, _sid(business, cid))
    assert run_tool(business, "transfer_to_human", {"confirmed": True}, cid, None, None).startswith("Error")  # browser call
    with tenant_session(business) as s:
        s.get(Business, business).settings = {}
    assert run_tool(business, "transfer_to_human", {"confirmed": True}, cid, "CA1", None).startswith("Error")


def _sid(business, cid):
    with tenant_session(business) as s:
        return s.get(Conversation, cid).call_sid


def test_transfer_requested_only_counts_an_ok_transfer_on_that_call(business):
    sid = "CA" + uuid.uuid4().hex
    cid = _conversation(business, sid)
    assert not transfer_requested(business, sid)
    _log_transfer(business, cid, "needs_confirmation")
    assert not transfer_requested(business, sid)
    _log_transfer(business, cid)
    assert transfer_requested(business, sid)
    assert not transfer_requested(business, "CAother")


def test_only_the_last_transfer_decision_counts(business):
    """#262: "actually, never mind" after a started transfer (the session's cancel marker)
    stops /twilio/transfer from dialing; a new transfer after it counts again."""
    sid = "CA" + uuid.uuid4().hex
    cid = _conversation(business, sid)
    _log_transfer(business, cid)
    cancel_transfer(business, cid)
    assert not transfer_requested(business, sid)
    _log_transfer(business, cid, "needs_confirmation")  # asking again is not a decision
    assert not transfer_requested(business, sid)
    _log_transfer(business, cid)
    assert transfer_requested(business, sid)


def test_transfer_whose_log_write_fails_is_an_error(business, monkeypatch):
    """#262: the action_log row is what /twilio/transfer dials on, so without it the model
    must not tell the caller it is connecting them."""
    sid = "CA" + uuid.uuid4().hex
    cid = _conversation(business, sid)

    def broken(_business_id):
        raise RuntimeError("db down")

    monkeypatch.setattr(executor, "tenant_session", broken)
    out = run_tool(business, "transfer_to_human", {"confirmed": True}, cid, sid, None)
    assert out.startswith("Error") and "take a message" in out
    assert not transfer_requested(business, sid)
    # a tool whose log is only an audit trail still succeeds
    assert run_tool(business, "take_message", {"message": "hi", "confirmed": True}, cid, sid, None).startswith("Message saved")


# --- Twilio routes ------------------------------------------------------------------

def _post(path, params):
    sig = RequestValidator(AUTH_TOKEN).compute_signature(PUBLIC + path, params)
    r = TestClient(app).post(path, data=params, headers={"X-Twilio-Signature": sig})
    assert r.status_code == 200, r.text
    return ET.fromstring(r.text)


def test_voice_webhook_redirects_to_transfer_after_the_stream(settings, business):
    with tenant_session(business) as s:
        s.get(Business, business).phone_numbers = ["+15550009999"]
    root = _post("/twilio/voice", {"CallSid": "CA1", "To": "+15550009999", "From": "+1555"})
    assert [e.tag for e in root] == ["Connect", "Redirect"]
    assert root.find("Redirect").text == "/twilio/transfer"


def test_transfer_route_dials_only_after_a_transfer(settings, business):
    sid = "CA" + uuid.uuid4().hex
    cid = _conversation(business, sid)
    assert [e.tag for e in _post("/twilio/transfer", {"CallSid": sid})] == ["Hangup"]  # goodbye / silence
    _log_transfer(business, cid)
    root = _post("/twilio/transfer", {"CallSid": sid})
    dial = root.find("Dial")
    assert dial.text == BOSS and dial.get("action") == "/twilio/transfer-result"
    assert [e.tag for e in _post("/twilio/transfer", {"CallSid": "CAunknown"})] == ["Hangup"]


def test_transfer_route_hangs_up_when_the_number_is_gone(settings, business):
    sid = "CA" + uuid.uuid4().hex
    _log_transfer(business, _conversation(business, sid))
    with tenant_session(business) as s:
        s.get(Business, business).settings = {}
    assert [e.tag for e in _post("/twilio/transfer", {"CallSid": sid})] == ["Hangup"]


@pytest.mark.parametrize("status, voicemail", [("completed", False), ("answered", False), ("no-answer", True),
                                               ("busy", True), ("failed", True)])
def test_unanswered_transfer_offers_voicemail(settings, status, voicemail):
    root = _post("/twilio/transfer-result", {"CallSid": "CA1", "DialCallStatus": status})
    assert (root.find("Record") is not None) == voicemail
    assert root.find("Hangup") is not None
    if voicemail:
        assert root.find("Record").get("action") == "/twilio/voicemail"


def test_voicemail_is_stored_as_a_message_for_the_owner(settings, business):
    sid = "CA" + uuid.uuid4().hex
    cid = _conversation(business, sid)
    root = _post("/twilio/voicemail", {"CallSid": sid, "From": "+15557778888", "RecordingDuration": "12",
                                       "RecordingUrl": "https://api.twilio.com/recordings/RE1"})
    assert root.find("Hangup") is not None
    (row,) = _messages(business)
    assert row.recording_url == "https://api.twilio.com/recordings/RE1" and row.caller_phone == "+15557778888"
    assert row.conversation_id == cid and "12 s" in row.text
    _post("/twilio/voicemail", {"CallSid": sid})  # no recording (caller hung up): nothing stored
    assert len(_messages(business)) == 1


# --- session ------------------------------------------------------------------------

class TransferLLM:
    """Round 1 calls transfer_to_human (confirmed); round 2 says the connecting line."""

    def __init__(self) -> None:
        self.n = 0
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    async def _create(self, **kwargs):
        if kwargs.get("max_tokens") == 1:
            return SimpleNamespace(choices=[])
        self.n += 1
        pieces = ([_chunk(tool_calls=[_tool_call_delta(0, id="c1", name="transfer_to_human",
                                                       arguments='{"reason": "wants a person", "confirmed": true}')])]
                  if self.n == 1 else [_chunk("Connecting you now.")])

        async def gen():
            for p in pieces:
                yield p

        return gen()


def test_session_hangs_up_after_a_transfer(business, monkeypatch):
    s, _, events = _make_session(business, TransferLLM(), monkeypatch)
    s._synthesize = short_synthesize
    s._call_sid = "CA" + uuid.uuid4().hex
    s.turn_detection = "manual"
    monkeypatch.setattr(FakeTranscriber, "finish", lambda self, timeout=5.0: "Can I talk to a person?")
    monkeypatch.setattr(session_mod, "HANG_UP_GRACE_S", 0.0)
    hung_up = _with_hang_up(s)

    async def run():
        await s.start()
        await s._reply_task
        await s.start_turn()
        s.end_turn()
        await s._reply_task
        await s.close()

    run_async(run())
    assert hung_up == ["hang_up"] and {"type": "hangup", "reason": "transfer"} in events
    assert [e["text"] for e in events if e["type"] == "reply"][-1] == "Connecting you now."
    assert transfer_requested(business, s._call_sid)


def test_a_failed_transfer_keeps_the_call(business, monkeypatch):
    """Browser call (no call_sid): the tool errors, the session does not hang up."""
    s, _, events = _make_session(business, TransferLLM(), monkeypatch)
    s._synthesize = short_synthesize
    s.turn_detection = "manual"
    monkeypatch.setattr(FakeTranscriber, "finish", lambda self, timeout=5.0: "Can I talk to a person?")
    hung_up = _with_hang_up(s)

    async def run():
        await s.start()
        await s._reply_task
        await s.start_turn()
        s.end_turn()
        await s._reply_task
        await s.close()

    run_async(run())
    assert hung_up == [] and not any(e.get("type") == "hangup" for e in events)


class ChangeOfMindLLM(TransferLLM):
    """Turn 1: transfer_to_human, then the connecting line. Turn 2: a normal answer."""

    async def _create(self, **kwargs):
        if kwargs.get("max_tokens") != 1 and self.n >= 2:
            self.n += 1

            async def gen():
                yield _chunk("Sure, we open at 9.")

            return gen()
        return await super()._create(**kwargs)


def _cancel_rows(business, cid):
    with tenant_session(business) as s:
        return s.scalars(select(ActionLog.id).where(ActionLog.conversation_id == cid,
                                                    ActionLog.tool == TRANSFER_CANCELLED)).all()


def test_barge_in_on_the_transfer_line_keeps_the_call(business, monkeypatch):
    """#225: the caller talks over "Connecting you now." and gets a normal answer: the
    session does not hang up into a transfer, and /twilio/transfer would not dial."""
    s, _, events = _make_session(business, ChangeOfMindLLM(), monkeypatch)
    # the connecting line is 3 s long, so there is time to talk over it
    s._synthesize = lambda text, rate: b"\0\0" * rate * (3 if text.startswith("Connecting") else 0) or short_synthesize(text, rate)
    s._call_sid = "CA" + uuid.uuid4().hex
    s.turn_detection = "manual"
    said = iter(["Can I talk to a person?", "Actually, never mind. When do you open?"])
    monkeypatch.setattr(FakeTranscriber, "finish", lambda self, timeout=5.0: next(said))
    monkeypatch.setattr(session_mod, "HANG_UP_GRACE_S", 0.0)
    hung_up = _with_hang_up(s)

    async def run():
        await s.start()
        await s._reply_task
        await s.start_turn()
        s.end_turn()
        while "Connecting you now." not in [e.get("text") for e in events]:
            await asyncio.sleep(0.02)
        await asyncio.sleep(0.2)
        await s.start_turn()  # barge-in on the connecting line
        s.end_turn()
        await s._reply_task
        await s.close()

    run_async(run())
    assert {"type": "clear"} in events
    assert [e["text"] for e in events if e["type"] == "reply"][-1] == "Sure, we open at 9."
    assert hung_up == [] and not any(e.get("type") == "hangup" for e in events)
    assert not transfer_requested(business, s._call_sid)
    assert len(_cancel_rows(business, s.conversation_id)) == 1


def test_barge_in_while_the_transfer_tool_runs_cancels_it(business, monkeypatch):
    """#262: the caller speaks while transfer_to_human is still running; its log row is
    written anyway, so the session records the cancellation."""
    s, _, events = _make_session(business, ChangeOfMindLLM(), monkeypatch)
    s._synthesize = short_synthesize
    s._call_sid = "CA" + uuid.uuid4().hex
    s.turn_detection = "manual"
    said = iter(["Can I talk to a person?", "Actually, never mind."])
    monkeypatch.setattr(FakeTranscriber, "finish", lambda self, timeout=5.0: next(said))
    monkeypatch.setattr(session_mod, "HANG_UP_GRACE_S", 0.0)
    real_run_tool = session_mod.run_tool
    started = threading.Event()

    def slow_run_tool(*args):
        started.set()
        time.sleep(0.3)
        return real_run_tool(*args)

    monkeypatch.setattr(session_mod, "run_tool", slow_run_tool)
    hung_up = _with_hang_up(s)

    async def run():
        await s.start()
        await s._reply_task
        await s.start_turn()
        s.end_turn()
        while not started.is_set():
            await asyncio.sleep(0.01)
        await s.start_turn()  # barge-in during the tool call
        s.end_turn()
        await s._reply_task
        await s.close()

    run_async(run())
    assert hung_up == []
    assert not transfer_requested(business, s._call_sid)
    assert len(_cancel_rows(business, s.conversation_id)) == 1
