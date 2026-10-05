"""Twilio adapter tests (plan/03-voice-channel.md, modes A and B): the webhook's TwiML, the
signature check, the Voice SDK token, and a whole call over /voice/ws with Twilio's
message format — fake STT/TTS/LLM, real VAD on the speech fixture, real Postgres.
No Twilio account or network needed.
"""

import base64
import functools
import json
import time
import uuid
import wave
from pathlib import Path
from types import SimpleNamespace

import jwt
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select
from twilio.request_validator import RequestValidator

from app.config import get_settings
from app.db import tenant_session
from app.db.models import Business, Conversation, Message
from app.main import app
from app.voice import session as session_mod
from app.voice import ws as ws_mod
from app.voice.audio import pcm16_to_ulaw, ulaw_to_pcm16
from app.voice.session import CallSession

FIXTURES = Path(__file__).parent / "fixtures" / "voice"
RATE = 8000
NUMBER = "+15550001234"
client = TestClient(app)


@pytest.fixture
def business():
    bid = uuid.uuid4()
    with tenant_session(bid) as s:
        s.add(Business(id=bid, name="Zebra Spa", timezone="Europe/Riga", phone_numbers=[NUMBER]))
    yield bid
    with tenant_session(bid) as s:
        s.execute(delete(Business).where(Business.id == bid))


@pytest.fixture
def settings(monkeypatch):
    """The cached Settings object; attributes set through monkeypatch are undone after the test."""
    s = get_settings()

    def set_(**values):
        for k, v in values.items():
            monkeypatch.setattr(s, k, v)

    return set_


def _stream_params(twiml: str) -> tuple[str, dict]:
    import xml.etree.ElementTree as ET

    stream = ET.fromstring(twiml).find("./Connect/Stream")
    assert stream is not None, twiml
    return stream.get("url"), {p.get("name"): p.get("value") for p in stream.findall("Parameter")}


# --- POST /twilio/voice -------------------------------------------------------------


def test_voice_sdk_call_connects_stream_with_business(business):
    # mode B: the browser page passes business_id to device.connect(); behind ngrok the
    # request arrives as http with X-Forwarded-Proto: https
    r = client.post("/twilio/voice", data={"CallSid": "CA1", "From": "client:browser-test",
                                           "business_id": str(business)},
                    headers={"host": "abc.ngrok.app", "x-forwarded-proto": "https"})
    assert r.status_code == 200 and r.headers["content-type"].startswith("application/xml")
    url, params = _stream_params(r.text)
    assert url == "wss://abc.ngrok.app/voice/ws"
    assert params == {"business_id": str(business), "caller": "client:browser-test"}


def test_dialled_number_picks_the_business(business):
    # mode A: no business_id, the dialled number decides
    r = client.post("/twilio/voice", data={"CallSid": "CA2", "From": "+15559998888", "To": NUMBER})
    _, params = _stream_params(r.text)
    assert params["business_id"] == str(business)


@pytest.mark.parametrize("data", [
    {"To": "+15550000000"},
    {"business_id": str(uuid.uuid4())},
    {"business_id": "not-a-uuid"},
    {},
])
def test_unknown_business_hangs_up(data):
    r = client.post("/twilio/voice", data=data)
    assert r.status_code == 200
    assert "<Say>" in r.text and "<Hangup />" in r.text and "<Stream" not in r.text


def test_public_base_url_overrides_headers(business, settings):
    settings(public_base_url="https://my.example.dev/")
    r = client.post("/twilio/voice", data={"business_id": str(business)})
    url, _ = _stream_params(r.text)
    assert url == "wss://my.example.dev/voice/ws"


