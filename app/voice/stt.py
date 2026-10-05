"""Google Speech-to-Text streaming for ONE caller turn (plan/03-voice-channel.md).

Our VAD decides when the caller starts and stops speaking. On speech start the call
loop creates a `TurnTranscriber`, pushes PCM16 into it while the caller speaks, and on
speech end calls `finish()`. Recognition runs *while* the caller speaks, so the final
text is ready ~200–300 ms after the audio ends instead of after a whole-utterance
upload. Native `google-cloud-speech` v1 SDK (voice module only), auth via Application
Default Credentials — no key handling here.
"""

import functools
import logging
import queue
import threading
from collections.abc import Iterator

from google.cloud import speech

from app.config import get_settings
from app.voice.google_auth import require_credentials

log = logging.getLogger(__name__)

# V3: the final result normally lands 90-180 ms after the audio ends (live check
# 2026-10-03); waiting longer than this only makes the caller wait for the same interim text
FINISH_TIMEOUT_S = 2.0


@functools.lru_cache(maxsize=1)
def get_stt_client() -> speech.SpeechClient:
    """Created on first use, so importing this module never needs credentials."""
    require_credentials()  # fails at once if ADC is missing, not after ~3 s each time (V7)
    return speech.SpeechClient()


class TurnTranscriber:
    """One streaming recognition per turn: a background thread consumes the gRPC
    response stream while `push()` feeds the request stream through a queue."""

    def __init__(self, sample_rate: int) -> None:
        self._audio: queue.Queue[bytes | None] = queue.Queue()
        self._final_parts: list[str] = []
        self._interim: str = ""
        self._error: BaseException | None = None

        settings = get_settings()
        self._streaming_config = speech.StreamingRecognitionConfig(
            config=speech.RecognitionConfig(
                encoding=speech.RecognitionConfig.AudioEncoding.LINEAR16,
                sample_rate_hertz=sample_rate,
                language_code=settings.voice_language,
                model=settings.stt_model,
                enable_automatic_punctuation=True,
            ),
            interim_results=True,
            single_utterance=False,
        )
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def push(self, pcm: bytes) -> None:
        if pcm:
            self._audio.put(pcm)

    def _requests(self) -> Iterator[speech.StreamingRecognizeRequest]:
        while True:
            chunk = self._audio.get()
            if chunk is None:
                return
            yield speech.StreamingRecognizeRequest(audio_content=chunk)

    def _run(self) -> None:
        try:
            # v1 helper: config goes separately, the request iterator yields only audio
            responses = get_stt_client().streaming_recognize(
                config=self._streaming_config, requests=self._requests()
            )
            for response in responses:
                for result in response.results:
                    if not result.alternatives:
                        continue
                    text = result.alternatives[0].transcript.strip()
                    if result.is_final:
                        self._final_parts.append(text)
                        self._interim = ""
                    else:
                        self._interim = text
        except BaseException as exc:  # never raise from the thread; finish() reports it
            self._error = exc

    def finish(self, timeout: float = FINISH_TIMEOUT_S) -> str:
        """End the audio stream and wait for the final transcript (blocking)."""
        self._audio.put(None)
        self._thread.join(timeout)
        if self._thread.is_alive():
            log.warning("STT final result not in after %.1fs, using what arrived so far", timeout)
        if self._error is not None:
            raise RuntimeError("speech-to-text failed") from self._error
        # a leftover interim is still the best text we have if no final result came
        return " ".join(p for p in self._final_parts + [self._interim] if p).strip()

    def cancel(self) -> None:
        """Abandon the turn: close the request stream without waiting."""
        self._audio.put(None)
