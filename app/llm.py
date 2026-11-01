"""OpenAI-compatible chat + embed client (DEC-05). LM Studio locally, Gemini in cloud —
switching providers is changing `LLM_*` / `EMBED_*` env vars, nothing else (DEC-20: no
LangChain/LlamaIndex, no provider classes).
"""

import asyncio
import json
import logging
import time
from functools import lru_cache, wraps

from openai import AsyncOpenAI, OpenAI

from app.config import get_settings

log = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def get_chat_client() -> OpenAI:
    settings = get_settings()
    return instrument(OpenAI(base_url=settings.llm_base_url, api_key=settings.llm_api_key))


@lru_cache(maxsize=1)
def get_async_chat_client() -> AsyncOpenAI:
    """For the voice call loop, which runs on the event loop: an async stream can be
    cancelled mid-answer when the caller barges in; a sync one in a thread can't."""
    settings = get_settings()
    return instrument(AsyncOpenAI(base_url=settings.llm_base_url, api_key=settings.llm_api_key))


@lru_cache(maxsize=1)
def get_embed_client() -> OpenAI:
    settings = get_settings()
    return instrument(OpenAI(base_url=settings.embed_base_url, api_key=settings.embed_api_key))


def embed(texts: list[str]) -> list[list[float]]:
    settings = get_settings()
    client = get_embed_client()
    # ask for EMBED_DIM explicitly: Gemini's default is larger than the column (768) and
    # its OpenAI-compatible endpoint honours `dimensions`; LM Studio ignores it (nomic is 768)
    response = client.embeddings.create(model=settings.embed_model, input=texts, dimensions=settings.embed_dim)
    vectors = [d.embedding for d in response.data]
    # the chunks.embedding column is fixed at EMBED_DIM; fail here with a clear message
    # rather than deep inside pgvector (e.g. a cloud model's default size isn't 768)
    if vectors and len(vectors[0]) != settings.embed_dim:
        raise ValueError(
            f"{settings.embed_model} returned {len(vectors[0])}-dim vectors, EMBED_DIM is {settings.embed_dim}"
        )
    return vectors


def chat(messages: list[dict], **kwargs) -> str:
    client = get_chat_client()
    response = client.chat.completions.create(model=get_settings().llm_model, messages=messages, **kwargs)
    # content can be None (e.g. a thinking model that spent its output on reasoning) --
    # callers store it in NOT NULL columns, so normalize to ""
    return response.choices[0].message.content or ""


def chat_json(
    messages: list[dict], schema: dict, schema_name: str = "result", model: str | None = None,
    max_tokens: int | None = None, timeout: float | None = None,
) -> dict:
    """Structured extraction (e.g. business profile, Part 1) via `response_format`.
    `model` overrides `LLM_MODEL` for one call (e.g. `FACTS_LLM_MODEL`, OPEN-20).
    `max_tokens` / `timeout` bound a runaway answer (a model looping inside the JSON);
    with a `timeout` the call is not retried -- the caller decides what a failure means."""
    client = get_chat_client()
    if timeout is not None:
        # with_options copies the client, so the copy needs the call logging again
        client = instrument(client.with_options(timeout=timeout, max_retries=0))
    extra = {"max_tokens": max_tokens} if max_tokens is not None else {}
    response = client.chat.completions.create(
        model=model or get_settings().llm_model,
        messages=messages,
        response_format={
            "type": "json_schema",
            "json_schema": {"name": schema_name, "schema": schema, "strict": True},
        },
        **extra,
    )
    message = response.choices[0].message
    # Some thinking-enabled models (e.g. qwen3.6-35b-a3b) emit the schema-conforming
    # JSON into reasoning_content and leave content empty when combined with
    # response_format=json_schema -- fall back to it rather than fail the whole call.
    content = message.content or getattr(message, "reasoning_content", None) or ""
    return json.loads(content)


# --- call logging (plan/02-local-rag.md: log every LLM call) -------------------------
# Every chat and embedding call made through the clients above logs one INFO line on
# logger `app.llm`: model, prompt size, tokens, time to first token (streams) and total
# time -- what's needed to explain DEC-32's 10-17 s turns. With LOG_LEVEL=DEBUG the full
# prompt and response are logged too. Done by wrapping `create` on the client, so the
# call sites (rag/answer.py, voice/session.py, ...) don't change.


def instrument(client):
    """Wrap `chat.completions.create` and `embeddings.create` of an (Async)OpenAI client
    so each call is logged. The SDK caches those resource objects on the client, so the
    wrapped `create` is what every later `client.chat.completions.create(...)` gets."""
    is_async = isinstance(client, AsyncOpenAI)
    completions, embeddings = client.chat.completions, client.embeddings
    completions.create = _wrap(completions.create, is_async, _chat_call)
    embeddings.create = _wrap(embeddings.create, is_async, _embed_call)
    return client


def _wrap(create, is_async: bool, make_call):
    if is_async:
        @wraps(create)
        async def logged(**kwargs):
            call = make_call(kwargs)
            try:
                result = await create(**call.kwargs)
            except BaseException as e:
                call.finish(status=f"error:{type(e).__name__}")
                raise
            return call.result(result)
    else:
        @wraps(create)
        def logged(**kwargs):
            call = make_call(kwargs)
            try:
                result = create(**call.kwargs)
            except BaseException as e:
                call.finish(status=f"error:{type(e).__name__}")
                raise
            return call.result(result)
    return logged


