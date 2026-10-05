"""Voice pipeline tests (plan/03-voice-channel.md): audio formats, VAD, shared tools.

Audio/VAD tests need neither Postgres nor LM Studio. The speech fixture is real speech
(macOS `say`, 8 kHz PCM16 — "What time do you close on Saturday?"), so the VAD is
tested on speech rather than synthetic tones. `audioop` (stdlib until Python 3.13)
serves only as an independent G.711 reference here; app code doesn't use it.
"""

import uuid
import warnings
import wave
from pathlib import Path

import numpy as np
import pytest
from sqlalchemy import delete

from app.voice.audio import TWILIO_SAMPLE_RATE, pcm16_to_ulaw, resample_pcm16, ulaw_to_pcm16
from app.voice.tools import TOOLS, run_tool
from app.voice.vad import TurnDetector, VadEvent

FIXTURES = Path(__file__).parent / "fixtures" / "voice"

with warnings.catch_warnings():
    warnings.simplefilter("ignore", DeprecationWarning)
    try:
        import audioop
    except ImportError:  # Python >= 3.13
        audioop = None


def _speech_8k() -> bytes:
    with wave.open(str(FIXTURES / "speech_8k.wav")) as w:
        assert (w.getframerate(), w.getnchannels(), w.getsampwidth()) == (8000, 1, 2)
        return w.readframes(w.getnframes())


def _tone(freq: float, rate: int, seconds: float = 0.5, amp: float = 10000) -> bytes:
    t = np.arange(int(rate * seconds)) / rate
    return (amp * np.sin(2 * np.pi * freq * t)).astype("<i2").tobytes()


def _samples(pcm: bytes) -> np.ndarray:
    return np.frombuffer(pcm, dtype="<i2").astype(np.float64)


def _rms(x: np.ndarray) -> float:
    return float(np.sqrt(np.mean(x**2)))


# --- audio.py ---------------------------------------------------------------------


def test_ulaw_decode_known_values():
    assert _samples(ulaw_to_pcm16(bytes([0xFF, 0x7F, 0x00, 0x80]))).tolist() == [0, 0, -32124, 32124]


@pytest.mark.skipif(audioop is None, reason="audioop reference needs Python < 3.13")
def test_ulaw_decode_matches_audioop_for_every_byte():
    all_bytes = bytes(range(256))
    assert ulaw_to_pcm16(all_bytes) == audioop.ulaw2lin(all_bytes, 2)


@pytest.mark.skipif(audioop is None, reason="audioop reference needs Python < 3.13")
def test_ulaw_encode_matches_audioop_for_every_int16():
    """Positive samples: identical codes. Negative samples: audioop truncates to 14 bits
    first (`sample >> 2`, which floors negatives — the ITU reference), ours is the
    sign-symmetric 16-bit variant; they may pick the neighbouring code at a step
    boundary (381 of 32768 values), but ours is never further from the input."""
    x = np.arange(-32768, 32768, dtype="<i2")
    ours = np.frombuffer(pcm16_to_ulaw(x.tobytes()), np.uint8)
    ref = np.frombuffer(audioop.lin2ulaw(x.tobytes(), 2), np.uint8)
    assert (ours[x >= 0] == ref[x >= 0]).all()
    differ = ours != ref
    assert (np.abs(ours[differ].astype(int) - ref[differ]) == 1).all()
    err_ours = np.abs(_samples(ulaw_to_pcm16(ours.tobytes())) - x)
    err_ref = np.abs(_samples(ulaw_to_pcm16(ref.tobytes())) - x)
    assert (err_ours <= err_ref).all()


def test_ulaw_roundtrip_every_code():
    codes = bytes(range(256))
    back = pcm16_to_ulaw(ulaw_to_pcm16(codes))
    expected = bytes(0xFF if c == 0x7F else c for c in codes)  # both mean zero
    assert back == expected


