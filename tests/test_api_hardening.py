"""API review fixes (#226, #230, #231, #233, #241, #242, #243). No database or network needed:
the DB/LLM edges are replaced with fakes."""

import asyncio
import time
import uuid

import httpx
import openai
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import chat, deps
from app.config import Settings
from app.voice import browser_ws, twilio_routes, ws as stream_ws


class _Settings(Settings):
    """Settings that ignore .env, so the tests don't depend on the developer's machine."""

    model_config = {**Settings.model_config, "env_file": None}


def _settings(**kw) -> Settings:
    return _Settings(**kw)


# --- #242: SSE framing ----------------------------------------------------------------

def test_sse_splits_on_every_line_terminator():
    assert chat._sse("a\nb") == "data: a\ndata: b\n\n"
    assert chat._sse("a\rb") == "data: a\ndata: b\n\n"  # a lone CR used to cut the text off
    assert chat._sse("a\r\nb") == "data: a\ndata: b\n\n"
    assert chat._sse("x", event="error") == "event: error\ndata: x\n\n"


# --- #233: require_business -----------------------------------------------------------

class _FakeSession:
    def __init__(self, found):
        self.found = found

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def scalar(self, stmt):
        return self.found


def test_require_business_404_for_unknown_id(monkeypatch):
    monkeypatch.setattr(deps, "SessionLocal", lambda: _FakeSession(None))
    with pytest.raises(deps.HTTPException) as e:
        deps.require_business(uuid.uuid4())
    assert e.value.status_code == 404
    bid = uuid.uuid4()
    monkeypatch.setattr(deps, "SessionLocal", lambda: _FakeSession(bid))
    assert deps.require_business(bid) == bid


def test_upstream_outage_becomes_503():
    request = httpx.Request("POST", "http://lmstudio/v1/chat")
    with pytest.raises(deps.HTTPException) as e:
        with deps.upstream_unavailable("embedding server"):
            raise openai.APIConnectionError(request=request)
    assert e.value.status_code == 503 and "embedding server" in e.value.detail
    with pytest.raises(deps.HTTPException):
        with deps.upstream_unavailable("x"):
            raise httpx.ConnectError("down")
    with pytest.raises(KeyError):  # a real bug is not hidden as 503
        with deps.upstream_unavailable("x"):
            raise KeyError("bug")


def test_chat_unknown_business_is_404_and_outage_is_503(monkeypatch):
    app = FastAPI()
    app.include_router(chat.router)
    client = TestClient(app)
    monkeypatch.setattr(chat, "require_business", lambda b: (_ for _ in ()).throw(
        deps.HTTPException(404, "business not found")))
    r = client.post("/chat", json={"business_id": str(uuid.uuid4()), "question": "hi"})
    assert r.status_code == 404

    # /chat/stream: the dependency was bound at import time, so override it instead
    app.dependency_overrides[deps.require_business] = lambda: (_ for _ in ()).throw(
        deps.HTTPException(404, "business not found"))
    assert client.get("/chat/stream", params={"business_id": str(uuid.uuid4()), "question": "hi"}).status_code == 404
    app.dependency_overrides.clear()

    monkeypatch.setattr(chat, "require_business", lambda b: b)
    monkeypatch.setattr(chat, "_prepare_conversation_and_history", lambda b, c: (uuid.uuid4(), []))

    def down(*a, **kw):
        raise openai.APIConnectionError(request=httpx.Request("POST", "http://x"))

    monkeypatch.setattr(chat, "answer_question", down)
    r = client.post("/chat", json={"business_id": str(uuid.uuid4()), "question": "hi"})
    assert r.status_code == 503


# --- #242: input limits ---------------------------------------------------------------

def test_chat_rejects_empty_and_oversized_questions(monkeypatch):
    app = FastAPI()
    app.include_router(chat.router)
    app.dependency_overrides[deps.require_business] = lambda: None
    client = TestClient(app)
    bid = str(uuid.uuid4())
    assert client.post("/chat", json={"business_id": bid, "question": ""}).status_code == 422
    big = "x" * (chat.MAX_QUESTION_LEN + 1)
    assert client.post("/chat", json={"business_id": bid, "question": big}).status_code == 422
    assert client.get("/chat/stream", params={"business_id": bid, "question": big}).status_code == 422


