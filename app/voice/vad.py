"""Turn detection for the voice pipeline.

webrtcvad classifies 10/20/30 ms PCM16 frames as speech or not; this module
smooths that into turn events. Budget for end-of-speech detection is 300–700 ms (plan latency budget).

V9/V10 (plan/03-voice-channel.md "Known problems"): while the bot is speaking, speaker
echo or a cough/"mm-hm" can look like speech for a little while. `set_bot_active(True)`
switches to a stricter aggressiveness and a longer minimum speech length *before*
`speech_start` fires, so a false trigger needs more sustained, more speech-like audio to
go through; once real speech is confirmed (`speech_start` fired), detection reverts to the
normal settings for the rest of that turn, so end-of-speech detection isn't affected.
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
        end_silence_ms: int = 700,
        preroll_ms: int = 300,
        barge_in_aggressiveness: int = 3,
        barge_in_start_ms: int = 300,
    ) -> None:
        if sample_rate not in (8000, 16000, 32000, 48000):
            raise ValueError("sample_rate must be 8000, 16000, 32000, or 48000")
        if frame_ms not in (10, 20, 30):
            raise ValueError("frame_ms must be 10, 20, or 30")
        if not (0 <= aggressiveness <= 3):
            raise ValueError("aggressiveness must be between 0 and 3")
        if not (0 <= barge_in_aggressiveness <= 3):
            raise ValueError("barge_in_aggressiveness must be between 0 and 3")

        self._aggressiveness = aggressiveness
        self._barge_in_aggressiveness = barge_in_aggressiveness
        self._vader = webrtcvad.Vad(aggressiveness)
        self._sample_rate = sample_rate
        self._frame_bytes = sample_rate * frame_ms // 1000 * 2
        self._start_frames = max(1, start_ms // frame_ms)
        self._barge_in_start_frames = max(1, barge_in_start_ms // frame_ms)
        self._end_frames = max(1, end_silence_ms // frame_ms)
        self._preroll_frames = preroll_ms // frame_ms

        self._buffer: bytearray = bytearray()
        self._ring: collections.deque[bytes] = collections.deque(
            maxlen=self._preroll_frames + self._start_frames
        )
        self._in_speech: bool = False
        self._speech_frames: int = 0
        self._silence_frames: int = 0
        self._bot_active: bool = False

    def set_bot_active(self, active: bool) -> None:
        """V9/V10: call on every `feed()` with whether the bot is currently speaking.
        Changes take effect only while deciding whether to *start* a turn
        (`not in_speech`) -- an already-confirmed turn keeps the normal settings so
        end-of-speech detection isn't affected by this."""
        changed = active != self._bot_active
        self._bot_active = active
        if changed and not self._in_speech:
            self._vader.set_mode(self._barge_in_aggressiveness if active else self._aggressiveness)
            start_frames = self._barge_in_start_frames if active else self._start_frames
            self._ring = collections.deque(self._ring, maxlen=self._preroll_frames + start_frames)

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

                start_frames = self._barge_in_start_frames if self._bot_active else self._start_frames
                if self._speech_frames >= start_frames:
                    self._in_speech = True
                    events.append(
                        VadEvent(kind="speech_start", audio=b"".join(self._ring))
                    )
                    self._ring.clear()
                    self._speech_frames = 0
                    self._vader.set_mode(self._aggressiveness)  # confirmed: normal sensitivity for the rest of the turn
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
        self._speech_frames = 0
        self._silence_frames = 0
        self._in_speech = False
        start_frames = self._barge_in_start_frames if self._bot_active else self._start_frames
        self._ring = collections.deque(maxlen=self._preroll_frames + start_frames)
        self._vader.set_mode(self._barge_in_aggressiveness if self._bot_active else self._aggressiveness)

    @property
    def in_speech(self) -> bool:
        return self._in_speech
