"""WebSocket /voice/browser — direct browser adapter, demo mode C (plan/03-voice-channel.md).

No Twilio, $0 apart from Google Speech: the voice app (`/web/`, hold-to-talk) and
`web/mic-test.html` (both via `web/src/voice/voiceCall.ts`) send binary PCM16 mono at
16 kHz and play back the binary PCM16 they receive; JSON text frames carry events
(transcript, reply, latency, clear). Used to tune STT/TTS/turn-taking without spending
call minutes. The business comes from the `business_id` query parameter.
"""

import logging
import uuid

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.voice.session import CallSession

log = logging.getLogger(__name__)
router = APIRouter()

BROWSER_SAMPLE_RATE = 16000


@router.websocket("/voice/browser")
async def browser_voice(ws: WebSocket, business_id: uuid.UUID) -> None:
    await ws.accept()

    async def send_audio(pcm: bytes) -> None:
        await ws.send_bytes(pcm)

    async def send_event(event: dict) -> None:
        await ws.send_json(event)

    session = CallSession(business_id, BROWSER_SAMPLE_RATE, send_audio, send_event, channel_caller="browser")
    try:
        await session.start()
    except LookupError:
        await ws.close(code=4404, reason="unknown business")
        return
    await send_event({"type": "conversation_id", "id": str(session.conversation_id)})

    try:
        while True:
            message = await ws.receive()
            if message["type"] == "websocket.disconnect":
                break
            if message.get("bytes"):
                await session.feed(message["bytes"])
    except WebSocketDisconnect:
        pass
    finally:
        await session.close()
