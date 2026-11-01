"""Group sessions as Google Calendar events (#149 part 2) against a fake calendar client.
Real Postgres. The live check with a real calendar is #150."""

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import delete, select

from app.actions.booking import session_events as G
from app.actions.booking import sessions as S
from app.actions.booking import tools as T
from app.actions.booking.google_client import CalendarError, PreconditionFailed
from app.actions.registry import ActionContext
from app.config import get_settings
from app.db import tenant_session
from app.db.models import Booking, Business, Conversation, GroupSession

NY = "America/New_York"
CAL = "demo@group.calendar.google.com"
DAY = "2030-06-10"
NOW = datetime(2030, 1, 1, tzinfo=timezone.utc)


class FakeCalendar:
    def __init__(self):
        self.events: dict[str, dict] = {}
        self.fail: set[str] = set()
        self.stale_etag_once = False
        self.n = 0

    def _check(self, op):
        if op in self.fail:
            raise CalendarError(f"{op} failed")

    def insert_event(self, calendar_id, body):
        self._check("insert")
        assert calendar_id == CAL
        eid = body.get("id") or f"ev{len(self.events) + 1}"
        self.events.setdefault(eid, {**body, "id": eid, "etag": "e0"})
        return eid

    def delete_event(self, calendar_id, event_id):
        self._check("delete")
        self.events.pop(event_id, None)

    def get_event(self, calendar_id, event_id):
        self._check("get")
        return dict(self.events[event_id]) if event_id in self.events else None

    def patch_event(self, calendar_id, event_id, body, etag=None):
        self._check("patch")
        ev = self.events[event_id]
        if self.stale_etag_once:  # somebody edited the event between our read and write
            self.stale_etag_once = False
            ev["etag"] = "changed"
            raise PreconditionFailed("412")
        if etag and etag != ev["etag"]:
            raise PreconditionFailed("412")
        self.n += 1
        ev.update(body)
        ev["etag"] = f"e{self.n}"
        return ev["etag"]

    def busy(self, *a):
        return []


@pytest.fixture
def cal(monkeypatch):
    fake = FakeCalendar()
    G.set_client(fake)
    monkeypatch.setattr(get_settings(), "google_calendar_id", CAL)
    yield fake
    G.set_client(None)


@pytest.fixture
def biz():
    bid = uuid.uuid4()
    with tenant_session(bid) as s:
        s.add(Business(id=bid, name="Sauna", timezone=NY, settings={"enabled_tools": ["list_sessions", "join_session"]}))
    yield bid
    with tenant_session(bid) as s:
        s.execute(delete(Business).where(Business.id == bid))


def _conv(biz):
    cid = uuid.uuid4()
    with tenant_session(biz) as s:
        s.add(Conversation(id=cid, business_id=biz, channel="voice"))
    return cid


def _make(biz, seats=2, **kw):
    item = S.create_session(biz, DAY, 17, "Poland Session", seats, now=NOW, **kw)
    with tenant_session(biz) as s:
        row = s.get(GroupSession, uuid.UUID(item["id"]))
        return row.id, row.external_ref


def test_create_writes_event_with_seats_in_title(cal, biz):
    sid, ref = _make(biz)
    assert ref == sid.hex and cal.events[ref]["summary"] == "Poland Session · 0/2 attendees"
    assert cal.events[ref]["extendedProperties"]["private"]["session_id"] == str(sid)
    assert "attendees" not in cal.events[ref]  # nobody is invited


def test_create_rolls_back_when_google_fails(cal, biz):
    cal.fail.add("insert")
    with pytest.raises(S.SessionError) as e:
        S.create_session(biz, DAY, 17, "Yoga", 2, now=NOW)
    assert e.value.status == 502
    with tenant_session(biz) as s:
        assert s.scalar(select(GroupSession.id).where(GroupSession.business_id == biz)) is None


