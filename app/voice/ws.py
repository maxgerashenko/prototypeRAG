"""WebSocket /voice/ws — Twilio Media Streams adapter, demo modes A and B (plan/03-voice-channel.md).

Twilio opens this socket after `/twilio/voice` answered with `<Connect><Stream>`. All frames
are JSON text: `connected`, then `start` (streamSid, callSid, our custom parameters),
`media` (base64 8 kHz μ-law, 20 ms each), `mark`, `dtmf`, `stop`. We send back `media`
(base64 μ-law) and `clear` (drop the audio Twilio has buffered — barge-in).

Thin like `browser_ws.py`: μ-law ↔ PCM16 at 8 kHz in and out, the call loop in
`session.py` does the rest. The session's other events (transcript, reply, latency) have
no Twilio equivalent and are only logged.
"""

import base64
import json
import logging
import uuid

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.voice.audio import TWILIO_SAMPLE_RATE, pcm16_to_ulaw, ulaw_to_pcm16
from app.voice.session import CallSession
from app.voice.twilio_routes import check_stream_token

log = logging.getLogger(__name__)
router = APIRouter()

FRAME_BYTES = 160  # 20 ms of 8 kHz μ-law — the frame size Twilio itself sends


@router.websocket("/voice/ws")
async def twilio_media_stream(ws: WebSocket) -> None:
    await ws.accept()
    stream_sid: str | None = None
    session: CallSession | None = None

    async def send_audio(pcm: bytes) -> None:
        ulaw = pcm16_to_ulaw(pcm)
        for i in range(0, len(ulaw), FRAME_BYTES):
            payload = base64.b64encode(ulaw[i:i + FRAME_BYTES]).decode()
            await ws.send_text(json.dumps({"event": "media", "streamSid": stream_sid, "media": {"payload": payload}}))

    async def send_event(event: dict) -> None:
        if event.get("type") == "clear":
            await ws.send_text(json.dumps({"event": "clear", "streamSid": stream_sid}))
        else:
            log.debug("twilio stream %s: %s", stream_sid, event)

    try:
        while True:
            try:
                data = json.loads(await ws.receive_text())
            except json.JSONDecodeError:
                continue
            if not isinstance(data, dict):
                continue
            event = data.get("event")

            if event == "media" and session is not None:
                media = data.get("media") or {}
                if media.get("track", "inbound") == "inbound" and media.get("payload"):
                    await session.feed(ulaw_to_pcm16(base64.b64decode(media["payload"])))

            elif event == "start" and session is None:
                start = data.get("start") or {}
                stream_sid = start.get("streamSid") or data.get("streamSid")
                call_sid = start.get("callSid", "")
                params = start.get("customParameters") or {}
                try:
                    business_id = uuid.UUID(params.get("business_id", ""))
                except ValueError:
                    business_id = None
                if business_id is None or not check_stream_token(business_id, call_sid, params.get("token", "")):
                    log.warning("twilio stream %s: rejected (call %s, params %s)", stream_sid, call_sid,
                                sorted(params))
                    await ws.close(code=4403, reason="stream not started by /twilio/voice")
                    return
                session = CallSession(
                    business_id, TWILIO_SAMPLE_RATE, send_audio, send_event,
                    channel_caller=params.get("caller") or None, call_sid=call_sid or None,
                )
                try:
                    await session.start()
                except LookupError:
                    session = None
                    await ws.close(code=4404, reason="unknown business")
                    return
                log.info("twilio stream %s: call %s, conversation %s", stream_sid, call_sid,
                         session.conversation_id)

            elif event == "stop":
                break
    except WebSocketDisconnect:
        pass
    finally:
        if session is not None:
            await session.close()
