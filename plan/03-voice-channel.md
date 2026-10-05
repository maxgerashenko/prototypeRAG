# Part 3 — Voice channel (phone calls)

**Goal:** a customer calls a phone number and has a spoken conversation with the
assistant, which answers from the business's RAG (Part 2) and can trigger actions (Part 4).

**Done when:** a Twilio call (Voice SDK from the browser — no phone number, DEC-34)
asking "what time do you close on Saturday?" returns a correct spoken answer with under
~1.5 s response delay. A real phone number is rented only when a pilot business pays for it.

---

## Call flow

```
Caller ──PSTN──► Twilio number   (stages 1–2: browser ──Voice SDK──► TwiML App, no number — DEC-34)
                    │ 1. HTTP webhook POST /twilio/voice  (which number was called → business_id;
                    │    caller still hears ringing here, and this query wakes the DB)
                    │    ◄── TwiML: <Connect><Stream url="wss://.../voice/ws"/>
                    │ 2. WebSocket: bidirectional audio (8 kHz μ-law, 20 ms frames)
                    ▼
            Voice service (FastAPI WebSocket)
              ├─ VAD: detect when caller stops speaking
              ├─ STT: audio → text (streaming)
              ├─ RAG + tools (Part 2 / Part 4) → reply text (streaming)
              ├─ TTS: text → audio (streaming, sentence by sentence)
              └─ send audio back to Twilio; stop playback if caller interrupts (barge-in)
```

## Components

| Step | Local | Cloud |
|---|---|---|
| Telephony | Twilio **Voice SDK** browser call (no number) + **ngrok** tunnel to local API | same → Cloud Run URL; real number once a business pays for it |
| VAD | webrtcvad (`webrtcvad-wheels`; Silero would add onnxruntime — not needed in stage 1) | same |
| Speech-to-Text | **Google Speech-to-Text** (streaming), called from the laptop | same |
| Text-to-Speech | **Google Text-to-Speech** (Neural2/Chirp voices), called from the laptop | same |
| All-in-one alternative | **Gemini Live API** (audio in → audio out, with tool calling) | same |

Speech runs on Google's APIs **also locally** — no local speech models (Whisper, Piper).
They would be local-only tech to replace later, and speech costs only cents while
testing. The LLM behind the voice still follows Part 2 (LM Studio locally, Gemini in cloud).
Google Speech uses native Google SDKs (not the OpenAI-compatible API); authenticate
locally with `gcloud auth application-default login`, in cloud with the service account.

## Two voice modes, one set of tools (DEC-30)

| | **Pipeline mode** | **Live mode** |
|---|---|---|
| Flow | Google STT → LLM (text) → Google TTS | Gemini Live: audio in → audio out in one streaming session |
| Where | Stage 1 local (LM Studio model) and cloud (Gemini Flash) | Cloud only — no local equivalent (R21) |
| VAD / barge-in | ours (Silero/webrtcvad) | built into Live |
| Latency | sum of stages (budget below) | usually lower — fewer hops |
| Control / debugging | every stage visible (transcripts, timings) | less visible per stage |
| SDK | Google Speech SDK + OpenAI-compatible LLM client | native Gemini Live SDK (R5) |

- **RAG is exposed as a tool** — `search_business_info(query)` — next to the action
  tools (Part 4). The same tool definitions work in pipeline mode (OpenAI-compatible
  `tools=[...]`) and in Live mode (function calling), so switching modes doesn't change
  business logic. The business profile (hours, address, rules) is small and always goes
  into the system instruction.
- **Stage 1** builds pipeline mode locally — the learning goal is to see every stage.
- **Stage 2** runs pipeline mode on Cloud Run with Gemini Flash (thinking off), then
  spikes Live mode on the same number and **decides by measurement** (latency, quality,
  cost per minute — OPEN-08).
