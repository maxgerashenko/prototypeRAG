# Voice tuning — everything that affects speed and quality of replies

One place to find every knob that changes how fast or how good a reply is, in the order a
turn flows: **caller audio → turn detection (VAD) → STT → LLM (+ retrieval tool) → sentence
splitting → TTS → playback**. The `/chat` text path shares the LLM, retrieval and prompt
parts. Measure first (see "How to measure"), change one knob at a time, and write the
result in [03-voice-channel.md](03-voice-channel.md) "Known problems" (V-items), as usual.

Values are the current defaults in the code; numbers are approximate and from stage 1 on a
64 GB Mac (LM Studio) — re-measure after any change of model or machine. Latency budget
target: < 1.5 s from end of speech to first audio ([03-voice-channel.md](03-voice-channel.md)
"Latency budget").

**Where a knob lives:** *env* = `.env` / [app/config.py](../app/config.py) (restart the
server); *code* = a constant in the file named (change needs a code edit, so a PR);
*prompt* = text sent to the model; *external* = LM Studio / Google / Twilio side.

## 1. Turn detection (VAD) — when the caller is "done"

Affects **speed** (every turn waits this long) and **quality** (too short cuts the caller
mid-sentence; too eager catches noise). Push-to-talk (`/web/`) skips VAD for ending turns.

| Knob | Default | Where | Effect / trade-off |
|---|---|---|---|
| `VAD_END_SILENCE_MS` | 700 | env | Silence that ends a turn. Lower = faster reply, more cut-off sentences (V3, DEC-44). Budget was 300–500 ms |
| `VAD_BARGE_IN_AGGRESSIVENESS` | 3 | env | webrtcvad strictness while the bot speaks (0–3). Higher = fewer false barge-ins from echo/cough (V9, V10), but a real interruption is harder |
| `VAD_BARGE_IN_START_MS` | 300 | env | Minimum speech length before barge-in fires. Higher = fewer false stops, slower real interrupt |
| VAD `aggressiveness` (normal) | 2 | code: `TurnDetector` in [app/voice/vad.py](../app/voice/vad.py) | How strict "this is speech" is when the bot is silent; not an env var |
| VAD `start_ms` | 160 | code, same | Speech length to start a turn |
| VAD `preroll_ms` | 300 | code, same | Audio kept from before speech start so the first syllable isn't lost |
| VAD `frame_ms` | 20 | code, same | Frame size for webrtcvad (10/20/30) |
| Silence re-prompt / hang-up | 6 s, 1 re-prompt | code: `SILENCE_*` in [app/voice/session.py](../app/voice/session.py) (V17) | Call-flow, not reply speed; VAD (phone) mode only |
| Playback-end tuning | `MARK_GRACE_S` 1.0, `HANG_UP_GRACE_S` 0.5 | code, session.py | When the bot counts as "done speaking" (affects barge-in window and silence timer) |

## 2. Speech-to-text (Google STT)

Affects **quality** of what the LLM sees (a wrong transcript is a wrong answer) and
**speed** (time to final text, budget 200–300 ms).

