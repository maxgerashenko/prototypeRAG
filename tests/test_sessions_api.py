"""Group sessions (app/actions/booking/sessions.py, app/api/sessions.py). Real Postgres; NY
timezone; the clock is the functions' `now` parameter, API tests use dates far in the future."""

import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from app.actions import sms
from app.actions.booking import sessions as S
from app.db import tenant_session
from app.db.models import Booking, Business, BusinessProfile, Conversation, GroupSession
from app.main import app

NY = "America/New_York"
DAY = "2030-06-10"
NOW = datetime(2030, 1, 1, tzinfo=timezone.utc)


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


@pytest.fixture(autouse=True)
def clean_outbox():
    sms.OUTBOX.clear()
    yield
    sms.OUTBOX.clear()


def utc(*a):
    return datetime(*a, tzinfo=timezone.utc)


def _post(client, bid, hour=10, name="Yoga", max_attendees=8, day=DAY):
    return client.post(f"/businesses/{bid}/sessions",
                       json={"date": day, "hour": hour, "name": name, "max_attendees": max_attendees})


def _attend(bid, sid, name="Ann", phone="+15550001", conversation_id=None, status="confirmed"):
    with tenant_session(bid) as s:
        row = s.get(GroupSession, uuid.UUID(sid))
        b = Booking(business_id=bid, session_id=row.id, type="session", starts_at=row.starts_at,
                    ends_at=row.ends_at, name=name, phone=phone, conversation_id=conversation_id, status=status)
        s.add(b)
        s.flush()
        return str(b.id)


def _booking_status(bid, booking_id):
    with tenant_session(bid) as s:
        return s.get(Booking, uuid.UUID(booking_id)).status


def test_sms_outbox_is_capped():
    for i in range(sms.OUTBOX_MAX + 5):
        sms.send_sms("+1", f"m{i}")
    assert len(sms.OUTBOX) == sms.OUTBOX_MAX
    assert sms.OUTBOX[0]["body"] == "m5" and sms.OUTBOX[-1] == {"to": "+1", "body": f"m{sms.OUTBOX_MAX + 4}"}


def test_local_hour_start():
    assert S.local_hour_start("2026-11-05", 10, NY).utcoffset() == timedelta(hours=-5)
    # clocks go back 2026-11-01 02:00 -> 01:00; hour 1 is ambiguous and resolves to the first (EDT)
    assert S.local_hour_start("2026-11-01", 1, NY).utcoffset() == timedelta(hours=-4)
    for day, hour in [("2026-03-08", 2), ("nope", 10), ("2026-11-05", 24), ("2026-11-05", -1)]:
        with pytest.raises(S.SessionError) as e:
            S.local_hour_start(day, hour, NY)
        assert e.value.status == 422


def test_create_returns_item(client, biz):
    res = _post(client, biz, name="  Yoga  ")
    assert res.status_code == 201
    body = res.json()
    assert (body["date"], body["start_min"], body["end_min"], body["name"], body["max_attendees"],
            body["booked"], body["attendees"]) == (DAY, 600, 660, "Yoga", 8, 0, [])
    with tenant_session(biz) as s:
        row = s.scalars(select(GroupSession).where(GroupSession.business_id == biz)).one()
        assert row.starts_at == utc(2030, 6, 10, 14, 0)  # 10:00 EDT


def test_create_validation(client, biz):
    assert _post(client, biz, name="   ").status_code == 422
    assert _post(client, biz, name="x" * 61).status_code == 422
    assert _post(client, biz, name="x" * 60).status_code == 201
    assert _post(client, biz, hour=11, max_attendees=0).status_code == 422
    assert _post(client, biz, hour=11, max_attendees=31).status_code == 422
    assert _post(client, biz, hour=11, max_attendees=30).status_code == 201
    assert _post(client, biz, hour=25).status_code == 422
    assert _post(client, biz, day="junk").status_code == 422
    assert _post(client, uuid.uuid4()).status_code == 404


def test_create_needs_future_and_rejects_dst_gap(biz):
    with pytest.raises(S.SessionError) as e:
        S.create_session(biz, DAY, 10, "Yoga", 5, now=utc(2030, 6, 10, 14, 0))  # starts exactly now
    assert e.value.status == 422
    S.create_session(biz, DAY, 10, "Yoga", 5, now=utc(2030, 6, 10, 13, 59))
    with pytest.raises(S.SessionError) as e:
        S.create_session(biz, "2026-03-08", 2, "Gap", 5, now=utc(2026, 1, 1))
    assert e.value.status == 422


