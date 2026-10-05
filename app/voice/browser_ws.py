"""WebSocket /voice/browser — direct browser adapter, demo mode C (plan/03-voice-channel.md).

No Twilio, $0 apart from Google Speech: the voice app (`/web/`, mode=ptt) and
`web/voice-debug.html` (mode=vad), both via `web/src/voice/voiceCall.ts`, send binary PCM16
mono at 16 kHz and play back the binary PCM16 they receive; JSON text frames carry events
(transcript, reply, latency, clear, no_speech, error, conversation_id, hangup). Used to
tune STT/TTS/turn-taking without spending call minutes. The business comes from the `business_id` query parameter.
Supports `mode=vad` (default) or `mode=ptt` (push-to-talk via `ptt_start`/`ptt_end` text frames).
Supports `continue_from=<conversation id>` to resume an earlier conversation.
When the session hangs up (V17: silence, or the caller said goodbye) it sends a `hangup`
event and the socket is closed with code 1000.
"""

import json
import logging
import uuid
from typing import Literal

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.voice.session import CallSession

log = logging.getLogger(__name__)
router = APIRouter()

BROWSER_SAMPLE_RATE = 16000


@router.websocket("/voice/browser")
async def browser_voice(
    ws: WebSocket,
    business_id: uuid.UUID,
    mode: Literal["vad", "ptt"] = "vad",
    continue_from: uuid.UUID | None = None,
) -> None:
    await ws.accept()

    async def send_audio(pcm: bytes) -> None:
        await ws.send_bytes(pcm)

    async def send_event(event: dict) -> None:
        await ws.send_json(event)

    async def hang_up() -> None:
        # the receive loop below then gets the disconnect and closes the session
        await ws.close(code=1000, reason="call ended")

    session = CallSession(
        business_id, BROWSER_SAMPLE_RATE, send_audio, send_event, channel_caller="browser",
        turn_detection="manual" if mode == "ptt" else "vad",
        continue_from=continue_from, hang_up=hang_up,
    )
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
            elif message.get("text"):
                try:
                    data = json.loads(message["text"])
                except (json.JSONDecodeError, TypeError):
                    continue
                if not isinstance(data, dict):
                    continue
                if data.get("type") == "ptt_start":
                    await session.start_turn()
                elif data.get("type") == "ptt_end":
                    session.end_turn()
    except WebSocketDisconnect:
        pass
    finally:
        await session.close()
