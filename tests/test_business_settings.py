"""Business settings API (app/api/business_settings.py, app/voice/twilio_numbers.py) and the
greeting in calls. Real parts: Postgres (business, settings JSONB, greeting_audio, messages,
phone numbers) and the /twilio/voice webhook. Faked: TTS, the embedding server and the Twilio
account (the number list). Needs Postgres."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select
from twilio.request_validator import RequestValidator

from app.api import business_settings
from app.config import get_settings
from app.db import tenant_session
from app.db.models import Business, Conversation, GreetingAudio, Message
from app.main import app
from app.voice import greeting_cache, tts, twilio_numbers, twilio_routes
from tests.test_twilio import AUTH_TOKEN, NUMBER, PUBLIC, _stream_params
from tests.test_voice_session import RATE, FakeLLM, _call_once, _make_session, fake_synthesize

OTHER_NUMBER = "+15557770000"
FREE_NUMBER = "+15551230000"
FULL = "Hi, I am an AI assistant. "


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def biz():
    bid = uuid.uuid4()
    with tenant_session(bid) as s:
        s.add(Business(id=bid, name="Zebra Spa", phone_numbers=[NUMBER],
                       settings={"transfer_number": "+15550009999", "google_calendar_id": "cal-1"}))
    yield bid
    with tenant_session(bid) as s:
        s.execute(delete(Business).where(Business.id == bid))


@pytest.fixture
def other_biz():
    bid = uuid.uuid4()
    with tenant_session(bid) as s:
        s.add(Business(id=bid, name="Yak Yoga", phone_numbers=[OTHER_NUMBER]))
    yield bid
    with tenant_session(bid) as s:
        s.execute(delete(Business).where(Business.id == bid))


@pytest.fixture
def fake_tts(monkeypatch):
    calls: list[tuple] = []

    def synth(text, rate):
        calls.append((text, rate))
        return fake_synthesize(text, rate)

    monkeypatch.setattr(tts, "synthesize", synth)
    return calls


def _settings(bid):
    with tenant_session(bid) as s:
        return dict(s.scalar(select(Business.settings).where(Business.id == bid)))


def _audio_texts(bid):
    with tenant_session(bid) as s:
        return sorted({t for (t,) in s.execute(select(GreetingAudio.text).where(GreetingAudio.business_id == bid))})


# --- greeting ----------------------------------------------------------------------

def test_business_greeting_resolver():
    b = Business(name="X", settings={})
    assert greeting_cache.business_greeting(b, None) == "Hi, I am an AI assistant."
    b.settings = {"greeting": "  Welcome to X!  "}
    assert greeting_cache.business_greeting(b, None) == "Hi, I am an AI assistant. Welcome to X!"
    b.settings = {"greeting": "   "}
    assert greeting_cache.business_greeting(b, None) == "Hi, I am an AI assistant."
    # the owner typed the disclosure themselves: it is not said twice
    b.settings = {"greeting": "hi, i am an AI assistant. Welcome to X!"}
    assert greeting_cache.business_greeting(b, None) == "Hi, I am an AI assistant. Welcome to X!"
    for typed in ("I am an AI assistant. Welcome to X!", "Hi, I'm an AI assistant - Welcome to X!",
                  "Hello! I'm an AI assistant. Welcome to X!"):
        b.settings = {"greeting": typed}
        assert greeting_cache.business_greeting(b, None) == "Hi, I am an AI assistant. Welcome to X!", typed
    b.settings = {"greeting": "I am an AI assistant"}  # nothing else said
    assert greeting_cache.business_greeting(b, None) == "Hi, I am an AI assistant."
    b.settings = {"greeting": "I am an AIRLINE helper"}  # not the disclosure
    assert greeting_cache.business_greeting(b, None) == "Hi, I am an AI assistant. I am an AIRLINE helper"


def test_get_greeting_default(client, biz):
    r = client.get(f"/businesses/{biz}/greeting")
    assert r.status_code == 200
    assert r.json() == {"text": "", "is_default": True, "cached": False, "cached_at": None, "max_length": 300,
                           "full_text": "Hi, I am an AI assistant."}
    assert client.get(f"/businesses/{uuid.uuid4()}/greeting").status_code == 404


def test_put_greeting_saves_caches_and_prunes(client, biz, fake_tts):
    r = client.put(f"/businesses/{biz}/greeting", json={"text": "  Welcome to Zebra Spa!  "})
    assert r.status_code == 200
    body = r.json()
    assert body["text"] == "Welcome to Zebra Spa!"  # the owner part only
    assert body["is_default"] is False and body["cached"] is True and body["cache_error"] is None
    assert body["cached_at"] is not None
    assert sorted(rate for _, rate in fake_tts) == sorted(greeting_cache.SAMPLE_RATES)
    assert {t for t, _ in fake_tts} == {FULL + "Welcome to Zebra Spa!"}
    assert client.get(f"/businesses/{biz}/greeting").json()["cached"] is True
    # a second text: the old audio is pruned
    client.put(f"/businesses/{biz}/greeting", json={"text": "Hello there"})
    assert _audio_texts(biz) == [FULL + "Hello there"]


def test_put_greeting_empty_resets_and_keeps_other_settings(client, biz, fake_tts):
    client.put(f"/businesses/{biz}/greeting", json={"text": "Hello"})
    assert _settings(biz) == {"transfer_number": "+15550009999", "google_calendar_id": "cal-1", "greeting": "Hello"}
    r = client.put(f"/businesses/{biz}/greeting", json={"text": "   "})
    assert r.json()["text"] == "" and r.json()["is_default"] is True and r.json()["cached"] is True
    assert _settings(biz) == {"transfer_number": "+15550009999", "google_calendar_id": "cal-1"}
    assert _audio_texts(biz) == [FULL.strip()]


def test_put_greeting_too_long_is_422(client, biz, fake_tts):
    assert client.put(f"/businesses/{biz}/greeting", json={"text": "x" * 300}).status_code == 200
    r = client.put(f"/businesses/{biz}/greeting", json={"text": "x" * 301})
    assert r.status_code == 422 and isinstance(r.json()["detail"], str)
    assert _settings(biz)["greeting"] == "x" * 300  # unchanged
    assert client.put(f"/businesses/{uuid.uuid4()}/greeting", json={"text": "Hi"}).status_code == 404


def test_put_greeting_tts_failure_keeps_the_text(client, biz, monkeypatch):
    def boom(text, rate):
        raise RuntimeError("no Google credentials")

    monkeypatch.setattr(tts, "synthesize", boom)
    r = client.put(f"/businesses/{biz}/greeting", json={"text": "Hello"})
    assert r.status_code == 200
    body = r.json()
    assert body["text"] == "Hello" and body["cached"] is False and "no Google credentials" in body["cache_error"]
    assert _settings(biz)["greeting"] == "Hello"


def test_cached_needs_both_rates(biz):
    text = FULL.strip()
    assert greeting_cache.cache_status(biz, text) == (False, None)
    settings = get_settings()
    greeting_cache.store(biz, text, RATE, settings.tts_voice, settings.voice_language, b"\0\0")
    cached, at = greeting_cache.cache_status(biz, text)
    assert cached is False and at is not None
    greeting_cache.store(biz, text, 16000, settings.tts_voice, settings.voice_language, b"\0\0")
    assert greeting_cache.cache_status(biz, text)[0] is True
    assert greeting_cache.prune(biz, text) == 0
    # cached prompts (not an opening greeting) survive the prune
    greeting_cache.store(biz, "Are you still there?", RATE, settings.tts_voice, settings.voice_language, b"\0\0")
    assert greeting_cache.prune(biz, "something else") == 2
    assert greeting_cache.load(biz, "Are you still there?", RATE, settings.tts_voice, settings.voice_language) is not None


def test_call_opens_with_the_custom_greeting(client, biz, fake_tts, monkeypatch):
    client.put(f"/businesses/{biz}/greeting", json={"text": "Welcome to Zebra Spa!"})
    fake_tts.clear()
    s, audio_out, events = _make_session(biz, FakeLLM(), monkeypatch)
    _call_once(biz, s._send_audio, s._send_event, lambda t, r: pytest.fail("TTS used, not the cache"))
    assert fake_tts == []  # played from the cache saved by the PUT
    assert [e["text"] for e in events if e["type"] == "reply"][0] == FULL + "Welcome to Zebra Spa!"
    assert len(audio_out) == 1


# --- top questions -----------------------------------------------------------------

T0 = datetime.now(UTC) - timedelta(days=1)


def _add_turns(bid, pairs):
    with tenant_session(bid) as s:
        conv = Conversation(business_id=bid, channel="voice", started_at=T0)
        s.add(conv)
        s.flush()
        for i, (q, a) in enumerate(pairs):
            s.add(Message(business_id=bid, conversation_id=conv.id, role="user", content=q,
                          created_at=T0 + timedelta(seconds=10 * i)))
            s.add(Message(business_id=bid, conversation_id=conv.id, role="assistant", content=a,
                          created_at=T0 + timedelta(seconds=10 * i + 1)))


def _fake_embed(texts):
    return [[1.0, 0.0, 0.0] if "open" in t.lower() else [0.0, 1.0, 0.0] for t in texts]


def test_top_questions_empty_does_not_call_embed(client, biz, monkeypatch):
    monkeypatch.setattr(business_settings, "embed", lambda texts: pytest.fail("embed called"))
    r = client.get(f"/businesses/{biz}/top-questions")
    assert r.status_code == 200
    assert r.json() == {"total_questions": 0, "conversations": 0, "days": 30, "questions": []}
    assert client.get(f"/businesses/{uuid.uuid4()}/top-questions").status_code == 404
    assert client.get(f"/businesses/{biz}/top-questions?limit=0").status_code == 422
    assert client.get(f"/businesses/{biz}/top-questions?days=366").status_code == 422


def test_top_questions_merges_and_ranks(client, biz, other_biz, monkeypatch):
    monkeypatch.setattr(business_settings, "embed", _fake_embed)
    _add_turns(biz, [("Are you open on Sunday?", "No."), ("Are you OPEN today?", "Yes."),
                     ("What does a massage cost?", "60 euros."), ("ok", "Bye")])
    _add_turns(other_biz, [("Are you open at night?", "leaked")])  # another business
    r = client.get(f"/businesses/{biz}/top-questions?limit=5&days=30")
    assert r.status_code == 200
    body = r.json()
    assert body["total_questions"] == 3 and body["conversations"] == 1 and body["days"] == 30
    assert [(q["count"], q["percent"]) for q in body["questions"]] == [(2, 67), (1, 33)]
    assert body["questions"][0]["answer"] in ("No.", "Yes.")
    assert body["questions"][1] == {"question": "What does a massage cost?", "count": 1, "percent": 33,
                                    "answer": "60 euros."}


def test_top_questions_embedding_down_is_503(client, biz, monkeypatch):
    def down(texts):
        raise ConnectionError("LM Studio is not running")

    monkeypatch.setattr(business_settings, "embed", down)
    _add_turns(biz, [("Are you open on Sunday?", "No.")])
    r = client.get(f"/businesses/{biz}/top-questions")
    assert r.status_code == 503 and r.json()["detail"] == "embedding server unavailable"


# --- numbers -----------------------------------------------------------------------

def _account(*numbers):
    return [{"number": n, "friendly_name": n, "voice": True, "sms": True, "routed": True} for n in numbers]


def test_twilio_numbers_without_credentials(client, monkeypatch):
    s = get_settings().model_copy(update={"twilio_account_sid": "", "twilio_auth_token": ""})
    monkeypatch.setattr(twilio_numbers, "get_settings", lambda: s)
    body = client.get("/twilio/numbers").json()
    assert body["numbers"] == [] and "TWILIO_ACCOUNT_SID" in body["error"]


def test_twilio_numbers_shows_holders(client, biz, other_biz, monkeypatch):
    monkeypatch.setattr(twilio_numbers, "account_numbers", lambda: _account(OTHER_NUMBER, FREE_NUMBER, NUMBER))
    body = client.get("/twilio/numbers").json()
    assert body["error"] is None
    assert [n["number"] for n in body["numbers"]] == sorted([OTHER_NUMBER, FREE_NUMBER, NUMBER])
    by_number = {n["number"]: n for n in body["numbers"]}
    assert by_number[NUMBER]["business_id"] == str(biz) and by_number[NUMBER]["business_name"] == "Zebra Spa"
    assert by_number[OTHER_NUMBER]["business_id"] == str(other_biz)
    assert by_number[FREE_NUMBER]["business_id"] is None and by_number[FREE_NUMBER]["business_name"] is None


def test_account_numbers_maps_the_twilio_client(monkeypatch):
    from types import SimpleNamespace

    s = get_settings().model_copy(update={"twilio_account_sid": "AC" + "0" * 32, "twilio_auth_token": "tok"})
    monkeypatch.setattr(twilio_numbers, "get_settings", lambda: s)
    rows = [
        SimpleNamespace(phone_number=NUMBER, friendly_name="(555) 000-1111", capabilities={"voice": True, "sms": False},
                        voice_url="https://x.ngrok.app/twilio/voice"),
        SimpleNamespace(phone_number=FREE_NUMBER, friendly_name="Shop", capabilities={"voice": True, "sms": True},
                        voice_url=None),
    ]
    import twilio.rest

    class FakeClient:
        def __init__(self, sid, token):
            self.incoming_phone_numbers = SimpleNamespace(list=lambda limit: rows)

    monkeypatch.setattr(twilio.rest, "Client", FakeClient)
    assert twilio_numbers.account_numbers() == [
        {"number": NUMBER, "friendly_name": "(555) 000-1111", "voice": True, "sms": False, "routed": True},
        {"number": FREE_NUMBER, "friendly_name": "Shop", "voice": True, "sms": True, "routed": False},
    ]


def test_put_number_moves_it_and_routes_calls(client, biz, other_biz, monkeypatch):
    monkeypatch.setattr(twilio_numbers, "account_numbers", lambda: _account(NUMBER, OTHER_NUMBER, FREE_NUMBER))
    s = get_settings().model_copy(update={
        "twilio_auth_token": AUTH_TOKEN, "public_base_url": PUBLIC, "twilio_validate_signature": True})
    monkeypatch.setattr(twilio_routes, "get_settings", lambda: s)
    r = client.put(f"/businesses/{other_biz}/number", json={"number": NUMBER})
    assert r.status_code == 200
    assert r.json() == {"business_id": str(other_biz), "phone_numbers": [NUMBER], "moved_from": [str(biz)]}
    listed = {b["id"]: b["phone_numbers"] for b in client.get("/businesses").json()}
    assert listed[str(biz)] == [] and listed[str(other_biz)] == [NUMBER]  # replaced, not added
    params = {"CallSid": "CA" + uuid.uuid4().hex, "To": NUMBER}
    sig = RequestValidator(AUTH_TOKEN).compute_signature(PUBLIC + "/twilio/voice", params)
    call = client.post("/twilio/voice", data=params, headers={"X-Twilio-Signature": sig})
    assert _stream_params(call.text)[1]["business_id"] == str(other_biz)
    # a free number: nobody loses one
    r = client.put(f"/businesses/{biz}/number", json={"number": FREE_NUMBER})
    assert r.json()["moved_from"] == [] and r.json()["phone_numbers"] == [FREE_NUMBER]


def test_put_number_errors(client, biz, monkeypatch):
    monkeypatch.setattr(twilio_numbers, "account_numbers", lambda: _account(NUMBER))
    assert client.put(f"/businesses/{biz}/number", json={"number": "+15550000000"}).status_code == 422
    assert client.put(f"/businesses/{biz}/number", json={"number": "+1 555 000 1111"}).status_code == 422  # not E.164
    assert client.put(f"/businesses/{uuid.uuid4()}/number", json={"number": NUMBER}).status_code == 404

    def no_creds():
        raise RuntimeError("TWILIO_ACCOUNT_SID / TWILIO_AUTH_TOKEN are not set")

    monkeypatch.setattr(twilio_numbers, "account_numbers", no_creds)
    assert client.put(f"/businesses/{biz}/number", json={"number": NUMBER}).status_code == 503


def test_safe_error_hides_the_request_uri():
    class FakeTwilioError(Exception):
        msg = "Authenticate"
        status = 401

        def __str__(self):
            return "HTTP 401 error: Authenticate https://api.twilio.com/2010-04-01/Accounts/ACsecret/IncomingPhoneNumbers.json"

    assert twilio_numbers.safe_error(FakeTwilioError()) == "Authenticate"
    assert twilio_numbers.safe_error(RuntimeError("TWILIO_ACCOUNT_SID is not set")) == "TWILIO_ACCOUNT_SID is not set"
