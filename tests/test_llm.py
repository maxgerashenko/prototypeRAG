"""LLM call logging (app/llm.py `instrument`): fake clients, no network or DB."""

import asyncio
import gc
import logging
from types import SimpleNamespace as NS

import pytest
from openai import AsyncOpenAI, OpenAI

from app.llm import instrument

USAGE = NS(prompt_tokens=120, completion_tokens=7)


def _chunk(content=None, tool_index=None, finish=None, usage=None, choices=True):
    tool_calls = [NS(index=tool_index)] if tool_index is not None else None
    delta = NS(content=content, tool_calls=tool_calls)
    return NS(choices=[NS(delta=delta, finish_reason=finish)] if choices else [], usage=usage)


STREAM = [_chunk("We open "), _chunk("at 9.", finish="stop"), _chunk(choices=False, usage=USAGE)]


def _client(cls, chat_create, embed_create=None):
    client = cls(api_key="x", base_url="http://127.0.0.1:9")
    client.chat.completions.create = chat_create
    client.embeddings.create = embed_create or (lambda **kw: None)
    return instrument(client)


def _lines(caplog):
    return [r.getMessage() for r in caplog.records if r.name == "app.llm" and r.levelno == logging.INFO]


def test_sync_chat_logs_tokens_and_sizes(caplog):
    caplog.set_level(logging.INFO, logger="app.llm")
    response = NS(choices=[NS(message=NS(content="Hi", tool_calls=None), finish_reason="stop")], usage=USAGE)
    client = _client(OpenAI, lambda **kw: response)
    got = client.chat.completions.create(model="m", messages=[{"role": "user", "content": "hello"}])
    assert got is response
    [line] = _lines(caplog)
    assert line.startswith("llm chat model=m messages=1 prompt_chars=5 ")
    assert "prompt_tokens=120 completion_tokens=7 out_chars=2 finish=stop" in line
    assert "total_ms=" in line and line.endswith("status=ok")


def test_sync_stream_passes_chunks_and_drops_usage_chunk(caplog):
    caplog.set_level(logging.INFO, logger="app.llm")
    sent = {}

    def create(**kw):
        sent.update(kw)
        return iter(STREAM)

    client = _client(OpenAI, create)
    stream = client.chat.completions.create(model="m", messages=[], stream=True)
    pieces = [c.choices[0].delta.content for c in stream]
    assert pieces == ["We open ", "at 9."]  # usage-only chunk isn't passed on
    assert sent["stream_options"] == {"include_usage": True}
    [line] = _lines(caplog)
    assert line.startswith("llm chat stream ")
    assert "ttft_ms=" in line and "prompt_tokens=120 completion_tokens=7 out_chars=13" in line
    assert line.endswith("status=ok")


def test_caller_stream_options_are_kept():
    sent = {}
    client = _client(OpenAI, lambda **kw: sent.update(kw) or iter([]))
    list(client.chat.completions.create(model="m", messages=[], stream=True, stream_options={"include_usage": False}))
    assert sent["stream_options"] == {"include_usage": False}


def test_async_stream_with_tool_call(caplog):
    caplog.set_level(logging.INFO, logger="app.llm")

    async def gen():
        for c in [_chunk(tool_index=0), _chunk(tool_index=0, finish="tool_calls"), _chunk(choices=False, usage=USAGE)]:
            yield c

    async def create(**kw):
        return gen()

    client = _client(AsyncOpenAI, create)

    async def run():
        stream = await client.chat.completions.create(model="m", messages=[], tools=[{}], stream=True)
        return [c async for c in stream]

    assert len(asyncio.run(run())) == 2
    [line] = _lines(caplog)
    assert "tools=1" in line and "tool_calls=1 finish=tool_calls" in line and line.endswith("status=ok")


def test_stream_abandoned_mid_answer_logs_stopped(caplog):
    caplog.set_level(logging.INFO, logger="app.llm")
    client = _client(OpenAI, lambda **kw: iter(STREAM))
    stream = client.chat.completions.create(model="m", messages=[], stream=True)
    next(stream)  # caller barges in after the first piece
    del stream
    gc.collect()
    [line] = _lines(caplog)
    assert "out_chars=8" in line and line.endswith("status=stopped")


def test_error_is_logged_and_raised(caplog):
    caplog.set_level(logging.INFO, logger="app.llm")

    def create(**kw):
        raise ConnectionError("LM Studio down")

    client = _client(OpenAI, create)
    with pytest.raises(ConnectionError):
        client.chat.completions.create(model="m", messages=[])
    [line] = _lines(caplog)
    assert line.endswith("status=error:ConnectionError")


def test_embed_logged(caplog):
    caplog.set_level(logging.INFO, logger="app.llm")
    client = _client(OpenAI, lambda **kw: None, lambda **kw: NS(data=[], usage=NS(prompt_tokens=9)))
    client.embeddings.create(model="e", input=["ab", "cde"])
    [line] = _lines(caplog)
    assert line.startswith("llm embed model=e inputs=2 input_chars=5 prompt_tokens=9 ")


def test_debug_logs_full_prompt_and_response(caplog):
    caplog.set_level(logging.DEBUG, logger="app.llm")
    client = _client(OpenAI, lambda **kw: iter(STREAM))
    list(client.chat.completions.create(model="m", messages=[{"role": "user", "content": "hours?"}], stream=True))
    debug = [r.getMessage() for r in caplog.records if r.levelno == logging.DEBUG]
    assert debug == ['llm prompt: [{"role": "user", "content": "hours?"}]', "llm response: We open at 9."]
