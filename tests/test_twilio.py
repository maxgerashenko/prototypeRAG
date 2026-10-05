"""Twilio adapter tests (app/voice/twilio_routes.py, app/voice/ws.py), modes A and B.

Real parts: Postgres (business lookup, conversation with call_sid), signature checks with
Twilio's own `RequestValidator`, μ-law codec and VAD on the real speech fixture. Faked:
Google STT/TTS and the LLM (as in test_voice_session.py), and the WebSocket — the handler
runs against an in-memory socket so every wait has a timeout. Live checks (ngrok + a
TwiML App + a Voice SDK call from web/call.html) are manual.
"""

import asyncio
import base64
import functools
import json
import uuid
from types import SimpleNamespace

import jwt
import pytest
from fastapi import WebSocketDisconnect
from fastapi.testclient import TestClient
from sqlalchemy import delete, select
from twilio.request_validator import RequestValidator

from app.config import get_settings
from app.db import tenant_session
from app.db.models import Business, Conversation, Message
from app.main import app
from app.voice import session as session_mod
from app.voice import twilio_routes, ws as ws_mod
from app.voice.audio import pcm16_to_ulaw
from app.voice.session import CallSession
from tests.async_helpers import run_async
from tests.test_voice_session import SILENCE_1S, FakeTranscriber, _chunk, _speech, fake_synthesize, short_synthesize

AUTH_TOKEN = "test-auth-token"
PUBLIC = "https://abc.ngrok.app"
NUMBER = "+15550001111"


@pytest.fixture
def settings(monkeypatch):
    s = get_settings().model_copy(update={
        "twilio_auth_token": AUTH_TOKEN, "public_base_url": PUBLIC, "twilio_validate_signature": True,
        "twilio_account_sid": "AC" + "0" * 32, "twilio_api_key_sid": "SK" + "0" * 32,
        "twilio_api_key_secret": "key-secret-at-least-32-bytes-long!!", "twilio_twiml_app_sid": "AP" + "0" * 32,
    })
    monkeypatch.setattr(twilio_routes, "get_settings", lambda: s)
    return s


@pytest.fixture
def business():
    bid = uuid.uuid4()
    with tenant_session(bid) as s:
        s.add(Business(id=bid, name="Zebra Spa", phone_numbers=[NUMBER]))
    yield bid
    with tenant_session(bid) as s:
        s.execute(delete(Business).where(Business.id == bid))


def _post(client, path, params, signature=None):
    if signature is None:
        signature = RequestValidator(AUTH_TOKEN).compute_signature(PUBLIC + path, params)
    return client.post(path, data=params, headers={"X-Twilio-Signature": signature})


def _stream_params(twiml: str) -> tuple[str, dict]:
    import xml.etree.ElementTree as ET
    stream = ET.fromstring(twiml).find("./Connect/Stream")
    assert stream is not None, twiml
    return stream.get("url"), {p.get("name"): p.get("value") for p in stream.findall("Parameter")}


# --- webhook ------------------------------------------------------------------------

def test_phone_call_maps_dialled_number_to_business(settings, business):
    call_sid = "CA" + uuid.uuid4().hex
    r = _post(TestClient(app), "/twilio/voice", {"CallSid": call_sid, "To": NUMBER, "From": "+15559998888"})
    assert r.status_code == 200 and r.headers["content-type"].startswith("application/xml")
    url, params = _stream_params(r.text)
    assert url == "wss://abc.ngrok.app/voice/ws"
    assert params["business_id"] == str(business)
    assert params["caller"] == "+15559998888"
    assert twilio_routes.check_stream_token(business, call_sid, params["token"])
    assert not twilio_routes.check_stream_token(business, "CAother", params["token"])


def test_voice_sdk_call_uses_business_id_parameter(settings, business):
    params = {"CallSid": "CA" + uuid.uuid4().hex, "From": "client:web-tester", "business_id": str(business)}
    _, stream = _stream_params(_post(TestClient(app), "/twilio/voice", params).text)
    assert stream["business_id"] == str(business)


@pytest.mark.parametrize("params", [
    {"To": "+15550000000"},  # number nobody has
    {"business_id": str(uuid.uuid4())},  # unknown business
    {"business_id": "not-a-uuid"},
])
def test_unknown_business_gets_polite_hangup(settings, business, params):
    r = _post(TestClient(app), "/twilio/voice", {"CallSid": "CA1", **params})
    assert r.status_code == 200
    assert "<Say>" in r.text and "<Hangup" in r.text and "<Stream" not in r.text


def test_webhook_rejects_bad_or_missing_signature(settings, business):
    client = TestClient(app)
    params = {"CallSid": "CA1", "To": NUMBER}
    assert _post(client, "/twilio/voice", params, signature="forged").status_code == 403
    assert client.post("/twilio/voice", data=params).status_code == 403
    # signed for another URL (PUBLIC_BASE_URL not matching what Twilio called)
    other = RequestValidator(AUTH_TOKEN).compute_signature("https://evil.example/twilio/voice", params)
    assert _post(client, "/twilio/voice", params, signature=other).status_code == 403


