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
V19: `ended_at` is set when the socket closes, even if closing the session is cancelled (server
shutdown); if the process is killed outright, `end_orphaned_browser_conversations()` closes the
leftovers on the next startup.
"""

import json
import logging
import uuid
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from sqlalchemy import func, select

from app.db import tenant_session
from app.db.models import Conversation, Message
from app.db.session import SessionLocal
from app.voice.session import CallSession

log = logging.getLogger(__name__)
router = APIRouter()

BROWSER_SAMPLE_RATE = 16000


def _mark_ended(business_id: uuid.UUID, conversation_id: uuid.UUID, ended_at: datetime | None = None) -> None:
    with tenant_session(business_id) as db:
        conversation = db.get(Conversation, conversation_id)
        if conversation is not None and conversation.ended_at is None:
            conversation.ended_at = ended_at or datetime.now().astimezone()


def end_orphaned_browser_conversations() -> int:
    """Startup sweep (V19): browser calls whose process died without closing them. Run before
    serving, so every open `channel='voice'` conversation without a `call_sid` (Twilio ones get
    theirs from the status callback) is a leftover. `ended_at` = the last message, else the start."""
    with SessionLocal() as db:
        last_message = (
            select(func.max(Message.created_at))
            .where(Message.conversation_id == Conversation.id).scalar_subquery()
        )
        rows = db.execute(
            select(Conversation.business_id, Conversation.id, func.coalesce(last_message, Conversation.started_at))
            .where(Conversation.channel == "voice", Conversation.call_sid.is_(None),
                   Conversation.ended_at.is_(None))
        ).all()
    for business_id, conversation_id, ended_at in rows:
        _mark_ended(business_id, conversation_id, ended_at)
    return len(rows)


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
        try:
            await session.close()
        finally:
            # session.close() may be cancelled before it gets to `ended_at` (server shutdown);
            # a short synchronous write is still allowed to finish here
            if session.conversation_id is not None:
                _mark_ended(business_id, session.conversation_id)