def test_join_until_full_updates_title(cal, biz):
    sid, ref = _make(biz)
    code = S.session_ref(sid)
    r = S.join_session(biz, code, _conv(biz), "Ann", now=NOW)
    assert r["booked"] == 1 and cal.events[ref]["summary"] == "Poland Session · 1/2 attendees"
    S.join_session(biz, code, _conv(biz), "Bob", now=NOW)
    assert cal.events[ref]["summary"] == "Poland Session · 2/2 attendees · FULL"
    with pytest.raises(S.SessionError) as e:
        S.join_session(biz, code, _conv(biz), "Cy", now=NOW)
    assert e.value.status == 409


def test_same_conversation_cannot_join_twice(cal, biz):
    sid, _ = _make(biz, seats=5)
    conv = _conv(biz)
    S.join_session(biz, S.session_ref(sid), conv, "Ann", now=NOW)
    with pytest.raises(S.SessionError) as e:
        S.join_session(biz, S.session_ref(sid), conv, "Ann", now=NOW)
    assert e.value.status == 409


def test_failed_title_patch_keeps_the_seat_and_reconcile_retries(cal, biz):
    sid, ref = _make(biz)
    cal.fail.add("patch")
    S.join_session(biz, S.session_ref(sid), _conv(biz), "Ann", now=NOW)
    with tenant_session(biz) as s:
        assert G._booked(s, biz, sid) == 1
    assert cal.events[ref]["summary"].endswith("0/2 attendees")
    cal.fail.clear()
    ev = cal.events[ref]
    row = G.EventRow(ref, ev["summary"], NOW + timedelta(days=1), NOW + timedelta(days=1, hours=1), "confirmed", kind="session")
    G.reconcile_events(CAL, [row], now=NOW)
    assert cal.events[ref]["summary"].endswith("1/2 attendees")


def test_patch_retries_on_412(cal, biz):
    sid, ref = _make(biz)
    cal.stale_etag_once = True
    S.join_session(biz, S.session_ref(sid), _conv(biz), "Ann", now=NOW)
    assert cal.events[ref]["summary"].endswith("1/2 attendees")


def test_private_and_ended_sessions_cannot_be_joined(cal, biz):
    sid, _ = _make(biz, private=True)
    with pytest.raises(S.SessionError) as e:
        S.join_session(biz, S.session_ref(sid), None, "Ann", now=NOW)
    assert e.value.status == 403
    assert S.list_open(biz, now=NOW) == []


def test_cancel_session_deletes_event_and_failure_keeps_it(cal, biz):
    sid, ref = _make(biz)
    cal.fail.add("delete")
    with pytest.raises(S.SessionError):
        S.cancel_session(biz, sid, now=NOW)
    with tenant_session(biz) as s:
        assert s.get(GroupSession, sid).status == "active"
    cal.fail.clear()
    S.cancel_session(biz, sid, now=NOW)
    assert ref not in cal.events


def test_remove_attendee_updates_title(cal, biz):
    sid, ref = _make(biz)
    S.join_session(biz, S.session_ref(sid), _conv(biz), "Ann", now=NOW)
    with tenant_session(biz) as s:
        bid = s.scalar(select(Booking.id).where(Booking.session_id == sid))
    S.remove_attendee(biz, sid, bid, now=NOW)
    assert cal.events[ref]["summary"].endswith("0/2 attendees")


# --- Google -> sessions ----------------------------------------------------------------------------

def _ev(i="g1", summary="5–6pm Poland Session Sauna 5", start=None, hours=1, **kw):
    start = start or NOW + timedelta(days=2)
    return G.EventRow(i, summary, start, start + timedelta(hours=hours), kw.pop("status", "confirmed"), **kw)


def _owner(biz, monkeypatch):
    monkeypatch.setattr(get_settings(), "google_calendar_owner_business_id", str(biz))


def test_typed_event_becomes_a_joinable_session(cal, biz, monkeypatch):
    _owner(biz, monkeypatch)
    cal.events["g1"] = {"id": "g1", "etag": "e0", "summary": "5–6pm Poland Session Sauna 5"}
    assert G.reconcile_events(CAL, [_ev(hours=2)], now=NOW)["created"] == 1
    assert cal.events["g1"]["summary"] == f"5–6pm Poland Session Sauna 5 · 0/{G.DEFAULT_SEATS} attendees"
    rows = S.list_open(biz, now=NOW)
    assert rows[0]["name"] == "5–6pm Poland Session Sauna 5" and not rows[0]["full"]
    # the title we wrote comes back through the sync: nothing changes, nothing is re-patched
    n = cal.n
    assert G.reconcile_events(CAL, [_ev(summary=cal.events["g1"]["summary"], hours=2)], now=NOW) == {
        "created": 0, "moved": 0, "renamed": 0, "cancelled": 0}
    assert cal.n == n
    r = S.join_session(biz, rows[0]["ref"], _conv(biz), "Ann", now=NOW)
    assert r["booked"] == 1


