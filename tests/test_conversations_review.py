"""Review status on conversations (PATCH .../review, fields in list and detail). Needs Postgres."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete

from app.db import tenant_session
from app.db.models import Business, Conversation, Message
from app.main import app

T0 = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)


@pytest.fixture
def conv():
    bid = uuid.uuid4()
    with tenant_session(bid) as s:
        s.add(Business(id=bid, name="Review Spa"))
        c = Conversation(business_id=bid, channel="voice", started_at=T0, ended_at=T0 + timedelta(seconds=30))
        s.add(c)
        s.flush()
        cid = c.id
        s.add(Message(business_id=bid, conversation_id=cid, role="user", content="Hello?", created_at=T0 + timedelta(seconds=5)))
    yield bid, cid
    with tenant_session(bid) as s:
        s.execute(delete(Business).where(Business.id == bid))


def _url(bid, cid):
    return f"/businesses/{bid}/conversations/{cid}"


def test_unchecked_by_default(conv):
    bid, cid = conv
    client = TestClient(app)
    d = client.get(_url(bid, cid)).json()
    assert d["review_status"] is None and d["review_note"] is None
    row = client.get(f"/businesses/{bid}/conversations").json()[0]
    assert row["review_status"] is None and row["review_note"] is None


def test_patch_sets_and_clears(conv):
    bid, cid = conv
    client = TestClient(app)
    r = client.patch(_url(bid, cid) + "/review", json={"status": "in_testing", "note": "check booking"})
    assert r.status_code == 200
    assert r.json()["review_status"] == "in_testing" and r.json()["review_note"] == "check booking"
    assert client.get(_url(bid, cid)).json()["review_status"] == "in_testing"
    assert client.get(f"/businesses/{bid}/conversations").json()[0]["review_note"] == "check booking"
    with tenant_session(bid) as s:
        assert s.get(Conversation, cid).reviewed_at is not None
    r = client.patch(_url(bid, cid) + "/review", json={"status": None, "note": None})
    assert r.status_code == 200 and r.json()["review_status"] is None
    with tenant_session(bid) as s:
        c = s.get(Conversation, cid)
        assert c.review_status is None and c.review_note is None and c.reviewed_at is None


def test_unknown_conversation_and_other_business_404(conv):
    bid, cid = conv
    client = TestClient(app)
    body = {"status": "fixed", "note": None}
    assert client.patch(_url(bid, uuid.uuid4()) + "/review", json=body).status_code == 404
    assert client.patch(_url(uuid.uuid4(), cid) + "/review", json=body).status_code == 404


def test_invalid_status_is_422(conv):
    bid, cid = conv
    r = TestClient(app).patch(_url(bid, cid) + "/review", json={"status": "bogus", "note": None})
    assert r.status_code == 422