def test_same_hour_409_and_cancelled_hour_is_free(client, biz):
    sid = _post(client, biz).json()["id"]
    assert _post(client, biz, name="Other").status_code == 409
    assert _post(client, biz, hour=11).status_code == 201
    assert client.delete(f"/businesses/{biz}/sessions/{sid}").status_code == 200
    assert _post(client, biz, name="Again").status_code == 201


def test_overlapping_one_to_one_booking_409(client, biz):
    with tenant_session(biz) as s:  # 10:30-11:30 local overlaps hour 10 by 30 minutes
        s.add(Booking(business_id=biz, type="appointment", starts_at=utc(2030, 6, 10, 14, 30),
                      ends_at=utc(2030, 6, 10, 15, 30), name="Bob"))
        s.add(Booking(business_id=biz, type="appointment", starts_at=utc(2030, 6, 10, 18, 0),
                      ends_at=utc(2030, 6, 10, 19, 0), name="Gone", status="cancelled"))
    assert _post(client, biz, hour=10).status_code == 409
    assert _post(client, biz, hour=11).status_code == 409  # 11:00-11:30 still overlaps
    assert _post(client, biz, hour=12).status_code == 201  # back to back is fine
    assert _post(client, biz, hour=14).status_code == 201  # cancelled booking does not block


def test_concurrent_creates_one_wins(biz):
    def go(_):
        try:
            return S.create_session(biz, DAY, 10, "Yoga", 5, now=NOW)["id"]
        except S.SessionError as exc:
            return exc.status
    with ThreadPoolExecutor(6) as pool:
        results = list(pool.map(go, range(6)))
    assert sum(1 for r in results if isinstance(r, str)) == 1
    assert [r for r in results if not isinstance(r, str)] == [409] * 5


def test_attendees_carry_the_booking_conversation_id(client, biz):
    sid = _post(client, biz).json()["id"]
    conv = uuid.uuid4()
    with tenant_session(biz) as s:
        s.add(Conversation(id=conv, business_id=biz, channel="voice"))
    _attend(biz, sid, "Ann", conversation_id=conv)
    _attend(biz, sid, "Bob")
    body = client.patch(f"/businesses/{biz}/sessions/{sid}", json={"name": "Yoga 2"}).json()
    got = {a["name"]: (a["by_assistant"], a["conversation_id"]) for a in body["attendees"]}
    assert got == {"Ann": (True, str(conv)), "Bob": (False, None)}


def test_update_name_and_max(client, biz):
    sid = _post(client, biz).json()["id"]
    _attend(biz, sid, "Ann")
    _attend(biz, sid, "Bob")
    url = f"/businesses/{biz}/sessions/{sid}"
    res = client.patch(url, json={"name": " Pilates "})
    assert res.status_code == 200 and res.json()["name"] == "Pilates" and res.json()["max_attendees"] == 8
    res = client.patch(url, json={"max_attendees": 2})
    assert res.status_code == 200 and res.json()["max_attendees"] == 2 and res.json()["booked"] == 2
    res = client.patch(url, json={"max_attendees": 1})
    assert res.status_code == 422 and res.json()["detail"] == "2 callers are booked — remove some first"
    assert client.patch(url, json={"name": ""}).status_code == 422
    assert client.patch(url, json={"max_attendees": 99}).status_code == 422
    assert client.patch(f"/businesses/{biz}/sessions/{uuid.uuid4()}", json={"name": "x"}).status_code == 404


def test_ended_session_is_read_only(biz):
    item = S.create_session(biz, DAY, 10, "Yoga", 5, now=NOW)
    sid = uuid.UUID(item["id"])
    bid = _attend(biz, item["id"])
    after = utc(2030, 6, 10, 15, 0)  # exactly the end
    for call in (lambda: S.update_session(biz, sid, name="x", now=after),
                 lambda: S.cancel_session(biz, sid, now=after),
                 lambda: S.remove_attendee(biz, sid, uuid.UUID(bid), now=after)):
        with pytest.raises(S.SessionError) as e:
            call()
        assert (e.value.status, e.value.message) == (422, "This session has ended")
    S.update_session(biz, sid, name="x", now=utc(2030, 6, 10, 14, 59))  # still running: allowed


