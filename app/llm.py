"""OpenAI-compatible chat + embed client (DEC-05). LM Studio locally, Gemini in cloud —
switching providers is changing `LLM_*` / `EMBED_*` env vars, nothing else (DEC-20: no
LangChain/LlamaIndex, no provider classes).
"""

import json

from functools import lru_cache

from openai import OpenAI

from app.config import get_settings


@lru_cache(maxsize=1)
def get_chat_client() -> OpenAI:
    settings = get_settings()
    return OpenAI(base_url=settings.llm_base_url, api_key=settings.llm_api_key)


@lru_cache(maxsize=1)
def get_embed_client() -> OpenAI:
    settings = get_settings()
    return OpenAI(base_url=settings.embed_base_url, api_key=settings.embed_api_key)


def embed(texts: list[str]) -> list[list[float]]:
    client = get_embed_client()
    response = client.embeddings.create(model=get_settings().embed_model, input=texts)
    return [d.embedding for d in response.data]


def chat(messages: list[dict], **kwargs) -> str:
    client = get_chat_client()
    response = client.chat.completions.create(model=get_settings().llm_model, messages=messages, **kwargs)
    return response.choices[0].message.content


def chat_json(messages: list[dict], schema: dict, schema_name: str = "result") -> dict:
    """Structured extraction (e.g. business profile, Part 1) via `response_format`."""
    client = get_chat_client()
    response = client.chat.completions.create(
        model=get_settings().llm_model,
        messages=messages,
        response_format={
            "type": "json_schema",
            "json_schema": {"name": schema_name, "schema": schema, "strict": True},
        },
    )
    message = response.choices[0].message
    # Some thinking-enabled models (e.g. qwen3.6-35b-a3b) emit the schema-conforming
    # JSON into reasoning_content and leave content empty when combined with
    # response_format=json_schema -- fall back to it rather than fail the whole call.
    content = message.content or getattr(message, "reasoning_content", None) or ""
    return json.loads(content)
