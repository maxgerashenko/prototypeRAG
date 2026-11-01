"""GET /businesses/{id}/bookings (app/api/bookings.py): the owner's day view. Real Postgres;
no fakes. Times come back in the business timezone as minutes since local midnight."""

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete

from app.actions.booking import rules as R
from app.api.bookings import open_hours
from app.db import tenant_session
from app.db.models import Booking, BookingRule, Business, Conversation
from app.main import app

NY = "America/New_York"


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def biz():
    bid = uuid.uuid4()
    with tenant_session(bid) as s:
        s.add(Business(id=bid, name="Zebra Spa", timezone=NY))
    yield bid
    with tenant_session(bid) as s:
        s.execute(delete(Business).where(Business.id == bid))


def _book(bid, start_utc, minutes=60, name="Ann", conversation_id=None, status="confirmed"):
    with tenant_session(bid) as s:
        s.add(Booking(business_id=bid, type="appointment", starts_at=start_utc,
                      ends_at=start_utc + timedelta(minutes=minutes), name=name, service="Massage",
                      conversation_id=conversation_id, status=status))


def utc(*a):
    return datetime(*a, tzinfo=timezone.utc)


def test_open_hours_from_rules():
    assert open_hours(R.Rules()) is None
    rules = R.Rules(hours={"mon": [["09:00", "17:30"]], "tue": [["07:00", "12:00"]]})
    assert open_hours(rules) == {"open": 7, "close": 18}
    assert open_hours(R.Rules(hours={"fri": [["18:00", "02:00"]]})) == {"open": 18, "close": 24}


def test_lists_local_day_and_skips_cancelled(client, biz):
    _book(biz, utc(2026, 11, 5, 19, 0), name="Ann")  # 14:00 EST
    _book(biz, utc(2026, 11, 6, 3, 30), name="Late")  # 22:30 EST on the 5th
    _book(biz, utc(2026, 11, 6, 6, 0), name="Next day")  # 01:00 EST on the 6th
    _book(biz, utc(2026, 11, 5, 20, 0), name="Gone", status="cancelled")
    res = client.get(f"/businesses/{biz}/bookings", params={"start": "2026-11-05", "end": "2026-11-05"})
    assert res.status_code == 200
    body = res.json()
    assert body["timezone"] == NY and body["hours"] is None
    assert [(b["name"], b["date"], b["start_min"], b["end_min"]) for b in body["bookings"]] == [
        ("Ann", "2026-11-05", 14 * 60, 15 * 60),
        ("Late", "2026-11-05", 22 * 60 + 30, 23 * 60 + 30),
    ]


def test_by_assistant_follows_the_conversation(client, biz):
    conv = uuid.uuid4()
    with tenant_session(biz) as s:
        s.add(Conversation(id=conv, business_id=biz, channel="voice"))
    _book(biz, utc(2026, 11, 5, 15, 0), name="Call", conversation_id=conv)
    _book(biz, utc(2026, 11, 5, 17, 0), name="Hand")
    res = client.get(f"/businesses/{biz}/bookings", params={"start": "2026-11-05", "end": "2026-11-05"})
    assert {b["name"]: b["by_assistant"] for b in res.json()["bookings"]} == {"Call": True, "Hand": False}


def test_hours_come_from_the_rules(client, biz):
    with tenant_session(biz) as s:
        s.add(BookingRule(business_id=biz, hours={"thu": [["09:00", "21:00"]]}))
    res = client.get(f"/businesses/{biz}/bookings", params={"start": "2026-11-05", "end": "2026-11-05"})
    assert res.json()["hours"] == {"open": 9, "close": 21}


def test_bad_input(client, biz):
    url = f"/businesses/{biz}/bookings"
    assert client.get(url, params={"start": "nope", "end": "2026-11-05"}).status_code == 422
    assert client.get(url, params={"start": "2026-11-05", "end": "2026-11-01"}).status_code == 422
    assert client.get(url, params={"start": "2026-01-01", "end": "2026-12-31"}).status_code == 422
    missing = uuid.uuid4()
    assert client.get(f"/businesses/{missing}/bookings", params={"start": "2026-11-05", "end": "2026-11-05"}).status_code == 404


