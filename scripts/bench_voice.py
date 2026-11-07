"""Compare voice stacks side by side: STT accuracy (WER) and latency, TTS latency, cost.

Candidates are adapters picked by name on the command line, so a new stack (Parakeet,
Piper, Fish Audio, ...) is one `cmd:` template away and needs no code change.

STT: --stt google faster-whisper:small "cmd:python my_parakeet.py {wav}"
TTS: --tts google piper:/path/en_US-lessac-medium.onnx "cmd:fish-say {text} {out}"
Clips: a directory of `<name>.wav` + `<name>.txt` (reference text), default
tests/fixtures/voice_bench. A clip without .txt is timed but not scored.
--roundtrip: also synthesizes every sentence in --sentences with each TTS and feeds that
audio to each STT (WER without recordings; biased towards synthetic voices, so only a
sanity check -- real phone audio from Twilio is the real test).

    uv run python scripts/bench_voice.py --stt google faster-whisper:small --tts google
    uv run python scripts/bench_voice.py --list     # candidates, setup, prices
Needs per candidate: google = ADC; faster-whisper = `uv pip install faster-whisper`;
piper = `piper` CLI + a voice file. See plan/03-voice-channel.md "Test on Mac".
"""

import argparse
import re
import shlex
import statistics
import subprocess
import sys
import tempfile
import time
import wave
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CLIPS = ROOT / "tests/fixtures/voice_bench"

# approximate list prices, re-check before deciding (DEC-33: pay-per-use only)
PRICES = {
    "google": {
        "stt_per_min": 0.016,
        "tts_per_1m_chars": 16.0,
    },  # STT v1 standard, TTS Neural2
}
LOCAL = {
    "stt_per_min": 0.0,
    "tts_per_1m_chars": 0.0,
}  # own hardware, electricity ignored

CANDIDATES = """\
google           STT+TTS  ADC; what the app uses today
faster-whisper:M STT      M = tiny|base|small|medium|large-v3; offline, CPU or GPU
piper:VOICE.onnx TTS      `piper` CLI on PATH; offline
cmd:TEMPLATE     STT/TTS  any program. STT: prints transcript, {wav} = input file.
                          TTS: writes a WAV to {out}, {text} = quoted sentence.
                          Use it for Parakeet TDT, Qwen3-TTS, Fish Audio (API or weights), etc.
"""


def normalize(text: str) -> list[str]:
    """Lower-case words without punctuation; "5:00 p.m." -> "5 00 p m" on both sides."""
    return re.sub(r"[^a-z0-9' ]+", " ", text.lower().replace("-", " ")).split()


def wer(reference: str, hypothesis: str) -> float:
    """Word error rate = word edit distance / reference length (0 = perfect, may exceed 1)."""
    ref, hyp = normalize(reference), normalize(hypothesis)
    if not ref:
        return 0.0 if not hyp else 1.0
    prev = list(range(len(hyp) + 1))
    for i, r in enumerate(ref, 1):
        cur = [i]
        for j, h in enumerate(hyp, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (r != h)))
        prev = cur
    return prev[-1] / len(ref)


def wav_seconds(path: Path) -> float:
    with wave.open(str(path)) as w:
        return w.getnframes() / w.getframerate()


@dataclass
class Result:
    candidate: str
    kind: str  # "stt" | "tts"
    samples: list[dict] = field(
        default_factory=list
    )  # {name, seconds, audio_s, wer?, text}

    def summary(self) -> dict:
        times = [s["seconds"] for s in self.samples]
        audio = sum(s["audio_s"] for s in self.samples)
        wers = [s["wer"] for s in self.samples if "wer" in s]
        out = {
            "candidate": self.candidate,
            "kind": self.kind,
            "n": len(times),
            "p50_s": statistics.median(times) if times else 0.0,
            "max_s": max(times, default=0.0),
            "rtf": sum(times) / audio if audio else 0.0,  # <1 = faster than real time
        }
        if wers:
            out["wer"] = statistics.mean(wers)
        return out


# ---- adapters: STT = fn(wav_path) -> text, TTS = fn(text, out_wav_path) -> None ----


def make_stt(spec: str):
    name, _, arg = spec.partition(":")
    if name == "google":

        def run(wav: Path) -> str:
            from google.cloud import speech

            from app.config import get_settings
            from app.voice.google_auth import require_credentials

            require_credentials()
            with wave.open(str(wav)) as w:
                rate, pcm = w.getframerate(), w.readframes(w.getnframes())
            model = "phone_call" if rate <= 8000 else get_settings().stt_model
            r = speech.SpeechClient().recognize(
                config=speech.RecognitionConfig(
                    encoding=speech.RecognitionConfig.AudioEncoding.LINEAR16,
                    sample_rate_hertz=rate,
                    language_code=get_settings().voice_language,
                    model=model,
                    enable_automatic_punctuation=True,
                ),
                audio=speech.RecognitionAudio(content=pcm),
            )
            return " ".join(x.alternatives[0].transcript for x in r.results)

        return run
    if name == "faster-whisper":
        cache: dict = {}

        def run(wav: Path) -> str:
            if "m" not in cache:
                from faster_whisper import WhisperModel

                cache["m"] = WhisperModel(arg or "small", compute_type="int8")
            segs, _ = cache["m"].transcribe(str(wav), language="en")
            return " ".join(s.text.strip() for s in segs)

        return run
    if name == "cmd":

        def run(wav: Path) -> str:
            cmd = [p.replace("{wav}", str(wav)) for p in shlex.split(arg)]
            return subprocess.run(
                cmd, capture_output=True, text=True, check=True
            ).stdout.strip()

        return run
    raise SystemExit(f"unknown STT candidate {spec!r}; see --list")


