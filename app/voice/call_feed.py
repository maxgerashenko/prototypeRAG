"""Live phone calls for the web app (DEC-46): GET /voice/feed, Server-Sent Events.

When the system takes a call over Twilio (`ws.py`, modes A and B), the voice app at
`/web/` shows it the way it shows its own browser calls: the same call screen, transcript
and status, updating as the call goes. `ws.py` hands each call's session events
(transcript, reply, clear, latency, no_speech, error) and the audio it sends to a
`CallFeed`, which adds `playback` on/off; `LiveCalls` fans them out to every open page.

Feed messages (one JSON object per SSE `data:` line):
- `snapshot`: every call in progress with all its events so far — sent first, so a page
  opened mid-call catches up, and again if a page fell too far behind
- `call_started`: a new call (`call`: id, business_id, conversation_id, caller, started_at)
- `call_event`: `call_id` + `event`, a session event with `t` (ms epoch) added
- `call_ended`: `call_id`, `t`

Events only, no audio: watching doesn't let you listen in. In-memory and per process:
fine for stage 1 (one uvicorn) and one Cloud Run instance; several instances in stage 2
would need a shared channel (Postgres LISTEN/NOTIFY), see DEC-46. Lists calls of every
business, as open as `/businesses` and the conversations API until access control lands
(OPEN-19); per-owner scoping is stage 3 (OPEN-12).
"""

import asyncio
import json
import time
from collections.abc import AsyncIterator
from dataclasses import asdict, dataclass, field

from fastapi import APIRouter
from fastapi.responses import StreamingResponse

router = APIRouter()

KEEPALIVE_S = 15  # an SSE comment line, so proxies (ngrok, Cloud Run) keep the stream open
QUEUE_SIZE = 500  # messages a slow page may lag behind before it gets a fresh snapshot
MAX_EVENTS = 1000  # per call kept for replay; a long call drops its oldest events


def now_ms() -> int:
    return int(time.time() * 1000)


@dataclass
class LiveCall:
    id: str
    business_id: str
    conversation_id: str | None
    caller: str | None
    started_at: int  # ms epoch
    events: list[dict] = field(default_factory=list)

    def info(self) -> dict:
        d = asdict(self)
        d.pop("events")
        return d


class LiveCalls:
    """Calls in progress and the pages watching them. Runs on the one event loop, so no lock."""

    def __init__(self) -> None:
        self._calls: dict[str, LiveCall] = {}
        self._watchers: set[asyncio.Queue] = set()

    def snapshot(self) -> dict:
        return {"type": "snapshot", "calls": [{**c.info(), "events": list(c.events)} for c in self._calls.values()]}

    def start(self, call_id: str, business_id: str, conversation_id: str | None, caller: str | None) -> None:
        call = LiveCall(call_id, business_id, conversation_id, caller, now_ms())
        self._calls[call_id] = call
        self._publish({"type": "call_started", "call": call.info()})

    def event(self, call_id: str, event: dict) -> None:
        call = self._calls.get(call_id)
        if call is None:
            return
        event = {**event, "t": now_ms()}
        call.events.append(event)
        del call.events[:-MAX_EVENTS]
        self._publish({"type": "call_event", "call_id": call_id, "event": event})

    def end(self, call_id: str) -> None:
        if self._calls.pop(call_id, None) is not None:
            self._publish({"type": "call_ended", "call_id": call_id, "t": now_ms()})

    def watch(self) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(QUEUE_SIZE)
        queue.put_nowait(self.snapshot())
        self._watchers.add(queue)
        return queue

    def unwatch(self, queue: asyncio.Queue) -> None:
        self._watchers.discard(queue)

    def _publish(self, message: dict) -> None:
        for queue in self._watchers:
            try:
                queue.put_nowait(message)
            except asyncio.QueueFull:
                # the page fell behind: drop what it hasn't read and start it over from now
                while not queue.empty():
                    queue.get_nowait()
                queue.put_nowait(self.snapshot())


live_calls = LiveCalls()


class CallFeed:
    """What one Twilio call publishes. Nothing goes out before `start()` (the stream isn't
    a call until its token and business check out) or after `end()`."""

    def __init__(self, live: LiveCalls = live_calls) -> None:
        self._live = live
        self._id: str | None = None
        self._playing_until = 0.0  # loop time Twilio runs out of the bot audio sent so far
        self._playback_task: asyncio.Task | None = None

    def start(self, call_id: str, business_id: object, conversation_id: object, caller: str | None) -> None:
        self._id = call_id
        self._live.start(call_id, str(business_id), conversation_id and str(conversation_id), caller)

    def event(self, event: dict) -> None:
        if self._id is None:
            return
        self._live.event(self._id, event)
        if event.get("type") == "clear":  # barge-in: Twilio drops what it hasn't played
            self._playing_until = 0.0
            if self._playback_task is not None and not self._playback_task.done():
                self._playback_task.cancel()
                self._live.event(self._id, {"type": "playback", "active": False})

    def audio_sent(self, seconds: float) -> None:
        """Twilio plays what we send in real time, so the bot is speaking until the audio
        sent so far runs out; `playback` on/off drives the web app's orb and status."""
        if self._id is None:
            return
        loop = asyncio.get_running_loop()
        self._playing_until = max(self._playing_until, loop.time()) + seconds
        if self._playback_task is None or self._playback_task.done():
            self._live.event(self._id, {"type": "playback", "active": True})
            self._playback_task = asyncio.create_task(self._playback_done())

    async def _playback_done(self) -> None:
        loop = asyncio.get_running_loop()
        while (left := self._playing_until - loop.time()) > 0:
            await asyncio.sleep(left)
        if self._id is not None:
            self._live.event(self._id, {"type": "playback", "active": False})

    def end(self) -> None:
        if self._playback_task is not None:
            self._playback_task.cancel()
        if self._id is not None:
            self._live.end(self._id)
            self._id = None


async def sse(queue: asyncio.Queue, keepalive_s: float = KEEPALIVE_S) -> AsyncIterator[str]:
    while True:
        try:
            message = await asyncio.wait_for(queue.get(), keepalive_s)
        except TimeoutError:
            yield ": keepalive\n\n"
            continue
        yield f"data: {json.dumps(message)}\n\n"


@router.get("/voice/feed", include_in_schema=False)
async def live_feed() -> StreamingResponse:
    queue = live_calls.watch()

    async def stream() -> AsyncIterator[str]:
        try:
            async for chunk in sse(queue):
                yield chunk
        finally:  # the page closed the stream (Starlette cancels the generator)
            live_calls.unwatch(queue)

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