def test_webhook_without_auth_token_fails_closed(settings, business, monkeypatch):
    s = settings.model_copy(update={"twilio_auth_token": ""})
    monkeypatch.setattr(twilio_routes, "get_settings", lambda: s)
    assert TestClient(app).post("/twilio/voice", data={"CallSid": "CA1", "To": NUMBER}).status_code == 403
    assert not twilio_routes.check_stream_token(business, "CA1", "")


def test_stream_url_derived_from_request_without_public_base_url(settings, business, monkeypatch):
    s = settings.model_copy(update={"public_base_url": "", "twilio_validate_signature": False})
    monkeypatch.setattr(twilio_routes, "get_settings", lambda: s)
    r = TestClient(app).post("/twilio/voice", data={"CallSid": "CA1", "To": NUMBER},
                             headers={"X-Forwarded-Proto": "https"})
    assert _stream_params(r.text)[0] == "wss://testserver/voice/ws"


def test_status_callback_ends_conversation(settings, business):
    call_sid = "CA" + uuid.uuid4().hex
    with tenant_session(business) as s:
        conversation = Conversation(business_id=business, channel="voice", call_sid=call_sid)
        s.add(conversation)
        s.flush()
        cid = conversation.id
    client = TestClient(app)
    assert _post(client, "/twilio/status", {"CallSid": call_sid, "CallStatus": "ringing"}).status_code == 204
    with tenant_session(business) as s:
        assert s.get(Conversation, cid).ended_at is None
    assert _post(client, "/twilio/status", {"CallSid": call_sid, "CallStatus": "completed"}).status_code == 204
    with tenant_session(business) as s:
        assert s.get(Conversation, cid).ended_at is not None
    # unknown call: nothing to do, still 204 so Twilio doesn't retry
    assert _post(client, "/twilio/status", {"CallSid": "CAnone", "CallStatus": "completed"}).status_code == 204


# --- access token (mode B) -----------------------------------------------------------

def test_token_has_voice_grant_for_twiml_app(settings):
    r = TestClient(app, client=("127.0.0.1", 50000)).get("/twilio/token")
    assert r.status_code == 200
    claims = jwt.decode(r.json()["token"], "key-secret-at-least-32-bytes-long!!", algorithms=["HS256"])
    assert claims["grants"]["identity"] == "web-tester"
    assert claims["grants"]["voice"]["outgoing"]["application_sid"] == settings.twilio_twiml_app_sid


def test_token_refused_through_ngrok_or_when_unconfigured(settings, monkeypatch):
    assert TestClient(app, client=("203.0.113.5", 50000)).get("/twilio/token").status_code == 403
    client = TestClient(app, client=("127.0.0.1", 50000))
    assert client.get("/twilio/token", headers={"X-Forwarded-For": "1.2.3.4"}).status_code == 403
    s = settings.model_copy(update={"twilio_api_key_secret": ""})
    monkeypatch.setattr(twilio_routes, "get_settings", lambda: s)
    r = client.get("/twilio/token")
    assert r.status_code == 503 and "TWILIO_API_KEY_SECRET" in r.text


# --- media stream ---------------------------------------------------------------------

class FakeSocket:
    """Enough of Starlette's WebSocket for the handler: queued inbound text, recorded output."""

    def __init__(self) -> None:
        self.inbound: asyncio.Queue = asyncio.Queue()
        self.sent: list[dict] = []
        self.closed: tuple | None = None

    async def accept(self) -> None:
        pass

    async def receive_text(self) -> str:
        item = await self.inbound.get()
        if item is None:
            raise WebSocketDisconnect(1000)
        return item

    async def send_text(self, text: str) -> None:
        self.sent.append(json.loads(text))

    async def close(self, code: int = 1000, reason: str = "") -> None:
        self.closed = (code, reason)

    def put(self, message: dict) -> None:
        self.inbound.put_nowait(json.dumps(message))


class AnswerLLM:
    """No tool call (retrieval needs LM Studio embeddings): a two-sentence answer."""

    def __init__(self) -> None:
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    async def _create(self, **kwargs):
        async def gen():
            for piece in ("We close at 5 pm", " on Saturday. Anything", " else?"):
                yield _chunk(piece)
        return gen()


def _media_frames(pcm: bytes) -> list[dict]:
    ulaw = pcm16_to_ulaw(pcm)
    return [{"event": "media", "streamSid": "MZ1", "media": {"track": "inbound",
             "payload": base64.b64encode(ulaw[i:i + 160]).decode()}} for i in range(0, len(ulaw), 160)]


async def _until(predicate, timeout: float = 10.0) -> None:
    async def poll():
        while not predicate():
            await asyncio.sleep(0.02)
    await asyncio.wait_for(poll(), timeout)


def _start(business_id, call_sid, token):
    return {"event": "start", "streamSid": "MZ1", "start": {
        "streamSid": "MZ1", "callSid": call_sid, "mediaFormat": {"encoding": "audio/x-mulaw", "sampleRate": 8000},
        "customParameters": {"business_id": str(business_id), "caller": "+15559998888", "token": token}}}


