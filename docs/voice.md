# Voice layer

The Voice layer turns what a caller or chat user says into the assistant's reply. It owns
turn taking, the LLM call loop with tools, the prompts, and when to stop talking or hang up.
It does not own audio transport, retrieval or stored data (Data layer), the things the app *does*
for the caller such as booking or transferring the call (Actions layer), or the clients for
outside services such as Twilio, Google Speech and the LLM endpoint (Integrations layer); it calls
those through the layers listed under [Depends on / Used by](#depends-on--used-by).

Based on `main` at `75d3bfd` (2026-11-02). Decisions referenced: DEC-17 (AI disclosure),
DEC-29 (thinking off for voice), DEC-30 (pipeline mode, RAG as a tool), DEC-44 (barge-in
threshold), DEC-49 (default location), DEC-53 (owner greeting). V/T/B numbers refer to
"Known problems" and tuning items in [plan/03-voice-channel.md](../plan/03-voice-channel.md).

## 1. Purpose and boundaries

| In this layer | Not in this layer (owner) |
|---|---|
| Per-call state machine: greeting, turns, barge-in, silence, hang-up | WebSocket wire formats, μ-law, Twilio frames ([api.md](api.md)) |
| Turn detection (VAD) and push-to-talk turns | Google STT/TTS clients, OpenAI-compatible client ([integrations.md](integrations.md)) |
| LLM tool-calling loop, voice and chat | What each action tool does ([actions.md](actions.md)) |
| System prompts and prompt assembly | Retrieval, chunks, profile, summary data ([data.md](data.md)) |
| Sentence splitting and cleaning text for TTS | Live call feed, Twilio status callback, call summary ([api.md](api.md), [actions.md](actions.md)) |
| Greeting text and its audio cache | Web UI that plays audio and shows events ([web.md](web.md)) |
| Saving messages and opening/closing a conversation row | Schema and RLS sessions ([data.md](data.md), `app/db/`) |

The voice session is **transport-agnostic**: an adapter hands it PCM16 frames and two
callbacks (`send_audio`, `send_event`) plus an optional `hang_up`. It never knows whether
the other end is Twilio or a browser.

## 2. Modules and roles

| Module | Role |
|---|---|
| `app/voice/session.py` | `CallSession`: the whole voice call loop. Also `split_sentences`, `speakable`, `is_farewell`, `VOICE_SYSTEM_PROMPT`. |
| `app/voice/vad.py` | `TurnDetector`: webrtcvad frames → `speech_start` / `speech` / `speech_end` events, with stricter start rules while the bot talks. |
| `app/voice/greeting_cache.py` | Greeting text (`business_greeting`, `voice_name`, `AI_DISCLOSURE`) and its stored audio in Postgres (`load`, `store`, `pregenerate`, `prune`, `cache_status`). |
| `app/voice/tools.py` | The tool surface shared by voice and chat: registers `search_business_info`, imports action modules so they register, exposes `TOOLS`, `tools_for`, `enabled_tool_names`, `run_tool`. |
| `app/voice/audio.py` (`working_sound`) | Generated "working on it" tick loop played while a tool runs (T1). The codec helpers in the same file belong to the transport side. |
| `app/rag/answer.py` | Chat answer pipeline: `prepare_prompt`, `answer_question` (blocking), `stream_answer` (generator), same tool loop as voice. |
| `app/rag/prompt.py` | Prompt text and formatters: chat `SYSTEM_PROMPT`, `BOOKING_PROMPT`, `SESSION_PROMPT`, `tools_prompt`, `format_profile`, `format_summary`, `format_chunks`, `build_prompt`. |

## 3. Voice call lifecycle and timing

```
adapter → CallSession(business_id, rate, send_audio, send_event, …)
  start()
    _load_business()        thread: tools for business, profile, history (continue_from),
                             new Conversation row, system prompt, greeting text
    reply task: _speak_fixed(greeting)   cached audio (memory → Postgres → TTS)
    warm-up task: _warm_up_llm()         max_tokens=1 with the full prompt prefix (T2)
    silence task: _watch_silence()       VAD mode only (V17)
  feed(pcm) … many times
    VAD speech_start  → _on_speech_start: barge-in if bot active, new TurnTranscriber
    VAD speech        → transcriber.push()
    VAD speech_end    → _on_speech_end: reply task = _handle_turn(turn)
  on_mark(name)        Twilio mark echoes = real playback end (V11)
  close()              cancel tasks, cancel STT, set conversation.ended_at
```

### One turn (`_handle_turn`)

1. **STT.** `turn.finish()` (in a thread) ends the gRPC stream and waits up to
   `FINISH_TIMEOUT_S` = 2 s for the final text; a leftover interim is used if the final
   result is late. The STT task is **shielded**: if a barge-in cancels the turn while STT
   is still running, the task is kept in `_unanswered_stt` and its words lead the next turn.
2. **Empty text** → `no_speech` event, no LLM call. **STT error** → `error` event (V7).
3. `transcript` event, then `_reply` → `_reply_rounds`, the tool loop (section 4).
4. Each finished sentence goes through `_say`: `speakable()` → `reply` event → TTS
   (thread) → `_play()`. The first sentence is spoken while the LLM is still writing the
   rest.
5. After the LLM is done, the turn **stays active until playback ends**
   (`_playback_left()`), so a barge-in can still cut audio that was sent but not heard (B2).
6. `finally`: history and the saved transcript get the user text and **only the sentences
   whose playback ended before the cut** (`play_ends` vs `heard_by`). Saving is shielded so
   a cancel doesn't lose it. A `latency` event is sent even on cancel.
7. If the answer played out fully: a pending transfer or a farewell (`is_farewell`) hangs
   up after playback.

### Timing fields (`latency` event and log line `voice turn …`)

All measured from the VAD's speech end (or push-to-talk release):
`stt_ms`, `working_sound_ms`, `tool_start_ms`, `tool_end_ms`, `llm_first_sentence_ms`,
`tts_first_audio_ms`, `total_ms`. Each is set once per turn (`setdefault`).

### Turn taking and barge-in

| Mechanism | How |
|---|---|
| End of turn (VAD mode) | `vad_end_silence_ms` (default 700 ms) of non-speech frames. Start needs 160 ms of speech; 300 ms pre-roll goes to STT. |
| End of turn (push-to-talk) | `start_turn()` / `end_turn()` from the client; no end-silence wait. `turn_detection="manual"` skips the VAD entirely. |
| Bot "active" | Reply task running **or** estimated playback left > 0. Playback end = `_playing_until`, advanced by each chunk's duration, because audio is sent faster than real time. |
| Playback marks (Twilio, V11) | After each `_play`, a `mark` event; Twilio echoes it when played. Until the echo, the bot counts as playing up to `MARK_GRACE_S` = 1 s past the estimate. |
| Barge-in | Speech start while active → cancel reply task, reset playback, clear marks, send `clear` (the adapter drops buffered audio). |
| False barge-in guard (V9/V10, DEC-44) | Every `feed()` calls `set_bot_active()`. While active, start needs aggressiveness `vad_barge_in_aggressiveness` (3) and `vad_barge_in_start_ms` (300 ms). After `speech_start` the normal settings return for that turn. |
| Working sound (T1) | First tool-call delta starts a tick loop, sent in 0.2 s real-time chunks so stopping it is never late and needs no `clear`. Stopped when the first answer audio is ready. Counts as bot audio, never in the transcript. |

### Silence and hang-up (V17, VAD mode only)

`_watch_silence` polls every 0.25 s. Quiet is measured from the later of the caller's last
speech and the end of bot audio. After `SILENCE_REPROMPT_S` = 6 s: one "Are you still
there?" (`SILENCE_MAX_REPROMPTS` = 1), after the next 6 s the goodbye line, then hang-up.
Both lines are reply tasks, so the caller can barge in on them, which resets the count.
They are saved to the transcript but kept out of LLM history.

Hang-up (`_hang_up_after_playback`) waits for playback left + `HANG_UP_GRACE_S` = 0.5 s,
sends a `hangup` event with a reason (`goodbye`, `silence`, `transfer`), then calls the
adapter's `hang_up`. For a transfer, closing the stream is what lets Twilio continue to
`<Dial>` (see [actions.md](actions.md), `app/actions/transfer.py`).

### Greeting

- New call: `business_greeting()` = `AI_DISCLOSURE` ("Hi, I am an AI assistant.") + the
  owner's text from `business.settings["greeting"]`, with a duplicated disclosure stripped
  (DEC-17, DEC-53). Cached audio: in-process dict, then `greeting_audio` table, then TTS
  (and stored). `pregenerate` fills the table after a crawl.
- `continue_from`: earlier user/assistant messages become history, the first user message
  (≤ 60 chars) becomes the topic, greeting is "Welcome back to {name}. … pick up where we
  left off: {topic}." Not cached.
- `voice_name()` picks the brand name, or the profile name while the brand is a placeholder.

## 4. LLM use and the tool-calling loop

Both voice (`_reply_rounds`) and chat (`answer_question`, `stream_answer`) run the same shape:

```
for round in 1..MAX_TOOL_ROUNDS (3):
    stream/complete with messages + tools
    collect text and tool_call deltas (by index)
    no tool calls → done
    append assistant message (this round's text + tool_calls)
    run each call via run_tool → append role=tool message
give-up line: "Sorry, I couldn't find that/finish that. Can I take a message?"
```

| | Voice | Chat |
|---|---|---|
| Client | `get_async_chat_client()` (async, streamed) | `get_chat_client()` (sync; streamed or not) |
| Model / thinking | `LLM_MODEL`, `VOICE_REASONING_EFFORT` = `none` (DEC-29) | `LLM_MODEL`, `CHAT_REASONING_EFFORT` = `none` (V24) |
| RAG | Tool only: the model calls `search_business_info` (DEC-30), top 5 | **Pre-retrieval** of top 5 chunks into the system prompt, plus the same search tool |
| System prompt | `VOICE_SYSTEM_PROMPT` + local time + profile + tools rules + locations + summary, built once at call start | `SYSTEM_PROMPT` + tools rules + profile + locations + summary + chunks, built per request |
| History | In memory for the call; only heard text | Loaded from `messages` per request; the full answer |
| Tool context | `business_id`, `conversation_id`, `call_sid`, `caller` from the session | `business_id`, `conversation_id` only |
| Output cleanup | `speakable()` strips markdown, list marks, URLs (V14); sentence splitter with abbreviation list and a 120-char clause cut (T4b, V13) | None; SSE escapes newlines |
| Warm-up | `max_tokens=1` call with the prefix while the greeting plays (T2) | None |

Tool rules that matter for conversation logic (enforced in `app/actions/executor.py`, see
[actions.md](actions.md)): unknown tool or bad JSON returns an error string, never raises; an
optional tool not enabled for the business is refused; a side-effect tool without
`confirmed=true` returns "Not done … ask for an explicit yes", which drives the read-back step
in the prompt. `tools_for(business_id)` offers only the tools that business enabled (#151),
and `tools_prompt()` adds the booking/session rules only for those.

## 5. Prompts

| Prompt | Where | Key rules |
|---|---|---|
| `VOICE_SYSTEM_PROMPT` | `session.py` | Answer only from profile or search results; search for anything else; offer `take_message` / `transfer_to_human`; never mention profile/search; name the area for area-specific hours; 1–3 short spoken sentences, no lists/markdown/URLs; caller's language; warm goodbye. |
| `SYSTEM_PROMPT` | `prompt.py` | Same grounding rule, short answers, same language. |
| `BOOKING_PROMPT`, `SESSION_PROMPT` | `prompt.py` | Check before offering, read back, wait for yes, `confirmed=true`, never claim done unless the tool said so. |
| `format_summary` | `prompt.py` | Business summary as tone, not facts; guest themes as opinions; capped at 480 chars. |

## 6. Key functions

| Function | Notes |
|---|---|
| `CallSession.start()` | Raises `LookupError` for an unknown business; adapters map it to close code 4404. |
| `CallSession.feed(pcm)` | Any chunk size; in manual mode pushes straight to the open transcriber. |
| `CallSession.start_turn()` / `end_turn()` | Push-to-talk; repeated presses are ignored. |
| `CallSession.on_mark(name)` | Clears that mark and all earlier ones; sets real playback end when none remain. |
| `CallSession.close()` | Safe after the transport is gone; always ends the conversation row. |
| `_spawn(coro)` | Reply tasks are never awaited on the happy path; a done-callback logs failures and sends an `error` event. |
| `split_sentences(buffer)` | Returns complete sentences + unfinished tail. |
| `is_farewell(text)` | English regex on the last sentence (≤ 8 words, not a question). |
| `run_tool(...)` | Wraps `executor.execute` with an `ActionContext`. |
| `prepare_prompt(...)` | Shared by chat answers and `/debug/prompt`, so debug shows the exact prompt. |

## 7. Config

All in `app/config.py`, set by env var.

| Setting | Default | Effect here |
|---|---|---|
| `LLM_MODEL` | `google/gemma-4-12b` | Voice and chat model (DEC-32). |
| `VOICE_REASONING_EFFORT` / `CHAT_REASONING_EFFORT` | `none` | Thinking off; ~8 s → ~0.3 s to first word on LM Studio. |
| `VOICE_LLM_WARMUP` | `true` | T2 prompt-cache warm-up per call. |
| `VAD_END_SILENCE_MS` | 700 | End-of-turn silence (V3). |
| `VAD_BARGE_IN_AGGRESSIVENESS` / `VAD_BARGE_IN_START_MS` | 3 / 300 | False barge-in guard (V9/V10). |
| `VOICE_LANGUAGE`, `TTS_VOICE` | `en-US`, `en-US-Neural2-F` | Part of the greeting-audio cache key. |
| `STT_MODEL`, `STT_ALTERNATIVE_LANGUAGES` | `latest_short`, empty | Used by the STT client ([integrations.md](integrations.md)). |

Constants in code: `MAX_TOOL_ROUNDS` = 3 (both paths), `SEARCH_TOP_N` = 5 (V28, #108),
silence/mark/hang-up timings in `session.py`, chunking limits `_MAX_CHUNK` = 120,
`_MIN_CHUNK` = 30.

## 8. Error handling

| Failure | Behaviour |
|---|---|
| Unknown business | `LookupError` from `start()`; adapter closes with 4404. |
| Bad business timezone | Logged, UTC used (B6), call continues. |
| STT error | `error` event with the cause; turn dropped, call continues. |
| Earlier unanswered STT fails | Logged, the new turn's words are still used. |
| LLM/TTS error in a reply task | Done-callback logs it and sends `error`; call stays open. |
| Tool error | Error string returned to the model; side-effect failures logged in `action_log`. |
| Warm-up fails | Warning only; first turn is slower. |
| Greeting cache read/write fails | Warning; falls back to TTS. |
| Transport closed mid-reply | `_try_send_event` swallows send errors; `close()` catches reply failures so cleanup still runs. |
| Process killed (browser calls) | `end_orphaned_browser_conversations()` closes leftovers on startup (V19, in `browser_ws.py`). |

## 9. Known gaps

From reading the code; V numbers are the matching rows in
[plan/03-voice-channel.md](../plan/03-voice-channel.md) where one exists.

- **Noise still stops the bot (V10).** The DEC-44 guard was not enough in a live call
  (2026-10-19). A barge-in cancels the reply for good; there is no resume.
- **Send interleaving (V15).** Reply-task sends and `clear` from `feed()` share one socket
  with no send queue.
- **Thread use (V18).** STT holds an executor thread for a whole turn; TTS, DB and tools use
  `asyncio.to_thread` too. Limits concurrent calls per instance (stage 2–3).
- **STT model per transport (V4, V27).** One `STT_MODEL` for 8 kHz phone and 16 kHz browser audio.
- **Reasoning value on Gemini (V20).** `none` is verified only on LM Studio.
- **Local time is frozen at call start.** The voice system prompt's "current local time" is
  built once in `_load_business`; long calls drift. Chat has no current time line at all.
- **Voice and chat prompts differ.** Two system prompts, two RAG strategies (tool-only vs
  pre-retrieval + tool), different give-up lines. Changes to grounding rules must be made
  twice.
- **Tool loop is duplicated.** The delta-collecting loop exists in `session.py` and twice in
  `answer.py`; chat tool calls get no `call_sid`/`caller`, so a transfer from chat cannot work.
- **Farewell detection is English-only**, though the prompt answers in the caller's language.
- **Fallback lines are English-only**: re-prompt, silence goodbye, give-up, greeting
  disclosure.
- **Tool-round cap hides partial work.** If the model keeps calling tools for 3 rounds, the
  caller hears the give-up line even if an action already ran in an earlier round.
- **History is not trimmed.** Both paths send the whole conversation every turn; long
  conversations grow the prompt without a limit.
- **Gemini Live mode (DEC-30)** is not built; only the shared tool schemas are ready for it.

## Depends on / Used by

**Depends on**

| Layer doc | What this layer uses |
|---|---|
| [integrations.md](integrations.md) | `app/llm.py` (`get_async_chat_client`, `get_chat_client`, call logging), `app/voice/stt.py` (`TurnTranscriber`), `app/voice/tts.py` (`synthesize`, `pcm16_duration_s`), `google_auth.py` via those. |
| [data.md](data.md) | `app/rag/retrieve.py` (`retrieve`), `app/rag/location.py` (`default_location_id`, `format_locations`), `app/db` (`tenant_session`, `Business`, `BusinessProfile`, `BusinessSummary`, `Conversation`, `Message`, `GreetingAudio`, `message_order`), `app/ingest/profile.py` (`is_placeholder_name`). |
| [actions.md](actions.md) | `app/actions/registry.py` (tool registry, `tools_for`, `enabled_tool_names`), `executor.py` (`execute`), `transfer.py` (`TRANSFER_STARTED`), action modules imported so their tools register (`messages`, `transfer`, `booking.tools`). |

**Used by**

| Layer doc | Entry points it calls |
|---|---|
| [api.md](api.md) | `app/voice/ws.py` (Twilio) and `app/voice/browser_ws.py` create `CallSession` and call `start`/`feed`/`on_mark`/`start_turn`/`end_turn`/`close`; `app/api/chat.py` calls `answer_question`, `stream_answer`, `prepare_prompt`; `app/api/business_settings.py` calls `business_greeting`, `cache_status`, `pregenerate`, `prune` when the owner edits the greeting; `call_feed.py` receives the session's events through the Twilio adapter. |
| [crawler.md](crawler.md) | `app/ingest/run.py` calls `greeting_cache.pregenerate` when a crawl finishes. |
| [data.md](data.md) | `app/rag/eval.py` runs `answer_question` over the eval set. |
| [web.md](web.md) | Indirectly: consumes the event types listed below over `/voice/browser` and the SSE feed. |

**Event contract** sent through `send_event` (adapters and [web.md](web.md) depend on it):
`reply`, `transcript`, `no_speech`, `latency`, `clear`, `mark`, `hangup`, `error`; the browser
adapter adds `conversation_id`.

**Layer rule check.** The Voice layer calls [integrations.md](integrations.md) and is never called by
it. It imports one name back from [actions.md](actions.md) (`TRANSFER_STARTED`) while actions are
otherwise called through the registry; `app/voice/tools.py` living under `voice/` but also
serving chat is a naming leftover, not a dependency problem.