def test_delete_cancels_session_and_texts_attendees(client, biz):
    sid = _post(client, biz).json()["id"]
    a = _attend(biz, sid, "Ann", "+15550001")
    b = _attend(biz, sid, "Bob", None)
    _attend(biz, sid, "Old", "+15550009", status="cancelled")
    res = client.delete(f"/businesses/{biz}/sessions/{sid}")
    assert res.status_code == 200
    assert res.json() == {"cancelled_bookings": 2, "texted": 1, "no_phone": 1}
    assert [m["to"] for m in sms.OUTBOX] == ["+15550001"]
    body = sms.OUTBOX[0]["body"]
    assert body == "Hi Ann, your spot in Yoga on Mon 10 Jun, 10:00 was cancelled by Zebra Spa."
    assert _booking_status(biz, a) == _booking_status(biz, b) == "cancelled"
    with tenant_session(biz) as s:
        row = s.get(GroupSession, uuid.UUID(sid))
        assert row.status == "cancelled" and row.cancelled_at is not None
    assert client.delete(f"/businesses/{biz}/sessions/{sid}").status_code == 404  # already gone


def test_message_uses_profile_name(client, biz):
    with tenant_session(biz) as s:
        s.add(BusinessProfile(business_id=biz, name="Zebra Day Spa"))
    sid = _post(client, biz).json()["id"]
    _attend(biz, sid)
    client.delete(f"/businesses/{biz}/sessions/{sid}")
    assert sms.OUTBOX[0]["body"].endswith("cancelled by Zebra Day Spa.")


def test_remove_attendee_only_that_booking(client, biz):
    sid = _post(client, biz).json()["id"]
    a = _attend(biz, sid, "Ann", "+15550001")
    b = _attend(biz, sid, "Bob", "+15550002")
    c = _attend(biz, sid, "Cy", None)
    res = client.delete(f"/businesses/{biz}/sessions/{sid}/attendees/{a}")
    assert res.status_code == 200
    body = res.json()
    assert body["texted"] is True and body["booked"] == 2
    assert [x["name"] for x in body["attendees"]] == ["Bob", "Cy"]
    assert _booking_status(biz, a) == "cancelled"
    assert _booking_status(biz, b) == _booking_status(biz, c) == "confirmed"
    assert [m["to"] for m in sms.OUTBOX] == ["+15550001"]
    res = client.delete(f"/businesses/{biz}/sessions/{sid}/attendees/{c}")
    assert res.json()["texted"] is False and len(sms.OUTBOX) == 1
    assert client.delete(f"/businesses/{biz}/sessions/{sid}/attendees/{a}").status_code == 404  # already removed
    assert client.delete(f"/businesses/{biz}/sessions/{sid}/attendees/{uuid.uuid4()}").status_code == 404


def test_remove_booking_of_another_session_404(client, biz):
    s1 = _post(client, biz, hour=10).json()["id"]
    s2 = _post(client, biz, hour=12).json()["id"]
    b = _attend(biz, s2)
    assert client.delete(f"/businesses/{biz}/sessions/{s1}/attendees/{b}").status_code == 404
    assert _booking_status(biz, b) == "confirmed"


def test_failing_send_does_not_undo(client, biz, monkeypatch):
    def boom(to, body):
        raise RuntimeError("twilio down")
    monkeypatch.setattr(sms, "send_sms", boom)
    sid = _post(client, biz).json()["id"]
    a = _attend(biz, sid, "Ann")
    b = _attend(biz, sid, "Bob", "+15550002")
    res = client.delete(f"/businesses/{biz}/sessions/{sid}/attendees/{a}")
    assert res.status_code == 200 and res.json()["texted"] is False
    assert _booking_status(biz, a) == "cancelled"
    res = client.delete(f"/businesses/{biz}/sessions/{sid}")
    assert res.status_code == 200
    assert res.json() == {"cancelled_bookings": 1, "texted": 0, "no_phone": 0}
    assert _booking_status(biz, b) == "cancelled"
    with tenant_session(biz) as s:
        assert s.get(GroupSession, uuid.UUID(sid)).status == "cancelled"


