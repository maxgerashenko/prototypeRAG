"""Guest history and merge API (app/api/guests.py). Real Postgres."""

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from app.actions.booking import guests
from app.db import tenant_session
from app.db.models import Booking, Business, GroupSession
from app.main import app

T0 = datetime(2030, 6, 10, 10, tzinfo=timezone.utc)


@pytest.fixture
def client():
    return TestClient(app)


def _business():
    bid = uuid.uuid4()
    with tenant_session(bid) as s:
        s.add(Business(id=bid, name="Spa"))
    return bid


@pytest.fixture
def biz():
    bid = _business()
    yield bid
    with tenant_session(bid) as s:
        s.execute(delete(Business).where(Business.id == bid))


@pytest.fixture
def other_biz():
    bid = _business()
    yield bid
    with tenant_session(bid) as s:
        s.execute(delete(Business).where(Business.id == bid))


def _guest(bid, first, last="", phone=None):
    with tenant_session(bid) as s:
        return str(guests.find_or_create(s, bid, first, last, phone).id)


def _book(bid, guest_id, days=0, session=None):
    with tenant_session(bid) as s:
        start = T0 + timedelta(days=days)
        sid = None
        if session:
            gs = GroupSession(business_id=bid, starts_at=start, ends_at=start + timedelta(hours=1),
                              name=session, max_attendees=8)
            s.add(gs)
            s.flush()
            sid = gs.id
        b = Booking(business_id=bid, guest_id=uuid.UUID(guest_id), session_id=sid,
                    type="session" if sid else "appointment", starts_at=start,
                    ends_at=start + timedelta(hours=1), name="x", phone="+1", status="confirmed")
        s.add(b)
        s.flush()
        return str(b.id)


def test_list_masks_phone_counts_bookings_and_hides_merged(client, biz):
    a = _guest(biz, "Anna", "Kowalska", "+19294892284")
    b = _guest(biz, "Ann", "Kowalska")
    _book(biz, a, 0)
    _book(biz, a, 2)
    assert client.post(f"/guests/{b}/merge", json={"into": a}).status_code == 200
    rows = client.get(f"/businesses/{biz}/guests").json()
    assert [r["id"] for r in rows] == [a]
    assert rows[0]["phone"] == "***2284"
    assert rows[0]["booking_count"] == 2
    assert rows[0]["last_booking_at"].startswith("2030-06-12")
    assert rows[0]["display_name"] == "Anna Kowalska"


def test_detail_has_bookings_and_sessions_newest_first(client, biz):
    a = _guest(biz, "Anna", "Kowalska")
    _book(biz, a, 0, session="Yoga")
    _book(biz, a, 5)
    d = client.get(f"/guests/{a}").json()
    assert d["display_name"] == "Anna Kowalska"
    assert [b["starts_at"][:10] for b in d["bookings"]] == ["2030-06-15", "2030-06-10"]
    assert [x["name"] for x in d["sessions"]] == ["Yoga"]
    assert client.get(f"/guests/{uuid.uuid4()}").status_code == 404


def test_merge_moves_bookings_and_keeps_survivor_email(client, biz):
    a = _guest(biz, "Anna", "Kowalska")
    b = _guest(biz, "Ana", "Kowalska")
    _book(biz, b, 1)
    r = client.post(f"/guests/{b}/merge", json={"into": a})
    assert r.status_code == 200
    assert r.json()["id"] == a
    assert r.json()["email"] == "anna_kowalska@guest.invalid"
    with tenant_session(biz) as s:
        assert {x.guest_id for x in s.scalars(select(Booking).where(Booking.business_id == biz))} == {uuid.UUID(a)}
    assert client.post(f"/guests/{b}/merge", json={"into": a}).status_code == 409


def test_merge_across_businesses_is_refused(client, biz, other_biz):
    a = _guest(biz, "Anna", "Kowalska")
    x = _guest(other_biz, "Xenia", "Ivanova")
    assert client.post(f"/guests/{a}/merge", json={"into": x}).status_code == 404
    with tenant_session(biz) as s:
        assert s.get(guests.SessionGuest, uuid.UUID(a)).merged_into is None


def test_merge_into_itself_is_refused(client, biz):
    a = _guest(biz, "Anna", "Kowalska")
    assert client.post(f"/guests/{a}/merge", json={"into": a}).status_code == 400


def test_merged_guest_follows_to_the_survivor(client, biz):
    a = _guest(biz, "Anna", "Kowalska")
    b = _guest(biz, "Ana", "Kowalska")
    c = _guest(biz, "Anya", "Kowalska")
    _book(biz, b, 1)
    client.post(f"/guests/{b}/merge", json={"into": a})
    d = client.get(f"/guests/{b}").json()
    assert d["id"] == a and d["requested_id"] == b
    assert len(d["bookings"]) == 1
    # merging into an already merged guest lands on its survivor; no cycle back to b
    r = client.post(f"/guests/{c}/merge", json={"into": b})
    assert r.json()["id"] == a
    assert client.post(f"/guests/{a}/merge", json={"into": b}).status_code == 400


def test_merge_refused_when_both_guests_hold_a_seat_in_the_same_session(client, biz):
    a, b = _guest(biz, "Anna", "K"), _guest(biz, "Ann", "K")
    with tenant_session(biz) as s:
        start = T0
        gs = GroupSession(business_id=biz, starts_at=start, ends_at=start + timedelta(hours=1), name="Yoga",
                          max_attendees=8)
        s.add(gs)
        s.flush()
        for g in (a, b):
            s.add(Booking(business_id=biz, guest_id=uuid.UUID(g), session_id=gs.id, type="session",
                          starts_at=start, ends_at=start + timedelta(hours=1), name="x", phone="+1",
                          status="confirmed"))
    r = client.post(f"/guests/{b}/merge", json={"into": a})
    assert r.status_code == 409 and "same session" in r.json()["detail"]
    with tenant_session(biz) as s:  # nothing changed
        assert s.execute(select(Booking.guest_id).where(Booking.business_id == biz)).scalars().all().count(
            uuid.UUID(b)) == 1
    assert client.get(f"/guests/{b}").json()["id"] == b
