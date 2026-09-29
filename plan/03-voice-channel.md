# Part 3 — Voice channel (phone calls)

**Goal:** a customer calls a phone number and has a spoken conversation with the
assistant, which answers from the business's RAG (Part 2) and can trigger actions (Part 4).

**Done when:** calling a real test number, asking "what time do you close on Saturday?"
returns a correct spoken answer with under ~1.5 s response delay.

---

## Call flow

```
Caller ──PSTN──► Twilio number
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
| Telephony | Twilio trial number + **ngrok** tunnel to local API | Twilio → Cloud Run URL |
| VAD | Silero VAD / webrtcvad (small library inside the app) | same |
| Speech-to-Text | **Google Speech-to-Text** (streaming), called from the laptop | same |
| Text-to-Speech | **Google Text-to-Speech** (Neural2/Chirp voices), called from the laptop | same |
| All-in-one alternative | **Gemini Live API** (audio in → audio out, with tool calling) | same |

Speech runs on Google's APIs **also locally** — no local speech models (Whisper, Piper).
They would be local-only tech to replace later, and speech costs only cents while
testing. The LLM behind the voice still follows Part 2 (LM Studio locally, Gemini in cloud).
Google Speech uses native Google SDKs (not the OpenAI-compatible API); authenticate
locally with `gcloud auth application-default login`, in cloud with the service account.

**Recommended path:** build the classic pipeline (STT → RAG → TTS) first to understand
every piece, then try Gemini Live as a replacement: it removes two services and usually
cuts latency, while RAG context and tools are still passed in.

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

## Local development setup

1. Twilio trial account, buy a number (trial can only call verified numbers).
2. `ngrok http 8000` → set the number's voice webhook to `https://<ngrok>/twilio/voice`.
3. Before phone: a **local mic test mode** (browser WebRTC or CLI) using the same voice
   pipeline, so STT/TTS can be tuned without spending call minutes.

## Code layout

```
app/voice/
  twilio_routes.py   POST /twilio/voice (TwiML), POST /twilio/status (call end → summary)
  ws.py              WebSocket /voice/ws — call session loop
  session.py         per-call state: business_id, history, audio buffers
  audio.py           μ-law ↔ PCM, resampling 8k ↔ 16k
  vad.py
  stt.py             Google Speech-to-Text streaming
  tts.py             Google Text-to-Speech
  live.py            Gemini Live implementation (later)
web/mic-test.html    browser mic test client
```

## Tasks

- [ ] Audio utilities (μ-law/PCM, resampling) + tests
- [ ] Google STT/TTS streaming (local auth via gcloud ADC)
- [ ] Local mic test mode end-to-end with Part 2 RAG
- [ ] Twilio webhook + TwiML + media stream WebSocket
- [ ] VAD, turn-taking, barge-in
- [ ] Phone number → business_id mapping from Postgres
- [ ] Call transfer + take-a-message fallback
- [ ] Save transcript at call end; summary runs in the Twilio status callback (DEC-24)
- [ ] Measure latency per stage and log it per turn
- [ ] Spike: Gemini Live API version

## Notes

- Phone audio is 8 kHz — test STT on real call audio, not just clean mic audio.
- Twilio charges per minute (number rental + inbound minutes); keep test calls short.
- WebSocket calls need the server to stay up for the call's duration — relevant for
  Cloud Run settings (timeout 3600 s; stage 2 measures cold starts with `min-instances=0`,
  stage 3 sets `min-instances=1` — DEC-16, R19).
- No generic "please wait while I load the assistant" message: the DB wake-up happens
  during ringing (DEC-17, R3).
