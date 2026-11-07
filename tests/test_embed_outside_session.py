"""Embedding must happen before `tenant_session` opens (#276, #227): no pooled DB
connection may be held during the embedding HTTP round trip."""
import uuid
from contextlib import contextmanager

from app.rag import answer as answer_mod
from app.rag import retrieve as retrieve_mod
from app.voice import tools as tools_mod


def _track(monkeypatch, module):
    state = {"open": False, "embedded_while_open": None}

    @contextmanager
    def fake_session(business_id):
        state["open"] = True
        try:
            yield object()
        finally:
            state["open"] = False

    def fake_embed(texts):
        state["embedded_while_open"] = state["open"]
        return [[0.0] * 768 for _ in texts]

    monkeypatch.setattr(module, "tenant_session", fake_session)
    monkeypatch.setattr(retrieve_mod, "embed", fake_embed)
    monkeypatch.setattr(module, "retrieve", lambda *a, **kw: [])
    monkeypatch.setattr(module, "embed_query", retrieve_mod.embed_query)
    monkeypatch.setattr(module, "default_location_id", lambda *a: None)
    return state


def test_voice_search_embeds_before_session(monkeypatch):
    state = _track(monkeypatch, tools_mod)
    tools_mod.search_business_info(uuid.uuid4(), "hours?")
    assert state["embedded_while_open"] is False


def test_chat_prompt_embeds_before_session(monkeypatch):
    state = _track(monkeypatch, answer_mod)
    monkeypatch.setattr(answer_mod, "enabled_tool_names", lambda b: [])
    monkeypatch.setattr(answer_mod, "format_locations", lambda *a, **k: "")
    monkeypatch.setattr(answer_mod, "build_prompt", lambda *a, **k: [])

    class S:
        def get(self, *a):
            return None

    @contextmanager
    def sess(b):
        state["open"] = True
        try:
            yield S()
        finally:
            state["open"] = False

    monkeypatch.setattr(answer_mod, "tenant_session", sess)
    answer_mod.prepare_prompt(uuid.uuid4(), "hours?")
    assert state["embedded_while_open"] is False
