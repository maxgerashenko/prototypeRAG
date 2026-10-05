"""Settings from env vars (and `.env` locally). Same code in every stage — only values change."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://app:app@localhost:5432/app"

    llm_base_url: str = "http://localhost:1234/v1"
    llm_api_key: str = "lm-studio"
    llm_model: str = "google/gemma-4-12b"  # chat/voice model (DEC-32)
    embed_base_url: str = "http://localhost:1234/v1"
    embed_api_key: str = "lm-studio"
    embed_model: str = "text-embedding-nomic-embed-text-v1.5"
    embed_dim: int = 768

    # voice (plan/03-voice-channel.md) -- Google Speech, auth via ADC, so no key here
    voice_language: str = "en-US"
    stt_model: str = "latest_short"  # "phone_call" is tuned for 8 kHz phone audio
    tts_voice: str = "en-US-Neural2-F"
    # thinking off for voice (DEC-29): gemma-4-12b thinks by default, ~8 s before the first
    # word; "none" -> ~0.3 s. Verified on LM Studio; Gemini's value is checked in step 6.
    voice_reasoning_effort: str = "none"
    # same for /chat (V24): a chat user waits for the first word just like a caller
    chat_reasoning_effort: str = "none"

    # DEC-43: set -> every non-public route needs this key (X-Access-Key header or the
    # cookie from POST /login); empty -> all routes open (local default). Required before
    # the URL is public (stage 2).
    access_key: str = ""

    # Twilio (plan/03-voice-channel.md, modes A/B). Voice SDK browser calls (DEC-34) need the
    # account SID, an API key and a TwiML App whose voice URL is <public_base_url>/twilio/voice.
    twilio_account_sid: str = ""
    twilio_api_key_sid: str = ""
    twilio_api_key_secret: str = ""
    twilio_twiml_app_sid: str = ""
    # set -> /twilio/voice needs a valid X-Twilio-Signature and /voice/ws a stream token
    # signed with it (DEC-43); required before the URL is public
    twilio_auth_token: str = ""
    # the URL Twilio reaches us at (ngrok locally, Cloud Run in stage 2); empty -> taken from
    # the request's Host / X-Forwarded-Proto headers
    public_base_url: str = ""

    dev_reload: bool = False  # live reload of the built web/dist pages (app/api/dev_reload.py); local only


@lru_cache
def get_settings() -> Settings:
    return Settings()