# --- #231: stream errors --------------------------------------------------------------

def test_stream_failure_sends_error_event_then_done_and_saves_nothing(monkeypatch):
    app = FastAPI()
    app.include_router(chat.router)
    app.dependency_overrides[deps.require_business] = lambda: None
    cid = uuid.uuid4()
    monkeypatch.setattr(chat, "_prepare_conversation_and_history", lambda b, c: (cid, []))

    def broken(*a, **kw):
        raise openai.APIConnectionError(request=httpx.Request("POST", "http://x"))
        yield  # pragma: no cover

    saved = []
    monkeypatch.setattr(chat, "stream_answer", broken)
    monkeypatch.setattr(chat, "_save_turn", lambda *a: saved.append(a))
    body = TestClient(app).get("/chat/stream", params={"business_id": str(uuid.uuid4()), "question": "hi"}).text
    assert body.startswith(f"event: conversation_id\ndata: {cid}\n\n")
    assert f"event: error\ndata: {chat.ERROR_TEXT}\n\n" in body
    assert body.endswith("data: [DONE]\n\n")
    assert saved == []


# --- #230: debug exposure -------------------------------------------------------------

def test_debug_routes_are_only_on_the_debug_router():
    paths = {r.path for r in chat.router.routes}
    assert "/debug/retrieve" not in paths and "/debug/prompt" not in paths
    assert {"/debug/retrieve", "/debug/prompt"} <= {r.path for r in chat.debug_router.routes}


def test_main_mounts_debug_only_when_enabled():
    from app.main import app

    from app.config import get_settings

    assert ("/debug/prompt" in app.openapi()["paths"]) == get_settings().debug_endpoints


def test_debug_top_n_is_capped():
    app = FastAPI()
    app.include_router(chat.debug_router)
    app.dependency_overrides[deps.require_business] = lambda: None
    client = TestClient(app)
    r = client.get("/debug/retrieve", params={"business_id": str(uuid.uuid4()), "q": "x", "top_n": 10_000})
    assert r.status_code == 422
    assert client.get("/debug/prompt", params={"business_id": str(uuid.uuid4()), "q": "x", "top_n": 0}).status_code == 422


def test_chat_prompt_only_with_debug_flag(monkeypatch):
    class Result:
        answer, chunks, messages = "ok", [], [{"role": "system", "content": "SECRET"}]

    app = FastAPI()
    app.include_router(chat.router)
    monkeypatch.setattr(chat, "require_business", lambda b: b)
    monkeypatch.setattr(chat, "_prepare_conversation_and_history", lambda b, c: (uuid.uuid4(), []))
    monkeypatch.setattr(chat, "answer_question", lambda *a, **kw: Result)
    monkeypatch.setattr(chat, "_save_turn", lambda *a: None)
    client = TestClient(app)
    body = {"business_id": str(uuid.uuid4()), "question": "hi"}

    monkeypatch.setattr(chat, "get_settings", lambda: _settings(debug_endpoints=False))
    r = client.post("/chat", json=body)
    assert r.json()["prompt"] is None and "SECRET" not in r.text
    monkeypatch.setattr(chat, "get_settings", lambda: _settings(debug_endpoints=True))
    assert client.post("/chat", json=body).json()["prompt"] == Result.messages


# --- #243: stream token expiry, start timeout, Origin ---------------------------------

def _twilio_on(monkeypatch, **kw):
    s = _settings(twilio_validate_signature=True, twilio_auth_token="secret", **kw)
    monkeypatch.setattr(twilio_routes, "get_settings", lambda: s)
    return s


