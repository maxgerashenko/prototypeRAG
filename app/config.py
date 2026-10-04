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


@lru_cache
def get_settings() -> Settings:
    return Settings()
