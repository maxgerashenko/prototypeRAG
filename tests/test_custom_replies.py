"""Custom replies API (app/api/custom_replies.py): CRUD plus the one chunk each reply owns.
Real: Postgres. Faked: the embedding server (a fixed vector). Needs Postgres."""

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, func, select

from app.api import custom_replies
from app.db import tenant_session
from app.db.models import Business, Chunk
from app.main import app


@pytest.fixture
def client(monkeypatch):
    seen: list[list[str]] = []

    def fake_embed(texts):
        seen.append(texts)
        return [[0.5] * 768 for _ in texts]

    monkeypatch.setattr(custom_replies, "embed", fake_embed)
    c = TestClient(app)
    c.embedded = seen
    return c


@pytest.fixture
def biz():
    bid = uuid.uuid4()
    with tenant_session(bid) as s:
        s.add(Business(id=bid, name="Reply Biz"))
    yield bid
    with tenant_session(bid) as s:
        s.execute(delete(Business).where(Business.id == bid))


def _chunks(bid):
    with tenant_session(bid) as s:
        return list(s.scalars(select(Chunk).where(Chunk.business_id == bid, Chunk.kind == "custom_reply")))


def _url(bid, rid=""):
    return f"/businesses/{bid}/custom-replies" + (f"/{rid}" if rid else "")


def test_create_returns_the_reply_and_indexes_exactly_one_chunk(client, biz):
    r = client.post(_url(biz), json={"question": "Parking?", "answer": "Free behind the shop."})
    assert r.status_code == 200
    body = r.json()
    assert body["question"] == "Parking?" and body["answer"] == "Free behind the shop."
    [chunk] = _chunks(biz)
    assert chunk.text == "Q: Parking?\nA: Free behind the shop."
    assert str(chunk.custom_reply_id) == body["id"] and chunk.page_id is None and chunk.chunk_index == 0
    assert client.embedded == [["search_document: Q: Parking?\nA: Free behind the shop."]]


def test_list_is_scoped_to_the_business_and_oldest_first(client, biz):
    other = uuid.uuid4()
    with tenant_session(other) as s:
        s.add(Business(id=other, name="Other"))
    try:
        client.post(_url(biz), json={"question": "one", "answer": "1"})
        client.post(_url(biz), json={"question": "two", "answer": "2"})
        client.post(_url(other), json={"question": "foreign", "answer": "x"})
        assert [x["question"] for x in client.get(_url(biz)).json()] == ["one", "two"]
    finally:
        with tenant_session(other) as s:
            s.execute(delete(Business).where(Business.id == other))


def test_update_replaces_the_chunk_instead_of_adding_one(client, biz):
    rid = client.post(_url(biz), json={"question": "Hours?", "answer": "9-5"}).json()["id"]
    r = client.put(_url(biz, rid), json={"question": "Hours?", "answer": "10-6"})
    assert r.status_code == 200 and r.json()["answer"] == "10-6"
    [chunk] = _chunks(biz)
    assert chunk.text == "Q: Hours?\nA: 10-6"


def test_delete_removes_the_reply_and_its_chunk(client, biz):
    rid = client.post(_url(biz), json={"question": "q", "answer": "a"}).json()["id"]
    assert client.delete(_url(biz, rid)).json() == {"deleted": True}
    assert client.get(_url(biz)).json() == []
    with tenant_session(biz) as s:
        assert s.scalar(select(func.count()).select_from(Chunk).where(Chunk.business_id == biz, Chunk.kind == "custom_reply")) == 0


def test_update_and_delete_of_an_unknown_reply_are_404(client, biz):
    missing = uuid.uuid4()
    assert client.put(_url(biz, missing), json={"question": "q", "answer": "a"}).status_code == 404
    assert client.delete(_url(biz, missing)).status_code == 404


def test_a_reply_of_another_business_cannot_be_changed(client, biz):
    other = uuid.uuid4()
    with tenant_session(other) as s:
        s.add(Business(id=other, name="Other"))
    try:
        rid = client.post(_url(other), json={"question": "q", "answer": "a"}).json()["id"]
        assert client.put(_url(biz, rid), json={"question": "x", "answer": "y"}).status_code == 404
        assert client.delete(_url(biz, rid)).status_code == 404
        assert client.get(_url(other)).json()[0]["answer"] == "a"
    finally:
        with tenant_session(other) as s:
            s.execute(delete(Business).where(Business.id == other))