def test_stream_token_expires(monkeypatch):
    _twilio_on(monkeypatch)
    bid = uuid.uuid4()
    now = time.time()
    token = twilio_routes.stream_token(bid, "CA1", now=now)
    assert twilio_routes.check_stream_token(bid, "CA1", token, now=now + 5)
    assert not twilio_routes.check_stream_token(bid, "CA1", token, now=now + twilio_routes.STREAM_TOKEN_TTL_S + 1)
    assert not twilio_routes.check_stream_token(bid, "CA2", token, now=now)  # other call
    # a forged timestamp does not help: the MAC covers it
    _, mac = token.split(".")
    assert not twilio_routes.check_stream_token(bid, "CA1", f"{int(now) + 1}.{mac}", now=now)
    assert not twilio_routes.check_stream_token(bid, "CA1", "", now=now)
    assert not twilio_routes.check_stream_token(bid, "CA1", "garbage", now=now)


def test_media_stream_without_start_is_closed(monkeypatch):
    monkeypatch.setattr(stream_ws, "START_TIMEOUT_S", 0.2)
    app = FastAPI()
    app.include_router(stream_ws.router)
    with TestClient(app).websocket_connect("/voice/ws") as sock:
        msg = sock.receive()
    assert msg["type"] == "websocket.close" and msg["code"] == 4408


def test_browser_origin_check(monkeypatch):
    monkeypatch.setattr(browser_ws, "get_settings", lambda: _settings(ws_allowed_origins="http://localhost:5173/"))
    ok = browser_ws.origin_allowed
    assert ok(None, "localhost:8000")  # not a browser
    assert ok("http://localhost:8000", "localhost:8000")  # our own page
    assert ok("http://localhost:5173", "localhost:8000")  # listed (Vite dev server)
    assert not ok("https://evil.example", "localhost:8000")


def test_browser_ws_refuses_foreign_origin():
    app = FastAPI()
    app.include_router(browser_ws.router)
    client = TestClient(app)
    with pytest.raises(Exception):  # closed before accept -> the handshake fails
        with client.websocket_connect(f"/voice/browser?business_id={uuid.uuid4()}",
                                      headers={"origin": "https://evil.example"}):
            pass


# --- #241: token client check ---------------------------------------------------------

class _Req:
    def __init__(self, host, headers=None):
        self.client = type("C", (), {"host": host})()
        self.headers = headers or {}


def test_token_client_allowed(monkeypatch):
    check = twilio_routes._token_client_allowed
    monkeypatch.setattr(twilio_routes, "get_settings", lambda: _settings())
    assert check(_Req("127.0.0.1")) and check(_Req("::1"))
    assert not check(_Req("192.168.65.1"))  # Docker gateway, not configured
    assert not check(_Req("127.0.0.1", {"x-forwarded-for": "1.2.3.4"}))
    monkeypatch.setattr(twilio_routes, "get_settings", lambda: _settings(twilio_token_allowed_cidrs="192.168.65.0/24, 172.16.0.0/12"))
    assert check(_Req("192.168.65.1")) and check(_Req("172.17.0.1"))
    assert not check(_Req("8.8.8.8"))
    assert not check(_Req("192.168.65.1", {"x-forwarded-for": "1.2.3.4"}))  # still refused behind a proxy


# --- #226: slow DB lookups don't block the event loop ---------------------------------

def test_twilio_voice_lookup_does_not_block_the_loop(monkeypatch):
    monkeypatch.setattr(twilio_routes, "get_settings", lambda: _settings(twilio_validate_signature=False))
    monkeypatch.setattr(twilio_routes, "find_business", lambda form: time.sleep(0.5))  # a cold DB

    app = FastAPI()
    app.include_router(twilio_routes.router)

    async def main():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
            slow = asyncio.create_task(c.post("/twilio/voice", data={"CallSid": "CA1", "To": "+1"}))
            await asyncio.sleep(0.05)
            started = time.perf_counter()
            await c.get("/twilio/number")  # a request on the same loop meanwhile
            quick = time.perf_counter() - started
            await slow
            return quick

    assert asyncio.run(main()) < 0.3
