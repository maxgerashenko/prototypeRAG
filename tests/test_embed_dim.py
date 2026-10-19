from types import SimpleNamespace

from app import llm
from app.config import get_settings


class FakeEmbeddings:
    def __init__(self, dim):
        self.dim, self.kwargs = dim, None

    def create(self, **kwargs):
        self.kwargs = kwargs
        data = [SimpleNamespace(embedding=[0.0] * self.dim) for _ in kwargs["input"]]
        return SimpleNamespace(data=data)


def _client(monkeypatch, dim):
    fake = FakeEmbeddings(dim)
    monkeypatch.setattr(llm, "get_embed_client", lambda: SimpleNamespace(embeddings=fake))
    return fake


def test_embed_requests_embed_dim(monkeypatch):
    dim = get_settings().embed_dim
    fake = _client(monkeypatch, dim)
    assert len(llm.embed(["a", "b"])) == 2
    assert fake.kwargs["dimensions"] == dim


def test_embed_rejects_wrong_dimension(monkeypatch):
    import pytest

    _client(monkeypatch, get_settings().embed_dim + 1)
    with pytest.raises(ValueError, match="EMBED_DIM"):
        llm.embed(["a"])
