"""Day view of Google sessions (#167): shown to every business (demo rule), name read-only, seats editable."""

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete

from app.db import tenant_session
from app.db.models import Booking, Business, GroupSession
from app.main import app

DAY = "2030-06-10"
START = datetime(2030, 6, 10, 18, 0, tzinfo=timezone.utc)  # 14:00 New York


def _biz(name):
    bid = uuid.uuid4()
    with tenant_session(bid) as s:
        s.add(Business(id=bid, name=name, timezone="America/New_York"))
    return bid


@pytest.fixture
def two():
    owner, other = _biz("Owner Sauna"), _biz("Other Barber")
    with tenant_session(owner) as s:
        row = GroupSession(business_id=owner, starts_at=START, ends_at=START + timedelta(minutes=90), name="Sauna social",
                           max_attendees=8, source="google", external_ref=f"g-{uuid.uuid4()}")
        s.add(row)
        s.flush()
        s.add(Booking(business_id=owner, session_id=row.id, type="session", starts_at=START, ends_at=row.ends_at, name="Ann"))
        sid = row.id
    yield owner, other, sid
    for b in (owner, other):
        with tenant_session(b) as s:
            s.execute(delete(Business).where(Business.id == b))


def test_every_business_sees_the_google_session_with_its_owner(two):
    owner, other, sid = two
    c = TestClient(app)
    for viewer in (owner, other):
        body = c.get(f"/businesses/{viewer}/bookings?start={DAY}&end={DAY}").json()
        [s] = body["sessions"]
        assert (s["id"], s["source"], s["business_id"], s["booked"]) == (str(sid), "google", str(owner), 1)
        assert (s["start_min"], s["end_min"]) == (14 * 60, 15 * 60 + 30)
        assert body["busy"] == []


def test_seats_editable_from_another_business_but_name_is_not(two):
    owner, other, sid = two
    c = TestClient(app)
    assert c.patch(f"/businesses/{other}/sessions/{sid}", json={"name": "Mine"}).status_code == 422
    r = c.patch(f"/businesses/{other}/sessions/{sid}", json={"max_attendees": 5, "private": True})
    assert r.status_code == 200
    assert (r.json()["max_attendees"], r.json()["private"], r.json()["booked"]) == (5, True, 1)
