"""Per-call state and the call loop (plan/03-voice-channel.md, pipeline mode — DEC-30).

Transport-agnostic: adapters (`browser_ws.py` now, Twilio `ws.py` next) convert their
wire format to PCM16 frames, call `feed()`, and give the session two callbacks — one
that sends PCM16 back to the caller and one that sends events (transcript, reply text,
latency, and `clear` = stop playback now, for barge-in). The session never knows which
transport is used.

Turns are detected by the VAD (phone, hands-free) or marked by the client
(`turn_detection="manual"`, push-to-talk: `start_turn()`/`end_turn()`), which also
skips the VAD's 500 ms end-of-speech wait.

Per turn: VAD (`vad.py`) marks speech start/end → audio streams into Google STT while
the caller speaks → on speech end the final transcript goes to the LLM with the shared
tools (`tools.py`; RAG is `search_business_info`) → the reply streams back sentence by
sentence, each sentence synthesized by Google TTS and sent as soon as it's ready.

Barge-in: caller speech while the bot is replying or its audio is still playing cancels
the reply task and sends `clear`. Playback time is estimated from audio duration sent,
because audio goes out faster than real time and the transport plays it from a buffer.

Pass `continue_from` to load an earlier conversation of the same business as LLM history
(a new conversation row is still created).
"""

import asyncio
import logging
import re
import time
import uuid
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Literal
from zoneinfo import ZoneInfo

from app.config import get_settings
from app.db import tenant_session
from app.db.models import Business, BusinessProfile, Conversation, Message, message_order
from app.llm import get_async_chat_client
from app.rag.prompt import format_profile
from app.voice import tts
from app.voice.audio import working_sound
from app.voice.stt import TurnTranscriber
from app.voice.tools import TOOLS, run_tool
from app.voice.vad import TurnDetector
from sqlalchemy import select

log = logging.getLogger(__name__)

SendAudio = Callable[[bytes], Awaitable[None]]
SendEvent = Callable[[dict], Awaitable[None]]

MAX_TOOL_ROUNDS = 3  # LLM → tool → LLM ... before giving up on a turn
WORKING_SOUND_CHUNK_S = 0.2  # T1: sent in real time, so stopping it is never more than ~this late

# T4: the standard greeting's audio per (text, sample rate, voice, language), so a call
# starts playing at once instead of after a TTS round trip. In-process only (lost on
# restart); DEC-17 stores it at onboarding later. "Welcome back" greetings aren't cached:
# they carry the earlier topic, so they are new text every time.
_greeting_audio: dict[tuple, bytes] = {}

VOICE_SYSTEM_PROMPT = (
    "You are the phone assistant of the business below, talking to a caller.\n"
    "Answer only from the business profile below or from search_business_info results. "
    "Never use outside knowledge about this business or any other.\n"
    "Call search_business_info for any question the profile doesn't answer.\n"
    "If the answer isn't found, say so plainly and offer to take a message.\n"
    "Your words are spoken aloud: one to three short sentences, no lists, no markdown, "
    "no URLs. Answer in the language the caller uses."
)

# sentence end = . ! ? followed by whitespace; the rest stays buffered until more text comes
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")
# a period after these doesn't end the sentence (T4b, V13): "We open at 9 a.m. on Monday"
# was spoken as two sentences with a pause. Lower-case, compared with the word before the
# break. Not "etc." / "no.": those end sentences often enough that a split is the safer guess.
_ABBREVIATIONS = {"a.m.", "p.m.", "dr.", "st.", "e.g.", "i.e.", "mr.", "mrs.", "ms.", "vs."}


def split_sentences(buffer: str) -> tuple[list[str], str]:
    """Complete sentences in `buffer`, plus the unfinished tail to keep buffering."""
    sentences, start = [], 0
    for m in _SENTENCE_END.finditer(buffer):
        words = buffer[start:m.start()].split()
        if words and words[-1].lstrip("(\"'").lower() in _ABBREVIATIONS:
            continue
        if sentence := buffer[start:m.start()].strip():
            sentences.append(sentence)
        start = m.end()
    return sentences, buffer[start:]


