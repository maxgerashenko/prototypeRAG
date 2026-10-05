"""Turn detection for the voice pipeline.

webrtcvad classifies 10/20/30 ms PCM16 frames as speech or not; this module
smooths that into turn events. Budget for end-of-speech detection is 300–500 ms (plan latency budget).
"""

import collections
from dataclasses import dataclass
from typing import Literal

import webrtcvad

@dataclass(frozen=True)
class VadEvent:
    kind: Literal["speech_start", "speech", "speech_end"]
    # speech_start: pre-roll + triggering frames; speech: the frames after it while in
    # speech, up to and including the frame that ends it (PCM16); speech_end: b""
    audio: bytes


class TurnDetector:
    def __init__(
        self,
        sample_rate: int = 8000,
        frame_ms: int = 20,
        aggressiveness: int = 2,
        start_ms: int = 160,
        end_silence_ms: int = 500,
        preroll_ms: int = 300,
    ) -> None:
        if sample_rate not in (8000, 16000, 32000, 48000):
            raise ValueError("sample_rate must be 8000, 16000, 32000, or 48000")
        if frame_ms not in (10, 20, 30):
            raise ValueError("frame_ms must be 10, 20, or 30")
        if not (0 <= aggressiveness <= 3):
            raise ValueError("aggressiveness must be between 0 and 3")

        self._vader = webrtcvad.Vad(aggressiveness)
        self._sample_rate = sample_rate
        self._frame_bytes = sample_rate * frame_ms // 1000 * 2
        self._start_frames = max(1, start_ms // frame_ms)
        self._end_frames = max(1, end_silence_ms // frame_ms)
        self._preroll_frames = preroll_ms // frame_ms

        self._buffer: bytearray = bytearray()
        self._ring: collections.deque[bytes] = collections.deque(
            maxlen=self._preroll_frames + self._start_frames
        )
        self._in_speech: bool = False
        self._speech_frames: int = 0
        self._silence_frames: int = 0

    def feed(self, pcm16: bytes) -> list[VadEvent]:
        """Process any number of PCM16 bytes; partial frames stay buffered for the next call.

        In-speech frames come back as at most one `speech` event per call between the
        turn events, so a chunk holding several frames splits exactly at the trigger frame."""
        events: list[VadEvent] = []
        speech = bytearray()

        def flush_speech() -> None:
            if speech:
                events.append(VadEvent(kind="speech", audio=bytes(speech)))
                speech.clear()

        self._buffer.extend(pcm16)

        while len(self._buffer) >= self._frame_bytes:
            frame = bytes(self._buffer[: self._frame_bytes])
            del self._buffer[: self._frame_bytes]  # in place, no re-copy of the rest

            is_speech = self._vader.is_speech(frame, self._sample_rate)

            if not self._in_speech:
                self._ring.append(frame)
                if is_speech:
                    self._speech_frames += 1
                else:
                    self._speech_frames = 0

                if self._speech_frames >= self._start_frames:
                    self._in_speech = True
                    events.append(
                        VadEvent(kind="speech_start", audio=b"".join(self._ring))
                    )
                    self._ring.clear()
                    self._speech_frames = 0
            else:
                speech.extend(frame)
                if not is_speech:
                    self._silence_frames += 1
                else:
                    self._silence_frames = 0

                if self._silence_frames >= self._end_frames:
                    self._in_speech = False
                    flush_speech()
                    events.append(VadEvent(kind="speech_end", audio=b""))
                    self._silence_frames = 0

        flush_speech()
        return events

    def reset(self) -> None:
        self._buffer.clear()
        self._ring.clear()
        self._speech_frames = 0
        self._silence_frames = 0
        self._in_speech = False

    @property
    def in_speech(self) -> bool:
        return self._in_speech