def test_media_stream_full_call(settings, business, monkeypatch):
    monkeypatch.setattr(session_mod, "get_async_chat_client", lambda: AnswerLLM())
    monkeypatch.setattr(ws_mod, "CallSession", functools.partial(
        CallSession, transcriber_factory=FakeTranscriber, synthesize=fake_synthesize))
    call_sid = "CA" + uuid.uuid4().hex
    frames_per_sentence = 8000 * 3 // 160  # fake TTS: 3 s per sentence, 20 ms frames

    async def scenario():
        sock = FakeSocket()
        handler = asyncio.create_task(ws_mod.twilio_media_stream(sock))
        sock.put({"event": "connected", "protocol": "Call", "version": "1.0.0"})
        sock.put(_start(business, call_sid, twilio_routes.stream_token(business, call_sid)))
        media = lambda: [m for m in sock.sent if m["event"] == "media"]
        await _until(lambda: len(media()) >= frames_per_sentence)  # greeting
        assert all(m["streamSid"] == "MZ1" and len(base64.b64decode(m["media"]["payload"])) == 160
                   for m in media())

        # the caller talks over the greeting (still "playing" for 3 s) → barge-in
        for frame in _media_frames(_speech() + SILENCE_1S):
            sock.put(frame)
        await _until(lambda: any(m["event"] == "clear" for m in sock.sent))
        clear_at = next(i for i, m in enumerate(sock.sent) if m["event"] == "clear")
        assert sock.sent[clear_at] == {"event": "clear", "streamSid": "MZ1"}
        await _until(lambda: len([m for m in sock.sent[clear_at:] if m["event"] == "media"]) >= 2 * frames_per_sentence)

        sock.put({"event": "stop", "streamSid": "MZ1", "stop": {"callSid": call_sid}})
        await asyncio.wait_for(handler, 5)
        return sock

    sock = run_async(scenario())
    assert sock.closed is None
    with tenant_session(business) as s:
        conversation = s.scalars(select(Conversation).where(Conversation.call_sid == call_sid)).one()
        assert conversation.caller == "+15559998888" and conversation.ended_at is not None
        messages = s.scalars(select(Message.content).where(Message.conversation_id == conversation.id)).all()
    assert "What time do you close on Saturday?" in messages
    assert "We close at 5 pm on Saturday. Anything else?" in messages


def test_media_stream_goodbye_closes_the_stream(settings, business, monkeypatch):
    """V17: "Thank you. Bye." → answered, then the stream is closed, which ends the call."""
    monkeypatch.setattr(session_mod, "get_async_chat_client", lambda: AnswerLLM())
    monkeypatch.setattr(session_mod, "HANG_UP_GRACE_S", 0.0)
    monkeypatch.setattr(FakeTranscriber, "finish", lambda self, timeout=5.0: "Thank you. Bye.")
    monkeypatch.setattr(ws_mod, "CallSession", functools.partial(
        CallSession, transcriber_factory=FakeTranscriber, synthesize=short_synthesize))
    call_sid = "CA" + uuid.uuid4().hex

    async def scenario():
        sock = FakeSocket()
        handler = asyncio.create_task(ws_mod.twilio_media_stream(sock))
        sock.put(_start(business, call_sid, twilio_routes.stream_token(business, call_sid)))
        await _until(lambda: any(m["event"] == "media" for m in sock.sent))
        for frame in _media_frames(_speech() + SILENCE_1S):
            sock.put(frame)
        await _until(lambda: sock.closed is not None)
        sock.inbound.put_nowait(None)  # Twilio's side of the close
        await asyncio.wait_for(handler, 5)
        return sock

    sock = run_async(scenario())
    assert sock.closed == (1000, "call ended")
    with tenant_session(business) as s:
        conversation = s.scalars(select(Conversation).where(Conversation.call_sid == call_sid)).one()
        assert conversation.ended_at is not None
        messages = s.scalars(select(Message.content).where(Message.conversation_id == conversation.id)).all()
    assert "Thank you. Bye." in messages


@pytest.mark.parametrize("token", ["", "forged"])
def test_media_stream_rejects_stream_not_started_by_webhook(settings, business, token):
    async def scenario():
        sock = FakeSocket()
        sock.put(_start(business, "CA1", token))
        await asyncio.wait_for(ws_mod.twilio_media_stream(sock), 5)
        return sock

    sock = run_async(scenario())
    assert sock.closed[0] == 4403 and sock.sent == []
    with tenant_session(business) as s:
        assert s.scalars(select(Conversation).where(Conversation.business_id == business)).all() == []


def test_media_stream_unknown_business_closes(settings):
    bid = uuid.uuid4()

    async def scenario():
        sock = FakeSocket()
        sock.put(_start(bid, "CA1", twilio_routes.stream_token(bid, "CA1")))
        await asyncio.wait_for(ws_mod.twilio_media_stream(sock), 5)
        return sock

    assert run_async(scenario()).closed[0] == 4404