def test_imported_session_item_has_its_real_length(cal, biz, monkeypatch):
    _owner(biz, monkeypatch)
    cal.events["g1"] = {"id": "g1", "etag": "e0", "summary": "x"}
    start = datetime(2030, 6, 10, 21, 0, tzinfo=timezone.utc)  # 17:00 New York
    G.reconcile_events(CAL, [_ev(start=start, hours=2)], now=NOW)
    from zoneinfo import ZoneInfo
    with tenant_session(biz) as s:
        items = S.sessions_in_range(s, biz, start - timedelta(hours=1), start + timedelta(hours=1), ZoneInfo(NY))
    assert (items[0]["start_min"], items[0]["end_min"], items[0]["source"]) == (17 * 60, 19 * 60, "google")


def test_event_moved_and_deleted_in_google(cal, biz, monkeypatch):
    _owner(biz, monkeypatch)
    cal.events["g1"] = {"id": "g1", "etag": "e0", "summary": "Yoga"}
    G.reconcile_events(CAL, [_ev(summary="Yoga")], now=NOW)
    rows = S.list_open(biz, now=NOW)
    S.join_session(biz, rows[0]["ref"], _conv(biz), "Ann", now=NOW)
    later = NOW + timedelta(days=3)
    assert G.reconcile_events(CAL, [_ev(summary="Yoga", start=later)], now=NOW)["moved"] == 1
    with tenant_session(biz) as s:
        assert s.scalar(select(Booking.starts_at).where(Booking.status == "confirmed")) == later
    assert G.reconcile_events(CAL, [_ev(summary="Yoga", start=later, status="cancelled")], now=NOW)["cancelled"] == 1
    with tenant_session(biz) as s:
        assert s.scalar(select(GroupSession.status)) == "cancelled"
        assert s.scalar(select(Booking.status)) == "cancelled"
    assert S.list_open(biz, now=NOW) == []


def test_free_busy_booking_and_long_events_are_not_sessions(cal, biz, monkeypatch):
    _owner(biz, monkeypatch)
    skip = [_ev("a", transparency="transparent"), _ev("b", kind="booking"), _ev("c", hours=24),
            _ev("d", status="cancelled"), _ev("e", start=NOW - timedelta(days=1))]
    assert G.reconcile_events(CAL, skip, now=NOW)["created"] == 0


def test_no_google_means_no_calendar_calls(biz):
    G.set_client(None)  # booking_backend is "internal" in tests
    item = S.create_session(biz, DAY, 17, "Yoga", 2, now=NOW)
    assert S.join_session(biz, item["id"][:8], None, "Ann", now=NOW)["booked"] == 1


def test_base_name_and_title():
    assert G.base_name("Sauna 5 · 3/3 attendees · FULL") == "Sauna 5"
    assert G.base_name("  · 1/2 attendees") == "Session"
    assert G.title("Sauna", 3, 3) == "Sauna · 3/3 attendees · FULL"


def test_tools(cal, biz):
    sid, _ = _make(biz, seats=1)
    ctx = ActionContext(business_id=biz, conversation_id=None)
    out = T.list_sessions(ctx, {"date": DAY})
    assert S.session_ref(sid) in out and "1 of 1 seats left" in out
    assert "Booked Ann" in T.join_session(ctx, {"session": S.session_ref(sid), "name": "Ann"})
    assert "full" in T.join_session(ctx, {"session": S.session_ref(sid), "name": "Bob"}).lower()
    assert "FULL" in T.list_sessions(ctx, {"date": DAY})
    assert T.join_session(ctx, {"session": S.session_ref(sid)}).startswith("Error")