def make_tts(spec: str):
    name, _, arg = spec.partition(":")
    if name == "google":

        def run(text: str, out: Path) -> None:
            from app.voice.tts import synthesize

            with wave.open(str(out), "wb") as w:
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(16000)
                w.writeframes(synthesize(text, 16000))

        return run
    if name == "piper":

        def run(text: str, out: Path) -> None:
            subprocess.run(
                ["piper", "--model", arg, "--output_file", str(out)],
                input=text,
                text=True,
                check=True,
                capture_output=True,
            )

        return run
    if name == "cmd":

        def run(text: str, out: Path) -> None:
            cmd = [
                p.replace("{text}", text).replace("{out}", str(out))
                for p in shlex.split(arg)
            ]
            subprocess.run(cmd, check=True, capture_output=True)

        return run
    raise SystemExit(f"unknown TTS candidate {spec!r}; see --list")


# ---- benchmark ----


def load_clips(directory: Path) -> list[tuple[Path, str | None]]:
    clips = []
    for wav in sorted(directory.glob("*.wav")):
        ref = wav.with_suffix(".txt")
        clips.append((wav, ref.read_text().strip() if ref.exists() else None))
    return clips


def bench_stt(spec: str, clips: list[tuple[Path, str | None]], runs: int) -> Result:
    fn, res = make_stt(spec), Result(spec, "stt")
    if clips:
        fn(
            clips[0][0]
        )  # warm-up: model load / first-call setup is not what a caller waits for
    for wav, ref in clips:
        for _ in range(runs):
            t0 = time.perf_counter()
            text = fn(wav)
            s = {
                "name": wav.name,
                "seconds": time.perf_counter() - t0,
                "audio_s": wav_seconds(wav),
                "text": text,
            }
            if ref is not None:
                s["wer"] = wer(ref, text)
            res.samples.append(s)
    return res


def bench_tts(
    spec: str, sentences: list[str], runs: int, keep: Path | None = None
) -> Result:
    fn, res = make_tts(spec), Result(spec, "tts")
    with tempfile.TemporaryDirectory() as tmp:
        if sentences:
            fn("Hello.", Path(tmp) / "warm.wav")
        for i, text in enumerate(sentences):
            for _ in range(runs):
                out = (keep or Path(tmp)) / f"{spec.split(':')[0]}_{i}.wav"
                t0 = time.perf_counter()
                fn(text, out)
                res.samples.append(
                    {
                        "name": text[:40],
                        "seconds": time.perf_counter() - t0,
                        "audio_s": wav_seconds(out),
                        "chars": len(text),
                        "text": text,
                        "wav": out,
                    }
                )
    return res


def roundtrip(stt_spec: str, tts_spec: str, sentences: list[str]) -> Result:
    """Synthesize with tts_spec, transcribe with stt_spec, score against the sentence."""
    synth, fn, res = (
        make_tts(tts_spec),
        make_stt(stt_spec),
        Result(f"{stt_spec} <- {tts_spec}", "stt"),
    )
    with tempfile.TemporaryDirectory() as tmp:
        for i, text in enumerate(sentences):
            wav = Path(tmp) / f"{i}.wav"
            synth(text, wav)
            t0 = time.perf_counter()
            heard = fn(wav)
            res.samples.append(
                {
                    "name": text[:40],
                    "seconds": time.perf_counter() - t0,
                    "audio_s": wav_seconds(wav),
                    "text": heard,
                    "wer": wer(text, heard),
                }
            )
    return res


def report(results: list[Result]) -> str:
    lines = [
        "| candidate | kind | n | p50 s | max s | RTF | WER | approx $ per 1k units |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for r in results:
        s = r.summary()
        prices = PRICES.get(r.candidate.split(" <- ")[0].split(":")[0], LOCAL)
        # price per 1000 minutes of audio (STT) or per 1000 characters (TTS)
        per_k = (
            prices["stt_per_min"] * 1000
            if r.kind == "stt"
            else prices["tts_per_1m_chars"] / 1000
        )
        w = f"{s['wer']:.1%}" if "wer" in s else "-"
        lines.append(
            f"| {s['candidate']} | {s['kind']} | {s['n']} | {s['p50_s']:.2f} | {s['max_s']:.2f} | "
            f"{s['rtf']:.2f} | {w} | {per_k:.2f} {'/1k min' if r.kind == 'stt' else '/1k chars'} |"
        )
    lines.append(
        "\nRTF < 1 is faster than real time. TTS 'seconds' is time to the whole file, not to first "
        "audio: streaming first-chunk latency (what a caller hears) needs the Mac/live test."
    )
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--stt", nargs="*", default=[])
    ap.add_argument("--tts", nargs="*", default=[])
    ap.add_argument("--clips", type=Path, default=DEFAULT_CLIPS)
    ap.add_argument("--sentences", type=Path, default=DEFAULT_CLIPS / "sentences.txt")
    ap.add_argument("--roundtrip", action="store_true")
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--list", action="store_true")
    args = ap.parse_args()
    if args.list:
        print(CANDIDATES, "Prices:", PRICES, "(approx.)")
        return
    sentences = (
        [x.strip() for x in args.sentences.read_text().splitlines() if x.strip()]
        if args.sentences.exists()
        else []
    )
    clips = load_clips(args.clips)
    results = [bench_stt(s, clips, args.runs) for s in args.stt] + [
        bench_tts(t, sentences, args.runs) for t in args.tts
    ]
    if args.roundtrip:
        results += [roundtrip(s, t, sentences) for s in args.stt for t in args.tts]
    print(report(results))


if __name__ == "__main__":
    sys.path.insert(0, str(ROOT))
    main()
