"""Call loop tests (app/voice/session.py) with fake STT, TTS and LLM.

Real parts: VAD on the real speech fixture, Postgres (business, conversation, messages)
and `run_tool` (keyword retrieval). Faked: Google STT/TTS (need ADC credentials) and the
LLM stream (so tool calling is deterministic). Live Google/LM Studio runs are manual,
through the voice app at /web/ (push-to-talk) or web/voice-debug.html (VAD).
"""

import asyncio
import uuid
import wave
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.async_helpers import run_async
from sqlalchemy import delete, select

from app.db import tenant_session
from app.db.models import Business, Chunk, Conversation, CustomReply, Message
from app.voice import session as session_mod
from app.voice.session import CallSession, split_sentences

FIXTURES = Path(__file__).parent / "fixtures" / "voice"
RATE = 8000


def _speech() -> bytes:
    with wave.open(str(FIXTURES / "speech_8k.wav")) as w:
        return w.readframes(w.getnframes())


SILENCE_1S = b"\0\0" * RATE


class FakeTranscriber:
    created: list["FakeTranscriber"] = []

    def __init__(self, sample_rate: int) -> None:
        self.audio = bytearray()
        self.cancelled = False
        FakeTranscriber.created.append(self)

    def push(self, pcm: bytes) -> None:
        self.audio += pcm

    def finish(self, timeout: float = 5.0) -> str:
        return "What time do you close on Saturday?"

    def cancel(self) -> None:
        self.cancelled = True


def fake_synthesize(text: str, sample_rate: int) -> bytes:
    return b"\0\0" * sample_rate * 3  # 3 s of "speech" per sentence


def _chunk(content=None, tool_calls=None):
    return SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content=content, tool_calls=tool_calls))])


def _tool_call_delta(index, id=None, name=None, arguments=None):
    return SimpleNamespace(index=index, id=id, function=SimpleNamespace(name=name, arguments=arguments))


class FakeLLM:
    """Round 1: a tool call streamed in pieces. Round 2: an answer in pieces."""

    def __init__(self) -> None:
        self.requests: list[dict] = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    async def _create(self, **kwargs):
        self.requests.append(kwargs)
        if len(self.requests) == 1:
            pieces = [
                _chunk(tool_calls=[_tool_call_delta(0, id="call_1", name="search_business_info", arguments='{"que')]),
                _chunk(tool_calls=[_tool_call_delta(0, arguments='ry": "zebrahours saturday"}')]),
            ]
        else:
            pieces = [_chunk("We close at 5 pm"), _chunk(" on Saturday. Anything"), _chunk(" else?")]

        async def gen():
            for p in pieces:
                yield p

        return gen()


@pytest.fixture
def business():
    bid = uuid.uuid4()
    with tenant_session(bid) as s:
        s.add(Business(id=bid, name="Zebra Spa", timezone="Europe/Riga"))
        s.flush()
        reply = CustomReply(business_id=bid, question="saturday hours?", answer="zebrahours: Saturday 10-17")
        s.add(reply)
        s.flush()
        s.add(Chunk(business_id=bid, custom_reply_id=reply.id, kind="custom_reply",
                    text="zebrahours: Saturday 10-17", content_hash="h"))
    yield bid
    with tenant_session(bid) as s:
        s.execute(delete(Business).where(Business.id == bid))


def _make_session(business_id, llm, monkeypatch):
    monkeypatch.setattr(session_mod, "get_async_chat_client", lambda: llm)
    FakeTranscriber.created = []
    audio_out: list[bytes] = []
    events: list[dict] = []

    async def send_audio(pcm):
        audio_out.append(pcm)

    async def send_event(ev):
        events.append(ev)

    s = CallSession(business_id, RATE, send_audio, send_event, channel_caller="test",
                    transcriber_factory=FakeTranscriber, synthesize=fake_synthesize)
    return s, audio_out, events


async def _feed(s: CallSession, pcm: bytes, chunk: int = 320) -> None:
    for i in range(0, len(pcm), chunk):
        await s.feed(pcm[i:i + chunk])


def test_split_sentences_keeps_unfinished_tail():
    assert split_sentences("We close at 5. Anything") == (["We close at 5."], "Anything")
    assert split_sentences("Yes! Sure? ok") == (["Yes!", "Sure?"], "ok")
    assert split_sentences("no end yet") == ([], "no end yet")


