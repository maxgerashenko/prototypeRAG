"""Google Text-to-Speech for the voice pipeline (plan/03-voice-channel.md).

Speech runs on Google's APIs also locally — no local speech models. Native
`google-cloud-texttospeech` SDK (allowed in the voice module only), authenticated with
Application Default Credentials, so there is no key handling here.
"""

import functools
import io
import logging
import wave

import google.cloud.texttospeech as texttospeech

from app.config import get_settings

log = logging.getLogger(__name__)


@functools.lru_cache(maxsize=1)
def get_tts_client() -> texttospeech.TextToSpeechClient:
    """Created on first use, so importing this module never needs credentials."""
    return texttospeech.TextToSpeechClient()


def warm_up() -> None:
    """T4: create the client and open its connection at app startup -- the first request
    per process took ≈ 700 ms vs ≈ 250 ms warm, and it used to land on a caller's
    greeting. Synthesizes a one-word text (a few characters of quota). Best effort: no
    credentials (tests, CI) only means the first call stays slow."""
    try:
        synthesize("Hi.", 8000)
    except Exception:
        log.warning("TTS warm-up failed; the first greeting will be slower", exc_info=True)


def synthesize(text: str, sample_rate: int) -> bytes:
    """Synthesize `text` to raw mono PCM16 LE at `sample_rate` Hz (no WAV header)."""
    if not text.strip():
        return b""

    settings = get_settings()
    response = get_tts_client().synthesize_speech(
        input=texttospeech.SynthesisInput(text=text),
        voice=texttospeech.VoiceSelectionParams(language_code=settings.voice_language, name=settings.tts_voice),
        audio_config=texttospeech.AudioConfig(
            audio_encoding=texttospeech.AudioEncoding.LINEAR16, sample_rate_hertz=sample_rate
        ),
    )

    # LINEAR16 comes back as a whole WAV file; parse the header instead of assuming 44 bytes
    with wave.open(io.BytesIO(response.audio_content), "rb") as wav:
        if wav.getnchannels() != 1 or wav.getsampwidth() != 2:
            raise ValueError("TTS response is not mono 16-bit PCM")
        if wav.getframerate() != sample_rate:
            raise ValueError(f"TTS returned {wav.getframerate()} Hz, requested {sample_rate} Hz")
        return wav.readframes(wav.getnframes())


def pcm16_duration_s(pcm: bytes, sample_rate: int) -> float:
    """Duration in seconds of raw mono PCM16 audio."""
    return len(pcm) / 2 / sample_rate
