"""Live phone calls for the web app (DEC-46): the in-memory feed of `app/voice/call_feed.py` and
what a Twilio call over /voice/ws publishes to it. Fake STT/TTS/LLM, real Postgres, the
in-memory socket of tests/test_twilio.py.
"""

import asyncio
import functools
import uuid

from app.voice import call_feed as live_mod
from app.voice import session as session_mod
from app.voice import twilio_routes, ws as ws_mod
from app.voice.call_feed import LiveCalls, live_calls, sse
from app.voice.session import CallSession
from tests.async_helpers import run_async
from tests.test_twilio import (  # noqa: F401 (fixtures)
    AnswerLLM, FakeSocket, _media_frames, _start, _until, business, settings,
)
from tests.test_voice_session import SILENCE_1S, FakeTranscriber, _speech


def _drain(queue: asyncio.Queue) -> list[dict]:
    out = []
    while not queue.empty():
        out.append(queue.get_nowait())
    return out


# --- the feed -----------------------------------------------------------------------


def test_watcher_gets_snapshot_then_call_messages():
    live = LiveCalls()
    q = live.watch()
    live.start("MZ1", "b1", "c1", "+15550001111")
    live.event("MZ1", {"type": "transcript", "text": "Hi"})
    live.event("MZ_UNKNOWN", {"type": "reply", "text": "dropped"})
    live.end("MZ1")
    live.end("MZ1")  # twice: one call_ended only

    msgs = _drain(q)
    assert [m["type"] for m in msgs] == ["snapshot", "call_started", "call_event", "call_ended"]
    assert msgs[0] == {"type": "snapshot", "calls": []}
    call = msgs[1]["call"]
    assert {k: call[k] for k in ("id", "business_id", "conversation_id", "caller")} == {
        "id": "MZ1", "business_id": "b1", "conversation_id": "c1", "caller": "+15550001111"}
    assert msgs[2]["call_id"] == "MZ1" and msgs[2]["event"]["text"] == "Hi" and msgs[2]["event"]["t"] > 0


def test_page_opened_mid_call_catches_up():
    live = LiveCalls()
    live.start("MZ1", "b1", "c1", None)
    live.event("MZ1", {"type": "reply", "text": "Hello"})
    snap = _drain(live.watch())
    assert len(snap) == 1 and [c["id"] for c in snap[0]["calls"]] == ["MZ1"]
    assert [e["text"] for e in snap[0]["calls"][0]["events"]] == ["Hello"]

    live.end("MZ1")
    assert _drain(live.watch())[0]["calls"] == []


def test_slow_page_gets_a_fresh_snapshot(monkeypatch):
    monkeypatch.setattr(live_mod, "QUEUE_SIZE", 3)
    live = LiveCalls()
    q = live.watch()  # holds the snapshot
    live.start("MZ1", "b1", None, None)
    live.event("MZ1", {"type": "reply", "text": "one"})
    live.event("MZ1", {"type": "reply", "text": "two"})  # full -> start over
    msgs = _drain(q)
    assert [m["type"] for m in msgs] == ["snapshot"]
    assert [e["text"] for e in msgs[0]["calls"][0]["events"]] == ["one", "two"]


def test_unwatched_page_gets_nothing():
    live = LiveCalls()
    q = live.watch()
    live.unwatch(q)
    live.start("MZ1", "b1", None, None)
    assert [m["type"] for m in _drain(q)] == ["snapshot"]


def test_sse_lines_and_keepalive():
    async def run():
        q: asyncio.Queue = asyncio.Queue()
        q.put_nowait({"type": "snapshot", "calls": []})
        gen = sse(q, keepalive_s=0.01)
        first = await anext(gen)
        second = await anext(gen)
        await gen.aclose()
        return first, second

    first, second = run_async(run())
    assert first == 'data: {"type": "snapshot", "calls": []}\n\n'
    assert second == ": keepalive\n\n"


# --- a Twilio call publishes to the feed ------------------------------------------------


def greeting_long_rest_short(text: str, sample_rate: int) -> bytes:
    """Greeting 3 s (long enough to barge into), answer sentences 0.2 s (the test waits them out)."""
    seconds = 3 if text.startswith("Hi, I am an AI") else 0.2
    return b"\0\0" * int(sample_rate * seconds)


def test_phone_call_shows_up_live(settings, business, monkeypatch):
    session_mod._greeting_audio.clear()  # the opening text is the same for every business (DEC-53)
    monkeypatch.setattr(session_mod, "get_async_chat_client", lambda: AnswerLLM())
    monkeypatch.setattr(ws_mod, "CallSession", functools.partial(
        CallSession, transcriber_factory=FakeTranscriber, synthesize=greeting_long_rest_short))
    call_sid = "CA" + uuid.uuid4().hex

    async def scenario():
        q = live_calls.watch()
        try:
            sock = FakeSocket()
            handler = asyncio.create_task(ws_mod.twilio_media_stream(sock))
            sock.put(_start(business, call_sid, twilio_routes.stream_token(business, call_sid)))
            await _until(lambda: any(m["event"] == "media" for m in sock.sent))  # greeting
            for frame in _media_frames(_speech() + SILENCE_1S):  # barge-in, then a question
                sock.put(frame)
            published = []

            def answered_and_quiet():
                published.extend(_drain(q))
                evs = [m["event"] for m in published if m["type"] == "call_event"]
                replies = [e for e in evs if e["type"] == "reply"]
                return len(replies) >= 3 and evs[-1] == {**evs[-1], "type": "playback", "active": False}

            await _until(answered_and_quiet)
            sock.put({"event": "stop", "streamSid": "MZ1"})
            await asyncio.wait_for(handler, 5)
            published.extend(_drain(q))
            return published
        finally:
            live_calls.unwatch(q)

    msgs = run_async(scenario())
    assert msgs[0]["type"] == "snapshot"
    assert msgs[1]["type"] == "call_started"
    call = msgs[1]["call"]
    assert call["id"] == "MZ1" and call["business_id"] == str(business)
    assert call["caller"] == "+15559998888" and call["conversation_id"]
    assert msgs[-1] == {"type": "call_ended", "call_id": "MZ1", "t": msgs[-1]["t"]}

    events = [m["event"] for m in msgs[2:-1]]
    assert all(m["call_id"] == "MZ1" for m in msgs[2:-1])
    seen = [(e["type"], e.get("text", e.get("active"))) for e in events if e["type"] != "latency"]
    assert seen == [
        ("reply", "Hi, I am an AI assistant."),
        ("playback", True),
        ("clear", None),
        ("playback", False),  # barge-in stops the bot at once
        ("transcript", "What time do you close on Saturday?"),
        ("reply", "We close at 5 pm on Saturday."),
        ("playback", True),
        ("reply", "Anything else?"),  # still speaking: no playback off/on in between
        ("playback", False),  # the answer's audio ran out
    ]
    assert any(e["type"] == "latency" for e in events)
    assert "MZ1" not in [c["id"] for c in live_calls.snapshot()["calls"]]