def test_full_turn_with_tool_call(business, monkeypatch):
    llm = FakeLLM()
    s, audio_out, events = _make_session(business, llm, monkeypatch)

    async def run():
        await s.start()
        await s._reply_task  # greeting
        await _feed(s, SILENCE_1S + _speech() + SILENCE_1S)
        await s._reply_task  # the turn
        await s.close()

    run_async(run())

    # STT got the pre-roll and the whole utterance, not just what came after the trigger
    assert len(FakeTranscriber.created) == 1
    assert len(FakeTranscriber.created[0].audio) >= len(_speech()) * 0.8

    replies = [e["text"] for e in events if e["type"] == "reply"]
    assert replies[0] == "Hi, you've reached Zebra Spa. I'm an AI assistant. How can I help?"
    assert replies[1:] == ["We close at 5 pm on Saturday.", "Anything else?"]
    assert {"type": "transcript", "text": "What time do you close on Saturday?"} in events
    assert len(audio_out) == 3  # greeting + 2 sentences

    # the tool ran against this business's data and its result went back to the LLM
    second = llm.requests[1]["messages"]
    assert second[-2]["tool_calls"][0]["function"] == {"name": "search_business_info",
                                                        "arguments": '{"query": "zebrahours saturday"}'}
    assert second[-1]["role"] == "tool" and "zebrahours: Saturday 10-17" in second[-1]["content"]
    assert "Current local time at the business" in second[0]["content"]

    latency = next(e for e in events if e["type"] == "latency")
    assert {"stt_ms", "tool_start_ms", "tool_end_ms", "llm_first_sentence_ms", "tts_first_audio_ms",
            "total_ms"} <= latency.keys()

    with tenant_session(business) as db:
        conv = db.scalar(select(Conversation).where(Conversation.business_id == business))
        assert conv.channel == "voice" and conv.caller == "test" and conv.ended_at is not None
        rows = db.scalars(select(Message).where(Message.business_id == business).order_by(Message.created_at))
        assert [(m.role, m.content) for m in rows] == [
            ("assistant", replies[0]),
            ("user", "What time do you close on Saturday?"),
            ("assistant", "We close at 5 pm on Saturday. Anything else?"),
        ]


def test_barge_in_clears_playback_and_cancels_reply(business, monkeypatch):
    s, audio_out, events = _make_session(business, FakeLLM(), monkeypatch)

    async def run():
        await s.start()
        await s._reply_task  # greeting sent; its 3 s of audio is still "playing"
        await _feed(s, _speech()[:8000])  # caller talks over it
        assert {"type": "clear"} in events
        assert s._playing_until == 0.0
        await s.close()

    run_async(run())
    # the abandoned turn's STT stream was closed on hang-up
    assert FakeTranscriber.created[-1].cancelled


def test_no_barge_in_when_bot_is_silent(business, monkeypatch):
    s, _, events = _make_session(business, FakeLLM(), monkeypatch)

    async def run():
        await s.start()
        await s._reply_task
        s._playing_until = 0.0  # greeting finished playing
        await _feed(s, _speech()[:8000])
        await s.close()

    run_async(run())
    assert {"type": "clear"} not in events


def test_unknown_business_raises(monkeypatch):
    s, _, _ = _make_session(uuid.uuid4(), FakeLLM(), monkeypatch)
    with pytest.raises(LookupError):
        run_async(s.start())


# --- stt.py / tts.py with a fake Google client (no credentials needed) ------------


def test_tts_strips_wav_header_and_checks_rate(monkeypatch):
    import io

    from app.voice import tts

    pcm = bytes(range(256)) * 8

    def wav(rate):
        buf = io.BytesIO()
        with wave.open(buf, "wb") as w:
            w.setnchannels(1), w.setsampwidth(2), w.setframerate(rate)
            w.writeframes(pcm)
        return buf.getvalue()

    for returned_rate in (16000, 8000):
        fake = SimpleNamespace(synthesize_speech=lambda **kw: SimpleNamespace(audio_content=wav(returned_rate)))
        monkeypatch.setattr(tts, "get_tts_client", lambda: fake)
        if returned_rate == 16000:
            assert tts.synthesize("Hello.", 16000) == pcm
        else:
            with pytest.raises(ValueError):
                tts.synthesize("Hello.", 16000)
    assert tts.synthesize("  ", 16000) == b""