def test_end_follows_the_wall_clock_across_dst_and_midnight(client, biz):
    _book(biz, utc(2026, 11, 1, 5, 30), 90)  # 01:30 EDT, fall-back at 02:00 -> ends 02:00 EST
    _book(biz, utc(2026, 11, 2, 4, 30), 90)  # 23:30 EST on the 1st, ends after midnight
    res = client.get(f"/businesses/{biz}/bookings", params={"start": "2026-11-01", "end": "2026-11-01"})
    rows = res.json()["bookings"]
    assert [(b["start_min"], b["end_min"]) for b in rows] == [(90, 120), (23 * 60 + 30, 24 * 60)]


def test_range_and_isolation(client, biz):
    other = uuid.uuid4()
    with tenant_session(other) as s:
        s.add(Business(id=other, name="Other", timezone=NY))
    try:
        _book(other, utc(2026, 11, 5, 19, 0), name="Not mine")
        _book(biz, utc(2026, 11, 7, 19, 0), name="B")
        _book(biz, utc(2026, 11, 5, 19, 0), name="A")
        url = f"/businesses/{biz}/bookings"
        body = client.get(url, params={"start": "2026-11-01", "end": "2026-11-30"}).json()
        assert [(b["name"], b["date"]) for b in body["bookings"]] == [("A", "2026-11-05"), ("B", "2026-11-07")]
        assert client.get(url, params={"start": "2026-11-01", "end": "2026-12-32"}).status_code == 422
        assert client.get(url, params={"start": "2026-10-01", "end": "2026-11-30"}).status_code == 200  # 61 days
        assert client.get(url, params={"start": "2026-10-01", "end": "2026-12-02"}).status_code == 422  # 63 days
    finally:
        with tenant_session(other) as s:
            s.execute(delete(Business).where(Business.id == other))


def test_get_lists_sessions_and_hides_attendee_rows(client, biz):
    from app.db.models import GroupSession
    conv = uuid.uuid4()
    with tenant_session(biz) as s:
        s.add(Conversation(id=conv, business_id=biz, channel="voice"))
        sess = GroupSession(business_id=biz, starts_at=utc(2026, 11, 5, 15, 0), ends_at=utc(2026, 11, 5, 16, 0),
                            name="Yoga", max_attendees=8)
        gone = GroupSession(business_id=biz, starts_at=utc(2026, 11, 5, 17, 0), ends_at=utc(2026, 11, 5, 18, 0),
                            name="Cancelled", max_attendees=8, status="cancelled")
        s.add_all([sess, gone])
        s.flush()
        for name, phone, c, status in [("Ann", "+1555", conv, "confirmed"), ("Bob", None, None, "confirmed"),
                                       ("Old", "+1666", None, "cancelled")]:
            s.add(Booking(business_id=biz, session_id=sess.id, type="session", starts_at=sess.starts_at,
                          ends_at=sess.ends_at, name=name, phone=phone, conversation_id=c, status=status))
        sid = str(sess.id)
    _book(biz, utc(2026, 11, 5, 19, 0), name="Solo")
    body = client.get(f"/businesses/{biz}/bookings", params={"start": "2026-11-05", "end": "2026-11-05"}).json()
    assert [b["name"] for b in body["bookings"]] == ["Solo"]
    assert len(body["sessions"]) == 1
    item = body["sessions"][0]
    assert (item["id"], item["date"], item["start_min"], item["end_min"], item["name"],
            item["max_attendees"], item["booked"]) == (sid, "2026-11-05", 10 * 60, 11 * 60, "Yoga", 8, 2)
    assert [(a["name"], a["phone"], a["by_assistant"]) for a in item["attendees"]] == [
        ("Ann", "+1555", True), ("Bob", None, False)]
    assert all(set(a) == {"id", "name", "phone", "by_assistant", "conversation_id"} for a in item["attendees"])
    # the id of the booking conversation comes through for the assistant's booking, null for a manual one
    assert [a["conversation_id"] for a in item["attendees"]] == [str(conv), None]
    other_day = client.get(f"/businesses/{biz}/bookings", params={"start": "2026-11-06", "end": "2026-11-06"}).json()
    assert other_day["sessions"] == []
