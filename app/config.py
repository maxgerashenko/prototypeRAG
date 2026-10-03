"""Settings from env vars (and `.env` locally). Same code in every stage — only values change."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://app:app@localhost:5432/app"

    llm_base_url: str = "http://localhost:1234/v1"
    llm_api_key: str = "lm-studio"
    llm_model: str = "qwen3-30b-a3b"
    embed_base_url: str = "http://localhost:1234/v1"
    embed_api_key: str = "lm-studio"
    embed_model: str = "text-embedding-nomic-embed-text-v1.5"
    embed_dim: int = 768


@lru_cache
def get_settings() -> Settings:
    return Settings()
