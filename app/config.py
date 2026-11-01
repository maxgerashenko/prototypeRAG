"""Settings from env vars (and `.env` locally). Same code in every stage — only values change."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://app:app@localhost:5432/app"

    llm_base_url: str = "http://localhost:1234/v1"
    llm_api_key: str = "lm-studio"
    llm_model: str = "google/gemma-4-12b"  # chat/voice model (DEC-32)
    # fact extraction (plan/07 §5.3, OPEN-20) is an offline job, so it may use a stronger
    # model than the chat one; empty = LLM_MODEL
    facts_llm_model: str = ""
    embed_base_url: str = "http://localhost:1234/v1"
    embed_api_key: str = "lm-studio"
    embed_model: str = "text-embedding-nomic-embed-text-v1.5"
    embed_dim: int = 768

    # voice (plan/03-voice-channel.md) -- Google Speech, auth via ADC, so no key here
    voice_language: str = "en-US"
    stt_model: str = "latest_short"  # "phone_call" is tuned for 8 kHz phone audio
    # V8: extra languages Google may detect besides VOICE_LANGUAGE, comma-separated
    # (e.g. "es-US,ru-RU"; Google allows at most 3). Empty = VOICE_LANGUAGE only.
    stt_alternative_languages: str = ""
    tts_voice: str = "en-US-Neural2-F"
    # thinking off for voice (DEC-29): gemma-4-12b thinks by default, ~8 s before the first
    # word; "none" -> ~0.3 s. Verified on LM Studio; Gemini's value is checked in step 6.
    voice_reasoning_effort: str = "none"
    # same for /chat (V24): a chat user waits for the first word just like a caller
    chat_reasoning_effort: str = "none"
    # T2: prime the LLM's prompt cache while the greeting plays. Helps LM Studio; whether
    # Gemini needs or allows it (it costs one extra request per call) is checked in stage 2.
    voice_llm_warmup: bool = True
    # V3/mid-thought pauses: how long a silence must be before a turn counts as finished.
    # 700 ms (plan budget was 300-500 ms) trades a little latency on every turn for fewer
    # turns cut by a pause mid-sentence (plan/03-voice-channel.md "Known problems" V3).
    vad_end_silence_ms: int = 700
    # V9/V10: while the bot is speaking, speech_start needs this stricter aggressiveness
    # and minimum speech length before it fires, so echo/noise doesn't look like a real
    # barge-in as easily; reverts to the normal VAD settings once speech is confirmed.
    vad_barge_in_aggressiveness: int = 3
    vad_barge_in_start_ms: int = 300

    # level of the `app.*` loggers: INFO logs one line per LLM call (app/llm.py) and the
    # voice turn timings; DEBUG adds the full LLM prompt and response
    log_level: str = "INFO"

    # Twilio (modes A/B, plan/03-voice-channel.md). No phone number needed for mode B (DEC-34):
    # the Voice SDK page calls the TwiML App, whose voice URL is <public_base_url>/twilio/voice.
    twilio_account_sid: str = ""
    twilio_auth_token: str = ""  # checks X-Twilio-Signature and signs the media stream token
    twilio_api_key_sid: str = ""  # API key + secret sign Voice SDK access tokens (mode B)
    twilio_api_key_secret: str = ""
    twilio_twiml_app_sid: str = ""
    # The main Twilio number (E.164, e.g. +15550001234). The voice app's business list
    # assigns it to one business at a time (POST /businesses/{id}/main-number).
    twilio_phone_number: str = ""
    # Public URL Twilio reaches us at (the ngrok URL locally, the Cloud Run URL in stage 2).
    # Webhook signatures cover this URL, and the media stream URL is built from it. Empty:
    # derived from the request (Host + X-Forwarded-Proto).
    public_base_url: str = ""
    # Never turn off when the API is reachable from the internet (ngrok counts); only for
    # replaying requests by hand (curl) without a signature.
    twilio_validate_signature: bool = True

    # Booking backend (plan/04-actions.md, DEC-48): "internal" (Postgres only) or "google_calendar".
    # Pilot auth is a service account key; the owner shares the calendar with its email.
    # `businesses.settings.google_calendar_id` overrides the id per business.
    booking_backend: str = "internal"
    google_calendar_id: str = ""
    google_calendar_credentials_file: str = ""
    # Where the app is reached; calendar events link back to the conversation with it.
    app_base_url: str = "http://localhost:8000"
    # Calendar sync (#165): how often Google's events are mirrored into `calendar_events`.
    calendar_sync_interval_seconds: int = 60

    dev_reload: bool = False  # live reload of the built web/dist pages (app/api/dev_reload.py); local only


@lru_cache
def get_settings() -> Settings:
    return Settings()