def test_ulaw_on_real_speech_keeps_telephone_quality():
    pcm = _speech_8k()
    back = _samples(ulaw_to_pcm16(pcm16_to_ulaw(pcm)))
    orig = _samples(pcm)
    snr_db = 20 * np.log10(_rms(orig) / _rms(orig - back))
    assert snr_db > 30  # G.711 is ~38 dB on speech


def test_resample_identity_and_empty():
    pcm = _tone(440, 8000)
    assert resample_pcm16(pcm, 8000, 8000) == pcm
    assert resample_pcm16(b"", 8000, 16000) == b""


@pytest.mark.parametrize(("src", "dst"), [(8000, 16000), (16000, 8000), (24000, 8000), (48000, 16000)])
def test_resample_length_and_pitch(src, dst):
    out = _samples(resample_pcm16(_tone(440, src), src, dst))
    assert len(out) == round(len(_tone(440, src)) // 2 * dst / src)
    spectrum = np.abs(np.fft.rfft(out))
    peak_hz = np.argmax(spectrum) * dst / len(out)
    assert abs(peak_hz - 440) < 5


def test_downsample_filters_out_aliasing_tones():
    # 6 kHz can't exist at 8 kHz (Nyquist 4 kHz); without the low-pass it would fold to 2 kHz.
    kept = _samples(resample_pcm16(_tone(1000, 16000), 16000, 8000))
    removed = _samples(resample_pcm16(_tone(6000, 16000), 16000, 8000))
    assert _rms(kept[100:-100]) > 0.9 * 10000 / np.sqrt(2)
    assert _rms(removed[100:-100]) < 0.05 * 10000 / np.sqrt(2)


# --- vad.py -----------------------------------------------------------------------


def _feed_all(
    det: TurnDetector, pcm: bytes, chunk: int, speech: bool = False
) -> list[tuple[float, VadEvent]]:
    """Feed 8 kHz `pcm` in `chunk`-byte pieces; returns (seconds at the chunk end, event).
    `speech` events (the in-speech audio) are left out unless asked for."""
    events = []
    for i in range(0, len(pcm), chunk):
        for ev in det.feed(pcm[i : i + chunk]):
            if speech or ev.kind != "speech":
                events.append((min(i + chunk, len(pcm)) / 2 / 8000, ev))
    return events


def _call_audio() -> tuple[bytes, float, float]:
    """1 s silence + real speech + 1.5 s silence; returns audio and speech start/end times."""
    speech = _speech_8k()
    silence_before, silence_after = b"\0\0" * 8000, b"\0\0" * 12000
    start = 1.0
    end = start + len(speech) / 2 / 8000
    return silence_before + speech + silence_after, start, end


def test_vad_rejects_unsupported_settings():
    for kwargs in ({"sample_rate": 22050}, {"frame_ms": 25}, {"aggressiveness": 4}):
        with pytest.raises(ValueError):
            TurnDetector(**kwargs)


def test_vad_silence_gives_no_events():
    det = TurnDetector()
    assert det.feed(b"\0\0" * 8000 * 3) == []
    assert not det.in_speech


def test_vad_real_speech_gives_one_turn_with_end_within_budget():
    audio, speech_start, speech_end = _call_audio()
    det = TurnDetector(end_silence_ms=500)
    events = _feed_all(det, audio, 320)  # Twilio-sized 20 ms frames
    assert [e.kind for _, e in events] == ["speech_start", "speech_end"]
    (t_start, _), (t_end, _) = events
    assert speech_start <= t_start < speech_start + 0.5
    # end fires after the 500 ms silence window, and not much later (latency budget)
    assert speech_end + 0.5 <= t_end + 0.05 < speech_end + 1.0


def test_vad_speech_start_carries_preroll():
    audio, _, _ = _call_audio()
    det = TurnDetector(frame_ms=20, start_ms=160, preroll_ms=300)
    start_event = next(e for _, e in _feed_all(det, audio, 320) if e.kind == "speech_start")
    frame_bytes = 320
    assert len(start_event.audio) == (300 // 20 + 160 // 20) * frame_bytes
    assert start_event.audio[:2] == b"\0\0"  # pre-roll reaches back into the silence


def test_vad_odd_chunk_sizes_give_same_events_as_frame_sized_chunks():
    audio, _, _ = _call_audio()
    reference = [e for _, e in _feed_all(TurnDetector(), audio, 320)]
    for chunk in (1, 333, 1001):
        assert [e for _, e in _feed_all(TurnDetector(), audio, chunk)] == reference


def _turn_audio(events: list[tuple[float, VadEvent]]) -> list[bytes]:
    """Audio a caller of feed() would stream to STT, one entry per turn."""
    turns: list[bytes] = []
    for _, ev in events:
        if ev.kind == "speech_start":
            turns.append(ev.audio)
        elif ev.kind == "speech":
            turns[-1] += ev.audio
    return turns


def test_vad_turn_audio_is_contiguous_for_any_chunk_size():
    # B4: with chunks larger than one frame, the audio after the trigger frame in the
    # same chunk used to be dropped, clipping the start of the turn
    audio, _, _ = _call_audio()
    reference = _turn_audio(_feed_all(TurnDetector(), audio, 320, speech=True))
    assert len(reference) == 1
    assert reference[0] in audio  # one gap-free slice of the input
    assert len(reference[0]) > len(_speech_8k()) // 2
    for chunk in (1, 333, 1001, 3200, len(audio)):
        assert _turn_audio(_feed_all(TurnDetector(), audio, chunk, speech=True)) == reference


def test_vad_works_on_twilio_ulaw_audio():
    audio, _, _ = _call_audio()
    phone_audio = ulaw_to_pcm16(pcm16_to_ulaw(audio))  # what we get back from Twilio
    assert [e.kind for _, e in _feed_all(TurnDetector(), phone_audio, 320)] == ["speech_start", "speech_end"]


def test_vad_reset_mid_speech():
    det = TurnDetector()
    det.feed(_speech_8k()[:16000])  # 1 s into the sentence
    assert det.in_speech
    det.reset()
    assert not det.in_speech
    assert det.feed(b"\0\0" * 8000) == []  # no stray speech_end after reset


# --- tools.py (needs Postgres; no LLM call) --------------------------------------


def test_tool_definitions_are_openai_function_schema():
    names = [t["function"]["name"] for t in TOOLS]
    assert "search_business_info" in names
    for t in TOOLS:
        assert t["type"] == "function"
        assert t["function"]["parameters"]["type"] == "object"


def test_run_tool_rejects_unknown_tool_and_bad_arguments():
    bid = uuid.uuid4()  # never reaches the DB on these paths
    assert run_tool(bid, "drop_tables", {}).startswith("Error")
    assert run_tool(bid, "search_business_info", "{not json").startswith("Error")
    assert run_tool(bid, "search_business_info", {"query": "  "}).startswith("Error")
    assert run_tool(bid, "search_business_info", '{"q": "hours"}').startswith("Error")


def test_search_business_info_only_sees_its_own_business():
    from app.db import tenant_session
    from app.db.models import Business, Chunk, CustomReply

    a, b = uuid.uuid4(), uuid.uuid4()
    with tenant_session(a) as s:
        s.add_all([Business(id=a, name="A"), Business(id=b, name="B")])
    try:
        with tenant_session(b) as s:
            reply = CustomReply(business_id=b, question="parking?", answer="zebraparking behind the shop")
            s.add(reply)
            s.flush()
            s.add(Chunk(business_id=b, custom_reply_id=reply.id, kind="custom_reply",
                        text="zebraparking behind the shop", content_hash="h"))
        # keyword path finds it for B, never for A (vector path is skipped: no embeddings)
        assert "zebraparking" in run_tool(b, "search_business_info", {"query": "zebraparking"})
        assert "zebraparking" not in run_tool(a, "search_business_info", {"query": "zebraparking"})
    finally:
        with tenant_session(a) as s:
            s.execute(delete(Business).where(Business.id.in_([a, b])))