class CallSession:
    def __init__(
        self,
        business_id: uuid.UUID,
        sample_rate: int,
        send_audio: SendAudio,
        send_event: SendEvent,
        *,
        turn_detection: Literal["vad", "manual"] = "vad",
        channel_caller: str | None = None,
        transcriber_factory: Callable[[int], TurnTranscriber] = TurnTranscriber,
        synthesize: Callable[[str, int], bytes] = tts.synthesize,
        continue_from: uuid.UUID | None = None,
    ) -> None:
        if turn_detection not in ("vad", "manual"):
            raise ValueError(f"turn_detection must be 'vad' or 'manual', got {turn_detection!r}")
        self.turn_detection = turn_detection
        self.business_id = business_id
        self.sample_rate = sample_rate
        self._send_audio = send_audio
        self._send_event = send_event
        self._caller = channel_caller
        self._new_transcriber = transcriber_factory
        self._synthesize = synthesize
        self._continue_from = continue_from
        self._topic: str | None = None

        self._vad = TurnDetector(sample_rate=sample_rate)
        self._turn: TurnTranscriber | None = None
        self._reply_task: asyncio.Task | None = None
        self._warmup_task: asyncio.Task | None = None
        self._working_task: asyncio.Task | None = None
        self._playing_until = 0.0  # monotonic time the caller's buffer runs out of bot audio
        self._history: list[dict] = []
        self._system_prompt = ""
        self.conversation_id: uuid.UUID | None = None

    # --- lifecycle ------------------------------------------------------------------

    async def start(self) -> None:
        """Load the business, open a conversation, play the greeting.
        Raises LookupError for an unknown business_id."""
        name = await asyncio.to_thread(self._load_business)
        # AI disclosure always (DEC-17); "may be recorded" only once recording exists (DEC-22)
        if self._topic is not None:
            greeting = f"Welcome back to {name}. I'm an AI assistant. Let's pick up where we left off: {self._topic}."
        else:
            greeting = f"Hi, you've reached {name}. I'm an AI assistant. How can I help?"
        self._reply_task = self._spawn(self._speak_greeting(greeting, cache=self._topic is None))
        if get_settings().voice_llm_warmup:
            self._warmup_task = asyncio.create_task(self._warm_up_llm())

    async def close(self) -> None:
        if self._warmup_task is not None:
            self._warmup_task.cancel()
        await self._cancel_reply()
        if self._turn is not None:
            self._turn.cancel()
            self._turn = None
        if self.conversation_id is not None:
            await asyncio.to_thread(self._end_conversation)

    # --- audio in -------------------------------------------------------------------

    async def feed(self, pcm: bytes) -> None:
        """PCM16 mono at `sample_rate`, any chunk size."""
        if self.turn_detection == "manual":
            if self._turn is not None:
                self._turn.push(pcm)
            return
        was_in_speech = self._vad.in_speech
        events = self._vad.feed(pcm)
        # speech_start's event carries the pre-roll + triggering frames; after that,
        # every chunk received while in speech streams to STT
        if was_in_speech and self._turn is not None:
            self._turn.push(pcm)
        for event in events:
            if event.kind == "speech_start":
                await self._on_speech_start(event.audio)
            else:
                self._on_speech_end()

    async def start_turn(self) -> None:
        """Caller pressed talk. Ignores repeated presses if a turn is already open."""
        if self._turn is not None:
            return
        await self._on_speech_start(b"")

    def end_turn(self) -> None:
        """Caller released talk."""
        if self._turn is None:
            return
        self._on_speech_end()

    async def _on_speech_start(self, preroll: bytes) -> None:
        if self._bot_active():
            await self._cancel_reply()
            self._playing_until = 0.0
            await self._send_event({"type": "clear"})
        self._turn = self._new_transcriber(self.sample_rate)
        self._turn.push(preroll)

    def _on_speech_end(self) -> None:
        turn, self._turn = self._turn, None
        if turn is not None:
            self._reply_task = self._spawn(self._handle_turn(turn, time.monotonic()))

    def _spawn(self, coro) -> asyncio.Task:
        """Reply tasks are never awaited on the happy path, so a failure (TTS without
        credentials, LLM down) would otherwise vanish; log it and tell the client."""
        task = asyncio.create_task(coro)

        def report(t: asyncio.Task) -> None:
            if not t.cancelled() and t.exception() is not None:
                log.error("voice reply failed", exc_info=t.exception())
                asyncio.ensure_future(self._try_send_event({"type": "error", "message": repr(t.exception())}))

        task.add_done_callback(report)
        return task

    async def _try_send_event(self, event: dict) -> None:
        try:
            await self._send_event(event)
        except Exception:  # the transport may already be closed
            pass

    def _bot_active(self) -> bool:
        replying = self._reply_task is not None and not self._reply_task.done()
        return replying or time.monotonic() < self._playing_until

    async def _cancel_reply(self) -> None:
        task, self._reply_task = self._reply_task, None
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    # --- one turn -------------------------------------------------------------------

    async def _handle_turn(self, turn: TurnTranscriber, speech_end: float) -> None:
        try:
            text = await asyncio.to_thread(turn.finish)
        except RuntimeError:
            log.exception("STT failed")
            return
        timings = {"stt_ms": _ms(speech_end)}
        if not text:
            # noise the VAD took for speech, or push-to-talk with nothing said; the client
            # needs to know so it doesn't wait for an answer
            await self._send_event({"type": "no_speech"})
            return
        await self._send_event({"type": "transcript", "text": text})

        spoken: list[str] = []
        try:
            await self._reply(text, spoken, speech_end, timings)
        finally:
            # also on barge-in (cancel): keep what the caller actually heard as context
            reply = " ".join(spoken)
            self._history += [{"role": "user", "content": text}, {"role": "assistant", "content": reply}]
            await asyncio.shield(asyncio.to_thread(self._save_turn, text, reply))
            timings["total_ms"] = _ms(speech_end)
            log.info("voice turn %s: %s", self.conversation_id, timings)
            await self._send_event({"type": "latency", **timings})

    async def _reply(self, text: str, spoken: list[str], speech_end: float, timings: dict) -> None:
        try:
            await self._reply_rounds(text, spoken, speech_end, timings)
        finally:
            self._stop_working_sound()

    async def _reply_rounds(self, text: str, spoken: list[str], speech_end: float, timings: dict) -> None:
        messages = [
            {"role": "system", "content": self._system_prompt},
            *self._history,
            {"role": "user", "content": text},
        ]
        client = get_async_chat_client()
        for _ in range(MAX_TOOL_ROUNDS):
            settings = get_settings()
            stream = await client.chat.completions.create(
                model=settings.llm_model, messages=messages, tools=TOOLS, stream=True,
                reasoning_effort=settings.voice_reasoning_effort,
            )
            buffer = ""
            calls: dict[int, dict] = {}
            async for chunk in stream:
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta
                if delta.tool_calls:
                    self._start_working_sound(speech_end, timings)
                for tc in delta.tool_calls or []:
                    call = calls.setdefault(tc.index, {"id": "", "name": "", "arguments": ""})
                    if tc.id:
                        call["id"] = tc.id
                    if tc.function and tc.function.name:
                        call["name"] += tc.function.name
                    if tc.function and tc.function.arguments:
                        call["arguments"] += tc.function.arguments
                if delta.content:
                    buffer += delta.content
                    sentences, buffer = split_sentences(buffer)
                    for sentence in sentences:
                        await self._say(sentence, spoken, speech_end, timings)
            if buffer.strip():
                await self._say(buffer.strip(), spoken, speech_end, timings)
            if not calls:
                return

            timings.setdefault("tool_start_ms", _ms(speech_end))
            messages.append({
                "role": "assistant",
                "content": " ".join(spoken) or None,
                "tool_calls": [
                    {"id": c["id"], "type": "function", "function": {"name": c["name"], "arguments": c["arguments"]}}
                    for c in calls.values()
                ],
            })
            for c in calls.values():
                result = await asyncio.to_thread(run_tool, self.business_id, c["name"], c["arguments"])
                messages.append({"role": "tool", "tool_call_id": c["id"], "content": result})
            timings.setdefault("tool_end_ms", _ms(speech_end))
        await self._say("Sorry, I couldn't find that. Can I take a message?", spoken, speech_end, timings)

    async def _say(self, sentence: str, spoken: list[str], speech_end: float, timings: dict) -> None:
        timings.setdefault("llm_first_sentence_ms", _ms(speech_end))
        await self._send_event({"type": "reply", "text": sentence})
        audio = await asyncio.to_thread(self._synthesize, sentence, self.sample_rate)
        timings.setdefault("tts_first_audio_ms", _ms(speech_end))
        self._stop_working_sound()  # its last chunk (≤ WORKING_SOUND_CHUNK_S) still plays first
        await self._play(audio)
        spoken.append(sentence)

    async def _speak_greeting(self, greeting: str, cache: bool) -> None:
        await self._send_event({"type": "reply", "text": greeting})
        settings = get_settings()
        key = (greeting, self.sample_rate, settings.tts_voice, settings.voice_language)
        audio = _greeting_audio.get(key) if cache else None
        if audio is None:
            audio = await asyncio.to_thread(self._synthesize, greeting, self.sample_rate)
            if cache:
                _greeting_audio[key] = audio
        await self._play(audio)
        self._history.append({"role": "assistant", "content": greeting})
        await asyncio.to_thread(self._save_message, "assistant", greeting)

    async def _warm_up_llm(self) -> None:
        """T2: while the greeting plays, send the prompt prefix every turn starts with
        (system prompt, tools, earlier history) with max_tokens=1, so LM Studio has it
        cached and the first answer isn't slower than later ones (2.6 s vs ~1 s measured).
        Best effort: a failure only means a slower first turn, so it's logged, not raised."""
        settings = get_settings()
        try:
            await get_async_chat_client().chat.completions.create(
                model=settings.llm_model, messages=[{"role": "system", "content": self._system_prompt}, *self._history],
                tools=TOOLS, max_tokens=1, reasoning_effort=settings.voice_reasoning_effort,
            )
        except Exception:
            log.warning("LLM warm-up failed", exc_info=True)

    def _start_working_sound(self, speech_end: float, timings: dict) -> None:
        """T1: a quiet tick loop while a tool runs, instead of seconds of silence."""
        if self._working_task is None or self._working_task.done():
            timings.setdefault("working_sound_ms", _ms(speech_end))
            self._working_task = asyncio.create_task(self._play_working_sound())

    def _stop_working_sound(self) -> None:
        task, self._working_task = self._working_task, None
        if task is not None:
            task.cancel()

    async def _play_working_sound(self) -> None:
        """Chunks paced in real time: the caller's buffer never holds much more than one
        chunk, so the answer starts right after it, with no `clear` that could cut the
        answer. Counts as bot audio (`_playing_until`), so barge-in stops it like speech;
        never added to the transcript."""
        loop = working_sound(self.sample_rate)
        twice = loop + loop  # a chunk may wrap past the loop's end
        step = round(self.sample_rate * WORKING_SOUND_CHUNK_S) * 2
        pos = 0
        try:
            while True:
                await self._play(twice[pos:pos + step])
                pos = (pos + step) % len(loop)
                await asyncio.sleep(max(0.0, self._playing_until - time.monotonic() - 0.1))
        except Exception:  # the caller hung up mid-search; close() cancels the reply that owns this
            pass

    async def _play(self, audio: bytes) -> None:
        await self._send_audio(audio)
        now = time.monotonic()
        self._playing_until = max(now, self._playing_until) + tts.pcm16_duration_s(audio, self.sample_rate)

    # --- database (sync, run in a thread) --------------------------------------------

    def _load_business(self) -> str:
        with tenant_session(self.business_id) as session:
            business = session.get(Business, self.business_id)
            if business is None:
                raise LookupError(f"unknown business {self.business_id}")
            profile = session.get(BusinessProfile, self.business_id)
            if self._continue_from is not None:
                rows = session.scalars(
                    select(Message).where(
                        Message.business_id == self.business_id,
                        Message.conversation_id == self._continue_from,
                        Message.role.in_(("user", "assistant")),
                    ).order_by(*message_order())
                ).all()
                if rows:
                    self._history = [{"role": m.role, "content": m.content} for m in rows]
                    user_msgs = [m for m in rows if m.role == "user"]
                    if user_msgs:
                        raw = user_msgs[0].content.strip()
                        if raw and raw[-1] in ".!?":
                            raw = raw[:-1]
                        if len(raw) > 60:
                            raw = raw[:59] + "…"
                        self._topic = raw
                    else:
                        self._topic = None
            conversation = Conversation(business_id=self.business_id, channel="voice", caller=self._caller)
            session.add(conversation)
            session.flush()
            self.conversation_id = conversation.id
            now = datetime.now(ZoneInfo(business.timezone)).strftime("%A %Y-%m-%d %H:%M")
            self._system_prompt = (
                f"{VOICE_SYSTEM_PROMPT}\n\nCurrent local time at the business: {now}\n\n{format_profile(profile)}"
            )
            # A3 (plan/07-knowledge-quality.md §3): business.name is now the brand
            # ("Bathhouse"), kept in sync from the site's og:site_name on every crawl
            # (app/ingest/run.py) -- preferred over business_profile.name, which still
            # holds the old LLM-extracted "Bathhouse Williamsburg" (profile table is
            # retired once the facts/location card replaces it, plan 07 §5.5).
            placeholder = {"business_name", "(pending)", "pending", ""}
            if business.name and business.name.strip().lower() not in placeholder:
                return business.name
            return (profile.name if profile and profile.name else None) or business.name

    def _save_message(self, role: str, content: str) -> None:
        with tenant_session(self.business_id) as session:
            session.add(Message(business_id=self.business_id, conversation_id=self.conversation_id,
                                role=role, content=content))

    def _save_turn(self, question: str, answer: str) -> None:
        with tenant_session(self.business_id) as session:
            for role, content in (("user", question), ("assistant", answer)):
                session.add(Message(business_id=self.business_id, conversation_id=self.conversation_id,
                                    role=role, content=content))

    def _end_conversation(self) -> None:
        with tenant_session(self.business_id) as session:
            conversation = session.get(Conversation, self.conversation_id)
            if conversation is not None and conversation.business_id == self.business_id:
                conversation.ended_at = datetime.now().astimezone()


def _ms(since: float) -> int:
    return round((time.monotonic() - since) * 1000)