class _Call:
    """Timing and sizes of one request; logs one line when the call ends."""

    def __init__(self, kind: str, kwargs: dict, fields: dict):
        self.kwargs = kwargs
        self.kind = kind
        self.fields = {"model": kwargs.get("model"), **fields}
        self.start = time.monotonic()
        self.done = False

    def ms(self) -> int:
        return round((time.monotonic() - self.start) * 1000)

    def result(self, result):
        try:
            fields = self.summarize(result)
        except Exception:  # an odd response shape must never fail a call that worked
            log.warning("llm %s: couldn't read the response for the log", self.kind, exc_info=True)
            fields = {}
        self.finish(**fields)
        return result

    def summarize(self, result) -> dict:
        return {}

    def finish(self, **fields) -> None:
        if self.done:
            return
        self.done = True
        status = fields.pop("status", "ok")
        fields = {**self.fields, **fields, "total_ms": self.ms(), "status": status}
        log.info("llm %s %s", self.kind, " ".join(f"{k}={v}" for k, v in fields.items() if v is not None))


def _usage(usage) -> dict:
    return {
        "prompt_tokens": getattr(usage, "prompt_tokens", None),
        "completion_tokens": getattr(usage, "completion_tokens", None),
    }


def _chat_call(kwargs: dict) -> _Call:
    messages = kwargs.get("messages") or []
    stream = bool(kwargs.get("stream"))
    if stream and "stream_options" not in kwargs:
        # token counts arrive in one extra final chunk with no choices; _LoggedStream
        # records it and drops it, so callers see the same chunks as before
        kwargs = {**kwargs, "stream_options": {"include_usage": True}}
    fields = {
        "messages": len(messages),
        "prompt_chars": sum(len(str(m.get("content") or "")) for m in messages),
        "tools": len(kwargs["tools"]) if kwargs.get("tools") else None,
        "max_tokens": kwargs.get("max_tokens"),
    }
    if log.isEnabledFor(logging.DEBUG):
        log.debug("llm prompt: %s", json.dumps(messages, ensure_ascii=False, default=str))
    return _ChatCall("chat stream" if stream else "chat", kwargs, fields)


class _ChatCall(_Call):
    def result(self, result):
        if self.kwargs.get("stream"):
            return _LoggedStream(result, self)
        return super().result(result)

    def summarize(self, response) -> dict:
        message = response.choices[0].message if response.choices else None
        content = (message and message.content) or ""
        reasoning = (message and getattr(message, "reasoning_content", None)) or ""
        tool_calls = (message and message.tool_calls) or []
        log.debug("llm response: %s", content)
        return {
            **_usage(response.usage),
            "out_chars": len(content),
            "reasoning_chars": len(reasoning) or None,
            "tool_calls": len(tool_calls) or None,
            "finish": response.choices[0].finish_reason if response.choices else None,
        }


def _embed_call(kwargs: dict) -> _Call:
    texts = kwargs.get("input")
    texts = [texts] if isinstance(texts, str) else list(texts or [])
    return _EmbedCall("embed", kwargs, {"inputs": len(texts), "input_chars": sum(len(str(t)) for t in texts)})


class _EmbedCall(_Call):
    def summarize(self, response) -> dict:
        return {"prompt_tokens": getattr(response.usage, "prompt_tokens", None)}


class _LoggedStream:
    """Passes a chat stream (sync or async) through unchanged, except the usage-only
    final chunk, and logs when it ends: time to first token (content or tool call --
    what the caller waits for), tokens, output size. If the caller stops reading early
    (barge-in, hang-up, an exception in its loop) the line is logged with status=stopped
    when the stream object is dropped."""

    def __init__(self, stream, call: _ChatCall):
        self._stream = stream
        self._call = call
        self._first_token_ms = None
        self._out_chars = 0
        self._reasoning_chars = 0
        self._tool_calls: set[int] = set()
        self._usage = {}
        self._finish = None
        self._parts: list[str] = []

    def _see(self, chunk) -> bool:
        """Record one chunk; False if it's the usage-only chunk callers don't get."""
        if getattr(chunk, "usage", None) is not None:
            self._usage = _usage(chunk.usage)
        if not chunk.choices:
            return False
        choice = chunk.choices[0]
        delta = choice.delta
        if delta is None:
            return True
        if delta.content or delta.tool_calls:
            if self._first_token_ms is None:
                self._first_token_ms = self._call.ms()
        if delta.content:
            self._out_chars += len(delta.content)
            self._parts.append(delta.content)
        self._reasoning_chars += len(getattr(delta, "reasoning_content", None) or "")
        for tc in delta.tool_calls or []:
            self._tool_calls.add(tc.index)
        if choice.finish_reason:
            self._finish = choice.finish_reason
        return True

    def _end(self, status: str = "ok") -> None:
        if not self._call.done:
            log.debug("llm response: %s", "".join(self._parts))
        self._call.finish(
            ttft_ms=self._first_token_ms,
            **self._usage,
            out_chars=self._out_chars,
            reasoning_chars=self._reasoning_chars or None,
            tool_calls=len(self._tool_calls) or None,
            finish=self._finish,
            status=status,
        )

    # sync
    def __iter__(self):
        return self

    def __next__(self):
        while True:
            try:
                chunk = next(self._stream)
            except StopIteration:
                self._end()
                raise
            except BaseException as e:
                self._end(f"error:{type(e).__name__}")
                raise
            if self._see(chunk):
                return chunk

    # async
    def __aiter__(self):
        return self

    async def __anext__(self):
        while True:
            try:
                chunk = await self._stream.__anext__()
            except StopAsyncIteration:
                self._end()
                raise
            except BaseException as e:  # incl. CancelledError on barge-in mid-read
                self._end("cancelled" if isinstance(e, asyncio.CancelledError) else f"error:{type(e).__name__}")
                raise
            if self._see(chunk):
                return chunk

    def __del__(self):
        if not self._call.done:
            self._end("stopped")

    def __getattr__(self, name):  # close(), response, ... (callers only iterate today)
        return getattr(self._stream, name)