| Knob | Default | Where | Effect / trade-off |
|---|---|---|---|
| `STT_MODEL` | `latest_short` | env | `phone_call` is tuned for 8 kHz Twilio audio and may be more accurate there; compare on real calls (#73, V4/V27). Model choice also changes price/latency |
| `VOICE_LANGUAGE` | `en-US` | env | Fixed language for STT **and** TTS; a non-English caller isn't transcribed (V8, #75). Alternative languages / auto-detect are a code change |
| Automatic punctuation | on | code: [app/voice/stt.py](../app/voice/stt.py) | Helps the LLM read the question |
| Speech adaptation (phrase hints) | not used | — | Idea: send the business name, staff and service names as hints for better recognition of proper nouns |
| Interim results | on | code, stt.py | Lets the last interim text stand in if the final is late |
| `FINISH_TIMEOUT_S` | 2.0 | code, stt.py | How long to wait for the final text after the turn ends; long = slow worst case, short = truncated transcript |
| Sample rate | 16 kHz browser, 8 kHz Twilio | code: `BROWSER_SAMPLE_RATE` in [app/voice/browser_ws.py](../app/voice/browser_ws.py) | Telephone audio is 8 kHz by nature; not tunable for phone calls |

## 3. LLM — the biggest lever on speed

Time to the first sentence is dominated by the model, the prompt size and whether the model
"thinks".

| Knob | Default | Where | Effect / trade-off |
|---|---|---|---|
| `LLM_MODEL` | `google/gemma-4-12b` | env | Model size/quality vs speed. Bigger = better answers, slower first word. Cloud: Gemini Flash (stage 2 comparison) |
| `LLM_BASE_URL` / `LLM_API_KEY` | LM Studio | env | Local vs Gemini endpoint — same code |
| `VOICE_REASONING_EFFORT` | `none` | env | Thinking off: ~0.3 s to first word vs ~8 s on (DEC-29). Turn it on only for offline jobs. Gemini Pro can't turn thinking off |
| `CHAT_REASONING_EFFORT` | `none` | env | Same for `/chat` (V24) |
| `VOICE_LLM_WARMUP` | `true` | env | T2: primes LM Studio's prompt cache while the greeting plays; saves the first turn's prompt processing. Extra request per call (cost on a paid API) |
| Cold model start | — | external | First call after a restart took 34 s on `/chat` (#81); keep the model loaded (no TTL unload), check `lms ps`, avoid memory pressure from a second big model |
| LM Studio load settings | context 262144, parallel 4 | external | A huge context and more parallel slots cost memory; a smaller context frees RAM and can speed up. Quantization, GPU offload, flash attention and KV-cache type are set in LM Studio, not here |
| `MAX_TOOL_ROUNDS` | 3 | code: session.py | LLM → tool → LLM loops per turn; each round costs a full LLM call |
| Streaming | on | code | The reply streams, so TTS starts on the first sentence — keep it |
| History sent each turn | whole call history | code: `_history` in session.py | Longer calls = bigger prompt = slower; a cap/summary would help long calls (not built) |
| `max_tokens` / `temperature` | not set (model default) | code | A `max_tokens` cap would bound runaway replies; lower temperature = steadier answers. Not used today |
| `FACTS_LLM_MODEL` | empty = `LLM_MODEL` | env | Offline fact extraction only; a stronger model here improves **quality** of stored facts without touching call speed |

## 4. Prompt and what is put into it

Affects **quality** (accuracy, tone, length) and **speed** (every character is processed on
every turn — though LM Studio's prompt cache makes the unchanged prefix cheap).

| Knob | Where | Effect / trade-off |
|---|---|---|
| `VOICE_SYSTEM_PROMPT` (rules: answer only from the data, 1–3 short sentences, no markdown, same language as caller, say which area hours belong to) | prompt: session.py | The main quality lever. Short spoken answers also cut LLM and TTS time. Known gaps: "the profile" leaks, one area's hours given as the business's (V29, #109) |
| Business profile | prompt: `format_profile` | Always in the prompt → common questions need no tool call (saves ~3 s, see V16). Keep it short and true |
| Location card (DEC-49) | prompt: `format_locations` | Default location + address list; lets the model answer per-location questions without searching |
| Business summary | prompt: `format_summary` | Short overview; costs prompt size |
| Current local time | prompt; needs `businesses.timezone` | Needed for "are you open now" (V22, #79) |
| Markdown stripping (V14) | code: session.py | Safety net when the model ignores "no markdown" — otherwise TTS reads symbols aloud |
| Tool definition `search_business_info` | code: [app/voice/tools.py](../app/voice/tools.py) | Its description decides *when* the model searches; a vague one causes needless tool calls (slow) or missed ones (wrong "I don't know") |

## 5. Retrieval (RAG tool, shared with `/chat`)

Search itself is fast (≈ 40 ms); what matters is **what comes back and how much** — the
tool result is what the LLM reads on its second call (≈ 3 s for 5 chunks, V16).

| Knob | Default | Where | Effect / trade-off |
|---|---|---|---|
| `SEARCH_TOP_N` | 5 | code: tools.py (`top_n` for `/chat` in [app/rag/answer.py](../app/rag/answer.py)) | Fewer chunks = faster second LLM call but may miss the answer; more = slower and noisier |
| Candidates per search (vector / keyword) | 10 + 10 | code: [app/rag/retrieve.py](../app/rag/retrieve.py) | Pool before fusion |
| RRF `k` | 60 | code, retrieve.py | How strongly top ranks win when fusing vector and keyword lists |
| `CUSTOM_REPLY_BOOST` / `FACT_BOOST` | 2.0 / 1.5 | code, retrieve.py | Owner replies > facts > scraped pages. Raising them makes answers more consistent and shorter, at the risk of burying a better page chunk |
| Fact-duplicate dropping (DEC-47) | on | code, retrieve.py | Removes page chunks that repeat a fact already returned: smaller tool result |
| Location filter (DEC-49) | default location | code | Only business-wide + that location's chunks searched; fewer wrong-branch answers |
| Hybrid search (DEC-12) | vector + keyword | code | Keyword catches names and numbers vectors miss |
| Embedding model / `EMBED_MODEL`, `EMBED_DIM` | nomic-embed-text-v1.5, 768 | env | Retrieval quality; changing it means re-indexing everything (R6). Query prefix `search_query:` is required by this model |
| Chunk size | 650 tokens, 12.5 % overlap | code: [app/ingest/chunk.py](../app/ingest/chunk.py) | Smaller chunks = shorter tool results (faster) but split answers; needs a re-crawl |
| Facts library, organize/clean step (plan 07) | built | crawler | Short, source-quoted facts are the most speed-friendly and accurate context |
| Custom replies | admin API | data | Owner-written answers for questions that matter; ranked first |

## 6. Sentence splitting (LLM text → TTS)

Affects the **time to first audio** and how natural the speech sounds.

| Knob | Default | Where | Effect / trade-off |
|---|---|---|---|
| Sentence end rule | `. ! ?` + space | code: session.py | TTS starts on the first complete sentence |
| `_ABBREVIATIONS` | a.m., p.m., dr., … | code | Stops "9 a.m." being split into two spoken pieces with a pause (V13) |
| `_MAX_CHUNK` / `_MIN_CHUNK` | 120 / 30 chars | code | A long sentence without punctuation is cut at a clause break after 120 chars. Lower max = earlier first audio but choppier prosody |
| Prompt asks 1–3 sentences | prompt | Fewer sentences = less to synthesize and to listen to |

## 7. Text-to-speech (Google TTS)

| Knob | Default | Where | Effect / trade-off |
|---|---|---|---|
| `TTS_VOICE` | `en-US-Neural2-F` | env | Voice quality, latency and price differ by family (Standard < Neural2 < WaveNet/Studio/Chirp). Neural2 ≈ 250 ms warm per sentence |
| `VOICE_LANGUAGE` | `en-US` | env | Must match the voice |
| Speaking rate / pitch | not set (1.0) | code: [app/voice/tts.py](../app/voice/tts.py) | `speaking_rate` > 1 shortens audio and the caller's wait for long answers; too fast hurts comprehension on phone lines |
| Audio encoding / sample rate | LINEAR16 at the call's rate | code, tts.py | Matches the transport; no resampling |
| TTS warm-up at startup (T4) | on | code, tts.py | First request per process ≈ 700 ms vs ≈ 250 ms warm |
| Greeting audio cache | in-memory per (text, rate, voice, language) | code: session.py | Repeat greetings and fixed phrases play instantly; not persisted across restarts — storing the greeting per business is #74 (V6, DEC-17) |
| SSML / text normalisation | not used | — | Idea: SSML for phone numbers, prices and times read aloud correctly |

## 8. Playback and feel

| Knob | Default | Where | Effect / trade-off |
|---|---|---|---|
| Working sound (T1) | quiet ticks, 0.2 s chunks | code: session.py, [app/voice/audio.py](../app/voice/audio.py) | Fills the silence while a tool runs (perceived speed, not actual); no spoken filler (#40) |
| Barge-in | on | VAD knobs in section 1 | Caller stops the bot; false triggers cut answers short |
| Push-to-talk vs VAD | per client | `/web/` vs phone | PTT removes end-of-speech wait and false triggers, so it is the fastest/cleanest mode |

## 9. Network and environment

| Factor | Effect |
|---|---|
| Twilio over ngrok | Adds round-trip latency to every audio chunk; compare with direct browser (mode C) — #38 |
| Region / Google API latency | STT and TTS are cloud calls even in the local stage; Cloud Run region near the caller in stage 2 |
| Cold start (Cloud Run `min-instances=0`, DEC-35) | First call after idle is slow; warm-up calls (T2, T4) partly hide it |
| Mac load | Other models in memory (the 35B qwen draft model is ~27–29 GB) cause swapping and slow the first token — check `lms ps` before measuring |
| Gemini instead of LM Studio | Usually faster and better than a local 12B; needs the paid key (R12); thinking setting differs per model |

## How to measure

- `LOG_LEVEL=INFO` gives one `llm chat stream ...` line per LLM call: model, prompt size,
  tokens, **time to first token**, total ms (see [app/llm.py](../app/llm.py)); `DEBUG` adds the
  full prompt and reply.
- The voice session emits `latency` events per turn (end of speech → first audio, with tool
  start/end); numbers in [03-voice-channel.md](03-voice-channel.md) V16 and the latency table.
- Compare with the same question warm vs cold, profile-only vs tool-needed, and one change
  at a time. For quality use the eval set in [02-local-rag.md](02-local-rag.md).

## Where to start (largest effect first)

1. **Keep thinking off and the model warm** (`*_REASONING_EFFORT=none`, `VOICE_LLM_WARMUP`) — seconds.
2. **Avoid tool calls** for common questions: richer profile, location card and facts in the prompt (V16: ≈ 5 s with a tool vs ≈ 1.2 s without).
3. **Shrink the tool result**: `SEARCH_TOP_N`, chunk size, fact-first retrieval.
4. **Faster or better model**: smaller/quantized local model, or Gemini Flash in stage 2.
5. **Turn detection**: `VAD_END_SILENCE_MS` (300–700 ms per turn) balanced against cut-offs.
6. **STT model per transport** and **phrase hints** for names — fewer wrong transcripts.
7. **TTS**: voice family, speaking rate, persisted greeting audio.

## Open ideas (not built)

STT phrase hints per business · SSML for numbers/times · history cap or summary for long
calls · `max_tokens`/`temperature` on voice calls · speculative LLM start on interim STT text ·
per-business tuning values (stage 3) · a script that replays a fixed question set and prints
the latency breakdown per setting.