def test_other_business_gets_404(client, biz):
    other = uuid.uuid4()
    with tenant_session(other) as s:
        s.add(Business(id=other, name="Other", timezone=NY))
    try:
        sid = _post(client, biz).json()["id"]
        a = _attend(biz, sid)
        assert client.patch(f"/businesses/{other}/sessions/{sid}", json={"name": "x"}).status_code == 404
        assert client.delete(f"/businesses/{other}/sessions/{sid}").status_code == 404
        assert client.delete(f"/businesses/{other}/sessions/{sid}/attendees/{a}").status_code == 404
        assert _booking_status(biz, a) == "confirmed"
        assert sms.OUTBOX == []
    finally:
        with tenant_session(other) as s:
            s.execute(delete(Business).where(Business.id == other))


@pytest.mark.parametrize("starts", [utc(2026, 11, 1, 5), utc(2026, 3, 8, 6)])
def test_dst_change_day_session_is_one_hour(biz, starts):
    # 01:00 EDT on the fall-back day, 01:00 EST on the spring-forward day
    with tenant_session(biz) as s:
        s.add(GroupSession(business_id=biz, starts_at=starts, ends_at=starts + timedelta(hours=1),
                           name="Yoga", max_attendees=5))
    with tenant_session(biz) as s:
        items = S.sessions_in_range(s, biz, starts - timedelta(hours=1), starts + timedelta(hours=1),
                                    S._zone(NY))
    assert [(i["start_min"], i["end_min"]) for i in items] == [(60, 120)]


def test_cancelled_session_attendee_edits_404(client, biz):
    sid = _post(client, biz).json()["id"]
    a = _attend(biz, sid)
    assert client.delete(f"/businesses/{biz}/sessions/{sid}").status_code == 200
    assert client.patch(f"/businesses/{biz}/sessions/{sid}", json={"name": "x"}).status_code == 404
    assert client.delete(f"/businesses/{biz}/sessions/{sid}/attendees/{a}").status_code == 404


def test_patch_empty_body_changes_nothing(client, biz):
    sid = _post(client, biz).json()["id"]
    before = client.patch(f"/businesses/{biz}/sessions/{sid}", json={}).json()
    assert before["name"] == "Yoga" and before["max_attendees"] == 8 and before["id"] == sid


def test_deleting_the_business_cascades():
    bid = uuid.uuid4()
    with tenant_session(bid) as s:
        s.add(Business(id=bid, name="Temp", timezone=NY))
    sid = _post(TestClient(app), bid).json()["id"]
    _attend(bid, sid)
    with tenant_session(bid) as s:
        s.execute(delete(Business).where(Business.id == bid))
    with tenant_session(bid) as s:
        assert s.scalar(select(GroupSession.id).where(GroupSession.business_id == bid)) is None
        assert s.scalar(select(Booking.id).where(Booking.business_id == bid)) is None


def test_private_session_keeps_its_limit(client, biz):
    res = client.post(f"/businesses/{biz}/sessions", json={"date": DAY, "hour": 10, "name": "Retreat", "max_attendees": 8, "private": True})
    assert res.status_code == 201
    assert res.json()["max_attendees"] == 8 and res.json()["private"] is True
    sid = res.json()["id"]
    url = f"/businesses/{biz}/sessions/{sid}"
    # the limit is still enforced for a private session
    for i in range(8):
        _attend(biz, sid, f"P{i}", f"+1555100{i:02d}")
    assert client.patch(url, json={"max_attendees": 5}).status_code == 422  # 8 booked
    # renaming leaves both fields alone
    res = client.patch(url, json={"name": "Retreat 2"})
    assert res.json()["private"] is True and res.json()["max_attendees"] == 8
    res = client.patch(url, json={"private": False})
    assert res.json()["private"] is False and res.json()["max_attendees"] == 8
    res = client.patch(url, json={"private": True, "max_attendees": 12})
    assert res.json()["private"] is True and res.json()["max_attendees"] == 12
    # a limit is required; public by default
    assert _post(client, biz, hour=12, max_attendees=None).status_code == 422
    assert _post(client, biz, hour=13).json()["private"] is False
