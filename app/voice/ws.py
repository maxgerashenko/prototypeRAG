"""WebSocket /voice/ws — Twilio Media Streams adapter, demo modes A and B
(plan/03-voice-channel.md).

`twilio_routes.py` answers the call with `<Connect><Stream url=".../voice/ws">`, so Twilio
opens this WebSocket and sends JSON text frames: `connected`, then `start` (streamSid,
callSid, and the `<Parameter>`s we put in the TwiML: business_id, caller), then `media`
every 20 ms (base64 8 kHz μ-law), `mark`, `dtmf`, and `stop` at hang-up. With
TWILIO_AUTH_TOKEN set, `start` must carry the stream token our TwiML signed (DEC-43).

Thin by design: μ-law ↔ PCM16 at 8 kHz in and out, everything else is `CallSession`.
The session's `clear` event (barge-in) becomes Twilio's `clear` message, which drops the
audio Twilio has buffered but not played yet; the other session events (transcript,
reply, latency) have no place on a phone line and only go to the log.
"""

import asyncio
import base64
import json
import logging
import uuid

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.voice.audio import TWILIO_SAMPLE_RATE, pcm16_to_ulaw, ulaw_to_pcm16
from app.voice.session import CallSession
from app.voice.twilio_routes import stream_token_ok

log = logging.getLogger(__name__)
router = APIRouter()

# outgoing audio per `media` message: 100 ms of 8 kHz μ-law, so a barge-in cancel stops
# sending within one chunk and Twilio never gets one huge payload
OUT_CHUNK_BYTES = TWILIO_SAMPLE_RATE // 10


class TwilioStream:
    """One Media Stream: Twilio's JSON protocol on one side, a CallSession on the other."""

    def __init__(self, ws: WebSocket, session_factory=None) -> None:
        self._ws = ws
        self._new_session = session_factory or CallSession  # looked up per call: tests patch it
        self._send_lock = asyncio.Lock()  # reply task and barge-in both send (V15)
        self.stream_sid: str | None = None
        self.session: CallSession | None = None

    async def _send(self, message: dict) -> None:
        async with self._send_lock:
            await self._ws.send_text(json.dumps(message))

    async def send_audio(self, pcm: bytes) -> None:
        ulaw = pcm16_to_ulaw(pcm)
        for i in range(0, len(ulaw), OUT_CHUNK_BYTES):
            payload = base64.b64encode(ulaw[i:i + OUT_CHUNK_BYTES]).decode("ascii")
            await self._send({"event": "media", "streamSid": self.stream_sid, "media": {"payload": payload}})

    async def send_event(self, event: dict) -> None:
        if event.get("type") == "clear":
            await self._send({"event": "clear", "streamSid": self.stream_sid})
        elif event.get("type") == "error":
            log.warning("call %s: %s", self.stream_sid, event.get("message"))
        else:
            log.info("call %s: %s", self.stream_sid, event)

    async def handle(self, message: dict) -> bool:
        """Process one Twilio message; False when the stream is over."""
        kind = message.get("event")
        if kind == "start":
            return await self._on_start(message.get("start") or {})
        if kind == "media" and self.session is not None:
            media = message.get("media") or {}
            if media.get("track", "inbound") == "inbound" and media.get("payload"):
                await self.session.feed(ulaw_to_pcm16(base64.b64decode(media["payload"])))
        elif kind == "stop":
            return False
        return True  # connected, mark, dtmf: nothing to do yet

    async def _on_start(self, start: dict) -> bool:
        self.stream_sid = start.get("streamSid")
        params = start.get("customParameters") or {}
        try:
            business_id = uuid.UUID(params.get("business_id", ""))
        except ValueError:
            log.warning("Twilio stream %s without a valid business_id", self.stream_sid)
            return False
        if not stream_token_ok(str(business_id), start.get("callSid", ""), params.get("token", "")):
            log.warning("Twilio stream %s with a bad stream token (DEC-43)", self.stream_sid)
            return False
        self.session = self._new_session(
            business_id, TWILIO_SAMPLE_RATE, self.send_audio, self.send_event,
            channel_caller=params.get("caller") or None,
        )
        try:
            await self.session.start()
        except LookupError:
            log.warning("Twilio stream %s for unknown business %s", self.stream_sid, business_id)
            self.session = None
            return False
        log.info("Twilio stream %s (call %s) -> conversation %s",
                 self.stream_sid, start.get("callSid"), self.session.conversation_id)
        return True

    async def close(self) -> None:
        if self.session is not None:
            await self.session.close()


@router.websocket("/voice/ws")
async def twilio_media_stream(ws: WebSocket) -> None:
    await ws.accept()
    stream = TwilioStream(ws)
    try:
        while True:
            try:
                message = json.loads(await ws.receive_text())
            except json.JSONDecodeError:
                continue
            if not isinstance(message, dict):
                continue
            if not await stream.handle(message):
                break
    except WebSocketDisconnect:
        pass
    finally:
        await stream.close()
    try:
        await ws.close()  # ends the <Connect>, so Twilio hangs up
    except (RuntimeError, WebSocketDisconnect):
        pass  # already closed by Twilio