def test_signature_checked_when_auth_token_set(business, settings):
    settings(twilio_auth_token="secret", public_base_url="https://abc.ngrok.app")
    data = {"CallSid": "CA3", "From": "client:browser-test", "business_id": str(business)}
    good = RequestValidator("secret").compute_signature("https://abc.ngrok.app/twilio/voice", data)

    assert client.post("/twilio/voice", data=data, headers={"x-twilio-signature": good}).status_code == 200
    assert client.post("/twilio/voice", data=data, headers={"x-twilio-signature": "bad"}).status_code == 403
    assert client.post("/twilio/voice", data=data).status_code == 403
    tampered = {**data, "business_id": str(uuid.uuid4())}
    assert client.post("/twilio/voice", data=tampered, headers={"x-twilio-signature": good}).status_code == 403


# --- GET /twilio/token --------------------------------------------------------------


def test_token_needs_config(settings):
    settings(twilio_account_sid="", twilio_api_key_sid="", twilio_api_key_secret="", twilio_twiml_app_sid="")
    assert client.get("/twilio/token").status_code == 503


def test_token_allows_calls_to_our_twiml_app_only(settings):
    settings(twilio_account_sid="AC123", twilio_api_key_sid="SK123", twilio_api_key_secret="k" * 32,
             twilio_twiml_app_sid="AP123")
    body = client.get("/twilio/token").json()
    claims = jwt.decode(body["token"], "k" * 32, algorithms=["HS256"])
    assert claims["iss"] == "SK123" and claims["sub"] == "AC123"
    assert claims["grants"]["identity"] == body["identity"]
    assert claims["grants"]["voice"] == {"outgoing": {"application_sid": "AP123"}}


# --- WebSocket /voice/ws ------------------------------------------------------------


def _speech() -> bytes:
    with wave.open(str(FIXTURES / "speech_8k.wav")) as w:
        return w.readframes(w.getnframes())


class FakeTranscriber:
    def __init__(self, sample_rate: int) -> None:
        assert sample_rate == RATE
        self.audio = bytearray()

    def push(self, pcm: bytes) -> None:
        self.audio += pcm

    def finish(self, timeout: float = 5.0) -> str:
        return "What time do you close on Saturday?" if self.audio else ""

    def cancel(self) -> None:
        pass


def fake_synthesize(text: str, sample_rate: int) -> bytes:
    """Greeting 3 s (30 media messages of 100 ms; long enough to barge into), other sentences 0.3 s."""
    assert sample_rate == RATE
    seconds = 3 if text.startswith("Hi") else 0.3
    return b"\x10\x00" * int(sample_rate * seconds)


class AnsweringLLM:
    def __init__(self) -> None:
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    async def _create(self, **kwargs):
        async def gen():
            delta = SimpleNamespace(content="We close at 5 pm on Saturday.", tool_calls=None)
            yield SimpleNamespace(choices=[SimpleNamespace(delta=delta)])

        return gen()


def _media(pcm: bytes, chunk_ms: int = 20) -> list[str]:
    """Inbound audio as Twilio sends it: 20 ms of base64 μ-law per message."""
    ulaw = pcm16_to_ulaw(pcm)
    step = RATE * chunk_ms // 1000
    return [json.dumps({"event": "media", "streamSid": "MZ1",
                        "media": {"track": "inbound", "payload": base64.b64encode(ulaw[i:i + step]).decode()}})
            for i in range(0, len(ulaw), step)]


def _start(business_id: str) -> str:
    return json.dumps({"event": "start", "streamSid": "MZ1", "start": {
        "streamSid": "MZ1", "callSid": "CA9", "tracks": ["inbound"],
        "mediaFormat": {"encoding": "audio/x-mulaw", "sampleRate": 8000, "channels": 1},
        "customParameters": {"business_id": business_id, "caller": "client:browser-test"},
    }})


def _receive_media(ws, n: int) -> bytes:
    audio = bytearray()
    for _ in range(n):
        msg = json.loads(ws.receive_text())
        assert msg["event"] == "media" and msg["streamSid"] == "MZ1", msg
        audio += base64.b64decode(msg["media"]["payload"])
    return bytes(audio)


