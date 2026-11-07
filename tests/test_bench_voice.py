"""The voice comparison harness (scripts/bench_voice.py): scoring and wiring, no real engines."""

import importlib.util
import sys
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location(
    "bench_voice", ROOT / "scripts/bench_voice.py"
)
bv = importlib.util.module_from_spec(spec)
sys.modules["bench_voice"] = bv
spec.loader.exec_module(bv)


def test_wer_basics():
    assert (
        bv.wer("open at five p.m.", "Open at 5 p.m.") == 0.2
    )  # '5' vs 'five' is one error; 'p.m.' is two words on both sides
    assert bv.wer("a b c d", "a b c d") == 0
    assert bv.wer("a b c d", "a x c") == 0.5  # one substitution + one deletion
    assert bv.wer("", "") == 0 and bv.wer("", "x") == 1


def _silence(path: Path, seconds: float = 1.0, rate: int = 8000) -> None:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\0\0" * int(rate * seconds))


def test_cmd_adapters_and_report(tmp_path):
    clip = tmp_path / "hello.wav"
    _silence(clip)
    clip.with_suffix(".txt").write_text("hello there")
    clips = bv.load_clips(tmp_path)
    assert clips == [(clip, "hello there")]
    r = bv.bench_stt(
        f"cmd:{sys.executable} -c \"print('hello their')\" {{wav}}", clips, runs=2
    )
    assert len(r.samples) == 2 and abs(r.samples[0]["wer"] - 0.5) < 1e-9
    assert abs(r.summary()["rtf"]) < 5
    tts_cmd = f"cmd:{sys.executable} -c \"import wave,sys;w=wave.open(sys.argv[2],'wb');w.setnchannels(1);w.setsampwidth(2);w.setframerate(8000);w.writeframes(bytes(16000))\" {{text}} {{out}}"
    t = bv.bench_tts(tts_cmd, ["Hi there."], runs=1)
    assert t.samples[0]["audio_s"] == 1.0
    table = bv.report([r, t])
    assert "| stt |" in table and "| tts |" in table