- Live mode audio formats: Twilio sends 8 kHz μ-law; Live expects 16 kHz PCM input and
  returns 24 kHz PCM → resample both ways in `audio.py`. Live sessions have duration
  limits — check current limits and session-resumption options for long calls.

Telephony alternatives: Telnyx, Vonage, Plivo — compare per-minute price and number
availability in the target country. Keep telephony behind an interface so the provider
can be swapped.

## Latency budget (target < 1.5 s)

| Stage | Budget |
|---|---|
| End-of-speech detection (VAD) | 300–500 ms |
| STT final text | 200–300 ms |
| Retrieval | < 100 ms |
| LLM first sentence | 300–500 ms |
| TTS first audio | 150–300 ms |

First measurement (stage 1, 2026-10-03, gemma-4-12b in LM Studio, thinking off, fake
STT/TTS — so LLM + tool only): answer from the profile alone ≈ 1.2–1.3 s to the first
sentence; with a `search_business_info` call ≈ 5 s (1.8 s to emit the tool call, 3.2 s
to answer over 5 chunks; the search itself ≈ 40 ms). Tool turns are the gap to close —
filler, fewer/shorter chunks, or a faster model. With thinking on (gemma's default) it
was 8–18 s.

Techniques: stream everything, start TTS on the first complete sentence, keep prompts
short, short spoken-style answers, play a filler ("one moment…") when a tool call runs.

## Conversation behaviour

- Greeting per business: "Hi, you've reached <name>. I'm an AI assistant. How can I
  help?" — pre-generated TTS audio stored at onboarding, so it plays instantly (DEC-17).
  AI disclosure always; add "this call may be recorded" only if recording is enabled
  (off by default, DEC-22, R15).
- Barge-in: caller speaking stops bot audio immediately.
- Silence handling: re-prompt after ~6 s, hang up politely after repeated silence.
- Fallback: transfer to the business's human number (Twilio `<Dial>`) or take a message.
- End of call → transcript saved to `conversations` → Part 4 summary.

## Ways to connect (demo modes) — one pipeline behind all of them

| Mode | Caller connects by | Reaches our server as | Business identified by | Cost (≈ 2026) |
|---|---|---|---|---|
| **A. Phone** | Dialling a Twilio number | `POST /twilio/voice` → WebSocket `/voice/ws` (8 kHz μ-law) | Dialled number (`To`) → `businesses.phone_numbers` | Per minute + number rental (≈ $1.15/month) — only during a Twilio trial or once a business pays (DEC-34) |
| **B. Browser via Twilio** (Voice SDK) | "Call" button on a web page → TwiML App | **Same** `POST /twilio/voice` → `/voice/ws` | Custom parameter sent by the page (`business_id`) | ≈ $0.004/min, no number; needs an active Twilio account |
| **C. Browser direct** (voice app / debug page) | "Call about …" in the voice app at `/web/` (server push-to-talk, DEC-42), or "Talk" on `web/voice-debug.html` (open mic, VAD) | Our own WebSocket `/voice/browser` (PCM16) — no Twilio | Query parameter on the WebSocket URL | $0 (only Google Speech usage) |

Design rule: **transport adapters are thin; the call loop doesn't know which one is used.**
`session.py` runs the pipeline (VAD → STT → LLM + tools → TTS) on PCM16 frames in and out.
Each adapter only converts its wire format to/from those frames and resolves `business_id`:
`ws.py` (Twilio Media Streams JSON + base64 μ-law, modes A and B) and `browser_ws.py`
(binary PCM16, mode C). Switching demo mode = using a different number/page/URL — no code.

Twilio trial: use its number for mode-A demos while the trial lasts (check in the Twilio
console what ends when). After it ends, demo with mode C ($0); upgrade (≈ $20 prepaid,
usage credit) only to show mode B; rent a number again when a business pays. A new number
will differ from the trial one — don't publish the trial number.

## Local development setup

1. Twilio account (trial credit is enough to start; auto-recharge off — DEC-33). No
   phone number: create a **TwiML App** + API key, and a page using the Twilio Voice
   JS SDK that calls it (≈ $0.004/min, DEC-34).
2. `ngrok http 8000` → set the TwiML App's voice URL to `https://<ngrok>/twilio/voice`.
   The same webhook later serves a real number unchanged.
3. Before phone: browser calls (mode C → `/voice/browser`) with the same voice pipeline, so
   STT/TTS can be tuned without spending call minutes: the voice app at `/web/` (push-to-talk,
   DEC-42) or `web/voice-debug.html` (open mic + raw event log).

## Code layout

```
app/voice/
  twilio_routes.py   POST /twilio/voice (TwiML), POST /twilio/status (call end → summary)
  ws.py              WebSocket /voice/ws — Twilio Media Streams adapter (modes A, B)
  browser_ws.py      WebSocket /voice/browser — direct browser adapter (mode C), no Twilio
  session.py         per-call state + call loop on PCM16 frames, transport-agnostic
  audio.py           μ-law ↔ PCM, resampling 8k ↔ 16k
  vad.py
  stt.py             Google Speech-to-Text streaming
  tts.py             Google Text-to-Speech
  live.py            Live mode: Gemini Live session, same tools (stage 2)
  tools.py           shared tool definitions: search_business_info + Part 4 actions
web/index.html       voice app (mode C, push-to-talk, DEC-42) — web/src/app/; audio + socket in web/src/voice/voiceCall.ts
web/voice-debug.html open-mic debug client with raw event log (mode C, VAD) — web/src/voice-debug/ (DEC-41)
web/call.html        Twilio Voice SDK call page (mode B) — React + TS like the others
```

## Known problems — test or fix when the environment allows

Found while building step 4 without Google credentials or Twilio (2026-10-03).
**Real** = seen or confirmed in code/data; **Potential** = likely, not yet observed.
Re-check each item once its environment exists, then tick it or turn it into a task.

### Needs Google ADC (`gcloud auth application-default login`)

| # | Problem | Kind | How to test | Fix idea if it fails |
|---|---|---|---|---|
| V1 | `stt.py` / `tts.py` never called live — only against fake clients (v1 streaming helper, auth, response shape) | Potential | Mic test: one question end to end | Fix against the real responses |
| V2 | STT final result after speech end may exceed the 200–300 ms budget; each turn opens a new gRPC stream (setup should hide behind speech) | Potential | `stt_ms` in the latency event, 10+ turns | Keep one stream per call; `single_utterance` |
| V3 | `finish()` waits up to 5 s, then silently uses the interim text | Potential | Log line "STT final result not in…" | Lower timeout; tune VAD `end_silence_ms` |
| V4 | `STT_MODEL=latest_short` vs `phone_call` on 8 kHz μ-law audio — accuracy unknown | Potential | Same questions via mic and via Twilio, compare transcripts | Per-transport STT model |
| V5 | `TTS_VOICE=en-US-Neural2-F` may be retired/renamed; TTS may not return the exact requested rate (8000/16000) → `ValueError` | Potential | First TTS call at both rates | Pick a current voice (Chirp 3 HD?); resample instead of failing |
| V6 | Greeting is synthesized at every call start, not pre-generated (DEC-17) → caller waits one TTS round trip | Real | Time from connect to first audio | Cache greeting audio per business (onboarding) |
| V7 | Missing credentials surface only after ~3 s (auth probes the metadata server), and again on every turn | Real | Seen in smoke test | Check ADC once at startup / fail fast |
| V8 | STT is fixed to `VOICE_LANGUAGE`, but the prompt says "answer in the caller's language" — a non-English caller won't be transcribed | Real (design gap) | Ask a question in another language | `alternative_language_codes`, or language per business |

**Checked live 2026-10-03** (ADC on project `prototype-rag-max`; TTS output and the
speech fixture streamed at real-time pace):

- **V1 ✅** STT and TTS work live through `stt.py`/`tts.py` unchanged.
- **V2 ✅** STT final text 90–180 ms after audio end (budget 200–300 ms).
- **V4 partly** — on the clean 8 kHz fixture both `latest_short` (90 ms) and `phone_call`
  (178 ms) transcribe correctly; real phone audio still untested (needs Twilio).
- **V5 ✅** `en-US-Neural2-F` exists; 8 and 16 kHz come back at the requested rate.
  Warm TTS 170–310 ms per sentence (budget 150–300 ms, at the edge); the **first** call
  per process took ~700 ms (client + auth setup) — supports V6: warm the TTS client at
  startup and pre-generate the greeting.
- STT turns "five pm" into "5:00 p.m." — the sentence splitter must not cut at "p.m." (V13).

**First browser mic test 2026-10-03** (Chrome, headphones, gemma-4-12b thinking off, pilot
business, 7 turns). Times are from the VAD's speech end; the caller also waits the
500 ms end-of-speech silence before that.

| Turn type | STT | First sentence (LLM) | First audio | Turns |
|---|---|---|---|---|
| Profile only, first turn of call | 85 ms | 2.6 s | 2.9 s | 1 |
| Profile / history only, later turns | 65–111 ms | 0.95–2.1 s | 1.2–2.3 s | 3 |
| With `search_business_info` | 90–102 ms | 5.4–5.5 s | 5.7 s | 2 |

Findings:
- **V16 confirmed** — search turns ≈ 5.7 s to first audio: 1.6–1.8 s until the tool call,
  search ≈ 40 ms, then ≈ 3.7 s until the first sentence of the answer. The main gap.
- **First turn is cold** (2.6 s vs ~1 s later) — LM Studio has not cached the system
  prompt yet. Idea: warm it during the greeting (one `max_tokens=1` request).
- **Pause mid-thought splits a turn**: "What if I want to come?" … "Today, night at
  9 pm" — the 500 ms end-silence ended the turn, the reply was cancelled by barge-in when
  the caller continued (works as designed; history keeps both parts). Watch whether
  this happens often → longer `end_silence_ms` costs latency on every turn.
- **"Thank you. Bye." doesn't end the call** — needs an end-call path (silence/hang-up task).
- **V21 confirmed** in the greeting ("business_name").
- Barge-in fired 3× with headphones, each when the caller started speaking — no false
  trigger seen; V9 (echo without headphones) still untested.
- To check: "Day Pass starts at $39" was answered **without** a search — verify it comes
  from the profile (`price_range`), not from model knowledge.


| # | Problem | Kind | How to test | Fix idea if it fails |
|---|---|---|---|---|
| V9 | Speaker echo → VAD hears the bot → false barge-in; browser echo cancellation may not cover WebAudio playback | Potential | Mic test without headphones | Require STT text before `clear`; raise VAD aggressiveness while bot speaks |
| V10 | Any noise (cough, "mm-hm") stops the bot for good — no resume | Real (by design) | Cough during an answer | Minimum speech length / confirmed words before barge-in |
| V11 | Playback end is *estimated* from audio duration sent (`_playing_until`); drifts with network/client buffering | Potential | Barge-in right after the bot stops | Client reports playback end; Twilio `mark` events |
| V12 | `AudioContext({sampleRate: 16000})` — fails in Firefox (mixed sample rates); untested in Safari | Potential | Open page in Chrome, Safari, Firefox | Capture at native rate, resample server-side |
| V13 | Sentence splitter cuts at "Dr. " / "5 p.m. on", and a long reply without punctuation is only spoken at the end | Potential | Look at `reply` events of real answers | Abbreviation list; split also on `,`/length cap |
| V14 | LLM may still output markdown, lists or URLs, which TTS reads aloud | Potential | Ask for "address and website" | Strip markdown before TTS |
| V15 | Reply-task sends and feed-path sends (`clear`) can interleave on one WebSocket | Potential | Barge-in many times quickly | One send queue per connection |

**Fixed or closed in code 2026-10-05** (stage 1 tuning, the locally testable items not owned
by T1–T4b or B1–B6; unit tests with fakes, live re-check on the Mac still to do):

- **V3 ✅ code** `finish()` waits at most 2 s (`stt.FINISH_TIMEOUT_S`), not 5 s — the final
  result came in 90–180 ms live. VAD `end_silence_ms` tuning stays open.
- **V7 ✅ code** `app/voice/google_auth.py` checks ADC once per process and caches the
  outcome; the STT/TTS client getters call it, so a missing login fails at once with
  "run `gcloud auth application-default login`" instead of ~3 s per turn. Checked at app
  startup in the background (warning in the log). An STT failure now sends an `error`
  event instead of leaving the client waiting.
- **V14 ✅ code** `speakable()` in `session.py` strips markdown, line-start bullets and
  numbers, and turns URLs into their domain before TTS; the transcript and saved reply use
  the same plain text. A sentence that is only markup is skipped.
- **V15 ✅ not a problem** — every uvicorn WebSocket implementation writes a whole message
  to the transport in one synchronous call, so two sends can't interleave mid-message, and
  `_on_speech_start` awaits the cancelled reply task before sending `clear`, so no audio
  of that reply follows it. The Twilio adapter must keep that order (cancel, then `clear`).

### Latency (LM Studio, measured with fake STT/TTS — see "Latency budget")

| # | Problem | Kind | How to test | Fix idea if it fails |
|---|---|---|---|---|
| V16 | Turns with `search_business_info` take ≈ 5 s to the first sentence (1.8 s to emit the tool call, 3.2 s over 5 chunks) — budget is 1.5 s | Real | `latency` events | Filler phrase while the tool runs; fewer/shorter chunks; LM Studio prompt caching; faster model; compare Gemini (step 6) |
| V17 | Silence re-prompt / hang-up not built yet (task above) | Real | — | — |

### Needs Twilio / later stages

| # | Problem | Kind | How to test | Fix idea if it fails |
|---|---|---|---|---|
| V18 | STT/TTS each hold a thread from the default executor (`asyncio.to_thread`); an STT stream holds one for a whole turn → limits concurrent calls per instance | Potential (stage 2–3) | Load test with several calls | Async Google clients; dedicated executor |
| V19 | `conversations.ended_at` stays NULL if the process dies mid-call | Potential | Kill the server during a call | Twilio status callback sets it (DEC-24) |
| V20 | `VOICE_REASONING_EFFORT=none` is only verified against LM Studio; Gemini's accepted value depends on the model | Potential | Step 6 Gemini comparison | Map per provider in `.env` |

### Data / other parts (found through voice testing)

| # | Problem | Kind | Where it's tracked |
|---|---|---|---|
| V21 | Greeting says "you've reached business_name" — profile `name` holds the placeholder; `businesses.name` is "(pending)" | Real | [01-crawler.md](01-crawler.md) tasks |
| V22 | `businesses.timezone` is never set by the crawler → stays `UTC`; the voice prompt's "current local time" is wrong for the pilot (New York) | Real | [01-crawler.md](01-crawler.md) tasks |
| V23 | "Saturday opening hours" answered "don't have it" — unknown whether the hours are missing from the data or retrieval missed them | Real (cause unknown) | `/debug/retrieve`, eval set |
| V24 | `/chat` doesn't send `reasoning_effort` → likely the same ~8 s thinking delay as voice had — **fixed 2026-10-05**: `CHAT_REASONING_EFFORT` (default `none`); measuring is still open | Potential | [02-local-rag.md](02-local-rag.md) tasks |
| V25 | Smoke tests left 4 test `voice` conversations on the pilot business | Real | Delete when convenient |

## Tasks

- [x] Audio utilities (μ-law/PCM, resampling) + tests
- [x] Google STT/TTS streaming code (`stt.py` one stream per VAD turn, `tts.py` per sentence) — live check pending gcloud ADC
- [x] Transport-agnostic call loop in `session.py` (PCM16 in/out, tool loop, sentence-by-sentence TTS) + tests with fake STT/TTS/LLM
- [x] Mic test adapter + page (mode C, `browser_ws.py`, `web/mic-test.html`)
- [x] Voice app from the design (DEC-42): businesses → past conversations → push-to-talk call → transcript, continue an earlier call; React app in `web/` merged with main's backend (2026-10-05); Vitest + Playwright tests
- [ ] Voice app: a real call with STT/TTS + LM Studio through the React app (only mocked so far)
- [x] Google STT/TTS checked live (ADC set up; results under "Known problems")
- [x] Mic test end-to-end in the browser with real STT/TTS + LM Studio (results under "Known problems")
- [ ] Twilio webhook + TwiML + media stream WebSocket — tested with the trial number (mode A) and a Voice SDK browser call (mode B)
- [x] VAD (`TurnDetector`, tested on real speech)
- [x] Turn-taking, barge-in in the call loop (playback end estimated from audio duration sent)
- [ ] Silence re-prompt (~6 s) and polite hang-up; filler while a tool runs, if latency needs it
- [ ] Called number / TwiML App → business_id mapping from Postgres
- [ ] Call transfer + take-a-message fallback
- [x] Transcript saved to `messages` per turn (survives a dropped call), `ended_at` set on hang-up
- [ ] Summary runs in the Twilio status callback (DEC-24)
- [x] Measure latency per stage and log it per turn (`latency` event + log: stt, tool, first sentence, first audio)
- [x] Record real latency numbers against the budget above (first mic test; repeat over Twilio)
- [x] `search_business_info` tool + shared tool definitions (used by both modes)
- [ ] Stage 2: Live mode spike on Cloud Run; compare with pipeline mode (OPEN-08)

### Test on Mac (stage 1)

Checks that need the 64 GB Mac (LM Studio, Google ADC, a real mic and browsers). The
cloud sessions can only run unit tests with fakes. Record results under "Known problems".

- [ ] **Real call through the React app** (`/web/`, push-to-talk): STT/TTS + LM Studio end
  to end — the open task above
- [ ] **V7** Run the server with ADC removed (`gcloud auth application-default revoke`
  or `GOOGLE_APPLICATION_CREDENTIALS=/nonexistent`): one startup warning, the first call
  shows the "run gcloud auth application-default login" error at once, no ~3 s stall per turn
- [ ] **V3** 10+ turns: no "STT final result not in after 2.0s" log line; if it shows,
  compare the transcript with what was said
- [ ] **V14** Ask for "the address and the website": no markup or full URL read aloud;
  the transcript shows the same plain text
- [ ] **V24** Time `/chat` (and `/chat/stream` first token) with `CHAT_REASONING_EFFORT=none`
  vs thinking on; also re-run the eval set — same task in [02-local-rag.md](02-local-rag.md)
- [ ] **V9** VAD mode (`voice-debug.html`) without headphones: does the bot's own voice
  trigger a barge-in?
- [ ] **V10** Cough or say "mm-hm" during an answer (VAD mode): how often does it stop the bot?
- [ ] **V12** Open `/web/` in Safari and Firefox: does `AudioContext({sampleRate: 16000})`
  work, does the mic stream?

## Notes

- Phone audio is 8 kHz — test STT on real call audio, not just clean mic audio.
- Twilio charges per minute; a phone number adds a monthly rental (any provider) —
  that's why testing uses Voice SDK calls (DEC-34). Keep test calls short.
- WebSocket calls need the server to stay up for the call's duration — relevant for
  Cloud Run settings (timeout 3600 s; `min-instances=0` in every stage, cold start
  happens during ringing — measured in stage 2, DEC-35, R19).
- No generic "please wait while I load the assistant" message: the DB wake-up happens
  during ringing (DEC-17, R3).