def test_stt_collects_final_and_interim_results(monkeypatch):
    from app.voice import stt

    sent: list[bytes] = []

    def streaming_recognize(config, requests):
        for r in requests:  # consumed until finish() closes the stream
            sent.append(r.audio_content)

        def res(text, final):
            return SimpleNamespace(results=[SimpleNamespace(alternatives=[SimpleNamespace(transcript=text)],
                                                            is_final=final)])
        return iter([res("what time", False), res("What time do you close", True), res(" on Saturday", False)])

    monkeypatch.setattr(stt, "get_stt_client", lambda: SimpleNamespace(streaming_recognize=streaming_recognize))
    t = stt.TurnTranscriber(16000)
    t.push(b"ab"), t.push(b""), t.push(b"cd")
    assert t.finish() == "What time do you close on Saturday"
    assert sent == [b"ab", b"cd"]


def test_stt_error_surfaces_in_finish(monkeypatch):
    from app.voice import stt

    def boom(config, requests):
        raise PermissionError("no ADC")

    monkeypatch.setattr(stt, "get_stt_client", lambda: SimpleNamespace(streaming_recognize=boom))
    with pytest.raises(RuntimeError):
        stt.TurnTranscriber(16000).finish()


def test_barge_in_mid_reply_keeps_only_what_was_spoken(business, monkeypatch):
    import time as _time

    def slow_synthesize(text, rate):
        if text.startswith("Anything"):
            _time.sleep(0.3)  # caller interrupts while this sentence is being synthesized
        return b"\0\0" * rate * 3

    s, _, events = _make_session(business, FakeLLM(), monkeypatch)
    s._synthesize = slow_synthesize

    async def run():
        await s.start()
        await s._reply_task
        s._playing_until = 0.0
        await _feed(s, _speech() + SILENCE_1S)
        while not any(e.get("text") == "Anything else?" for e in events):
            await asyncio.sleep(0.01)
        await _feed(s, _speech()[:8000])  # barge in
        await s.close()

    run_async(run())
    assert {"type": "clear"} in events
    with tenant_session(business) as db:
        rows = db.scalars(select(Message).where(Message.business_id == business).order_by(Message.created_at))
        assert [(m.role, m.content) for m in rows][1:] == [
            ("user", "What time do you close on Saturday?"),
            ("assistant", "We close at 5 pm on Saturday."),  # the unspoken sentence isn't stored
        ]
    assert s._history[-1] == {"role": "assistant", "content": "We close at 5 pm on Saturday."}


# --- push-to-talk (turn_detection="manual") and the business picker ------------------


def test_manual_turns_ignore_vad_and_answer_on_release(business, monkeypatch):
    s, audio_out, events = _make_session(business, FakeLLM(), monkeypatch)
    s.turn_detection = "manual"

    async def run():
        await s.start()
        await s._reply_task
        s._playing_until = 0.0
        await _feed(s, _speech())  # talking without pressing: ignored, VAD not used
        assert FakeTranscriber.created == []
        await s.start_turn()
        await s.start_turn()  # repeated press: same turn
        await _feed(s, _speech())
        s.end_turn()  # release: answered at once, no 500 ms silence needed
        await s._reply_task
        s.end_turn()  # stray release: no-op
        await s.close()

    run_async(run())
    assert len(FakeTranscriber.created) == 1
    assert len(FakeTranscriber.created[0].audio) == len(_speech())
    assert [e["text"] for e in events if e["type"] == "reply"][1:] == ["We close at 5 pm on Saturday.", "Anything else?"]


def test_manual_press_during_reply_barges_in(business, monkeypatch):
    s, _, events = _make_session(business, FakeLLM(), monkeypatch)
    s.turn_detection = "manual"

    async def run():
        await s.start()
        await s._reply_task  # greeting audio still "playing"
        await s.start_turn()
        assert {"type": "clear"} in events
        await s.close()

    run_async(run())


def test_invalid_turn_detection_rejected():
    with pytest.raises(ValueError):
        CallSession(uuid.uuid4(), RATE, None, None, turn_detection="push")


def test_businesses_endpoint_uses_real_name_over_placeholder(business):
    from fastapi.testclient import TestClient

    from app.api.businesses import display_name
    from app.db.models import BusinessProfile
    from app.main import app

    assert display_name("Zebra Spa", "business_name") == "Zebra Spa"
    assert display_name("(pending)", "Bathhouse") == "Bathhouse"
    assert display_name("(pending)", "  ") == "(pending)"

    with tenant_session(business) as db:
        db.add(BusinessProfile(business_id=business, name="business_name"))
    rows = TestClient(app).get("/businesses").json()
    assert {
        "id": str(business), "name": "Zebra Spa", "website": None, "domain": None,
        "conversation_count": 0, "default_location": None,
    } in rows