def test_call_over_media_stream(business, monkeypatch):
    monkeypatch.setattr(session_mod, "get_async_chat_client", lambda: AnsweringLLM())
    monkeypatch.setattr(ws_mod, "CallSession", functools.partial(
        CallSession, transcriber_factory=FakeTranscriber, synthesize=fake_synthesize))

    with client.websocket_connect("/voice/ws") as ws:
        ws.send_text(json.dumps({"event": "connected", "protocol": "Call", "version": "1.0.0"}))
        ws.send_text(_start(str(business)))

        # greeting: 3 s as 30 μ-law messages of 100 ms, decoding back to what TTS returned
        greeting = _receive_media(ws, 30)
        assert ulaw_to_pcm16(greeting) == ulaw_to_pcm16(pcm16_to_ulaw(fake_synthesize("Hi", RATE)))

        # the caller speaks while the greeting still plays -> barge-in clears Twilio's buffer
        for m in _media(_speech() + b"\0\0" * RATE):
            ws.send_text(m)
        assert json.loads(ws.receive_text()) == {"event": "clear", "streamSid": "MZ1"}

        # end of speech -> answer
        assert len(_receive_media(ws, 3)) == RATE * 3 // 10
        # hang up only after the answer has played: what the caller didn't hear isn't saved (B2)
        time.sleep(0.5)
        ws.send_text(json.dumps({"event": "stop", "streamSid": "MZ1", "stop": {"callSid": "CA9"}}))
        # the server closes the socket only after the session's cleanup (ended_at) ran
        while ws.receive()["type"] != "websocket.close":
            pass

    with tenant_session(business) as db:
        conv = db.scalar(select(Conversation).where(Conversation.business_id == business))
        assert conv.channel == "voice" and conv.caller == "client:browser-test" and conv.ended_at is not None
        rows = db.scalars(select(Message).where(Message.business_id == business).order_by(Message.created_at))
        assert [(m.role, m.content) for m in rows] == [
            ("assistant", "Hi, you've reached Zebra Spa. I'm an AI assistant. How can I help?"),
            ("user", "What time do you close on Saturday?"),
            ("assistant", "We close at 5 pm on Saturday."),
        ]


@pytest.mark.parametrize("business_id", ["not-a-uuid", str(uuid.uuid4())])
def test_media_stream_for_unknown_business_closes(business_id):
    with client.websocket_connect("/voice/ws") as ws:
        ws.send_text(_start(business_id))
        msg = ws.receive()
        assert msg["type"] == "websocket.close"


def test_stream_token_required_when_auth_token_set(business, settings, monkeypatch):
    settings(twilio_auth_token="secret", public_base_url="https://abc.ngrok.app")
    data = {"CallSid": "CA9", "From": "client:browser-test", "business_id": str(business)}
    sig = RequestValidator("secret").compute_signature("https://abc.ngrok.app/twilio/voice", data)
    _, params = _stream_params(client.post("/twilio/voice", data=data, headers={"x-twilio-signature": sig}).text)
    assert params["token"]

    opened = []
    monkeypatch.setattr(ws_mod, "CallSession", lambda *a, **kw: opened.append(a) or _NoopSession())

    def start(token: str | None, call_sid: str = "CA9") -> None:
        msg = json.loads(_start(str(business)))
        msg["start"]["callSid"] = call_sid
        if token is not None:
            msg["start"]["customParameters"]["token"] = token
        with client.websocket_connect("/voice/ws") as ws:
            ws.send_text(json.dumps(msg))
            ws.send_text(json.dumps({"event": "stop"}))
            assert ws.receive()["type"] == "websocket.close"

    start(None)                       # no token
    start("0" * 64)                   # wrong token
    start(params["token"], "CA_OTHER")  # token of another call
    assert opened == []
    start(params["token"])            # the token from our TwiML
    assert len(opened) == 1


class _NoopSession:
    conversation_id = None

    async def start(self):
        pass

    async def feed(self, pcm):
        pass

    async def close(self):
        pass
