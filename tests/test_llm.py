"""Embedding request shape — no LM Studio or Gemini needed (fake client)."""

from types import SimpleNamespace

import pytest

from app import llm
from app.config import get_settings


class _FakeEmbeddings:
    def __init__(self, dim):
        self.dim = dim
        self.kwargs = None

    def create(self, **kwargs):
        self.kwargs = kwargs
        n = len(kwargs["input"])
        return SimpleNamespace(data=[SimpleNamespace(embedding=[0.1] * self.dim) for _ in range(n)])


def _fake_client(monkeypatch, dim):
    embeddings = _FakeEmbeddings(dim)
    monkeypatch.setattr(llm, "get_embed_client", lambda: SimpleNamespace(embeddings=embeddings))
    return embeddings


def test_embed_requests_embed_dim(monkeypatch):
    # gemini-embedding-001 returns 3072 dims unless the request asks for fewer (DEC-08)
    embeddings = _fake_client(monkeypatch, get_settings().embed_dim)
    vectors = llm.embed(["a", "b"])
    assert embeddings.kwargs["dimensions"] == get_settings().embed_dim
    assert len(vectors) == 2


def test_embed_fails_fast_on_dim_mismatch(monkeypatch):
    _fake_client(monkeypatch, 3072)
    with pytest.raises(ValueError, match="3072-dim"):
        llm.embed(["a"])
