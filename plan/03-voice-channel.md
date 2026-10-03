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
| **C. Browser direct** (mic test page) | "Talk" button on `web/mic-test.html` | Our own WebSocket `/voice/browser` (PCM16) — no Twilio | Query parameter on the WebSocket URL | $0 (only Google Speech usage) |

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
3. Before phone: a **local mic test mode** (mode C: `web/mic-test.html` → `/voice/browser`)
   using the same voice pipeline, so STT/TTS can be tuned without spending call minutes.

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
web/mic-test.html    browser mic test client (mode C)
web/call.html        Twilio Voice SDK call page (mode B)
```

## Tasks

- [x] Audio utilities (μ-law/PCM, resampling) + tests
- [ ] Google STT/TTS streaming (local auth via gcloud ADC)
- [ ] Transport-agnostic call loop in `session.py` (PCM16 in/out)
- [ ] Local mic test mode end-to-end with Part 2 RAG (mode C, `browser_ws.py`)
- [ ] Twilio webhook + TwiML + media stream WebSocket — tested with the trial number (mode A) and a Voice SDK browser call (mode B)
- [x] VAD (`TurnDetector`, tested on real speech)
- [ ] Turn-taking, barge-in in the call loop
- [ ] Called number / TwiML App → business_id mapping from Postgres
- [ ] Call transfer + take-a-message fallback
- [ ] Save transcript at call end; summary runs in the Twilio status callback (DEC-24)
- [ ] Measure latency per stage and log it per turn
- [x] `search_business_info` tool + shared tool definitions (used by both modes)
- [ ] Stage 2: Live mode spike on Cloud Run; compare with pipeline mode (OPEN-08)

## Notes

- Phone audio is 8 kHz — test STT on real call audio, not just clean mic audio.
- Twilio charges per minute; a phone number adds a monthly rental (any provider) —
  that's why testing uses Voice SDK calls (DEC-34). Keep test calls short.
- WebSocket calls need the server to stay up for the call's duration — relevant for
  Cloud Run settings (timeout 3600 s; `min-instances=0` in every stage, cold start
  happens during ringing — measured in stage 2, DEC-35, R19).
- No generic "please wait while I load the assistant" message: the DB wake-up happens
  during ringing (DEC-17, R3).
