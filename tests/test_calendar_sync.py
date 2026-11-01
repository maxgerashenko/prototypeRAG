"""Google Calendar -> Postgres sync (#165) against a fake Google client. Needs Postgres only.
The live check with a real calendar is on the "Test on Mac" list (plan/03-voice-channel.md)."""

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import delete, select

from app.actions.booking import calendar_sync as cs
from app.actions.booking.google_client import (
    CalendarError, EventExists, EventsPage, GoogleCalendarClient, PreconditionFailed, SyncTokenExpired,
)
from app.db import tenant_session
from app.db.models import Booking, Business, CalendarEvent, CalendarSyncState
from app.db.session import SessionLocal

NOW = datetime(2026, 11, 1, 12, 0, tzinfo=timezone.utc)


def ev(eid, start="2026-11-05T15:00:00Z", end="2026-11-05T16:00:00Z", **extra):
    return {"id": eid, "etag": f'"{eid}-1"', "status": "confirmed", "summary": f"Event {eid}",
            "updated": "2026-11-01T10:00:00Z", "start": {"dateTime": start}, "end": {"dateTime": end}, **extra}


class FakeGoogle:
    """events.list with sync tokens: a full list returns everything and token N; a token-N list
    returns what changed since N (the test calls `change`/`remove`)."""

    def __init__(self, *events):
        self.events = {e["id"]: e for e in events}
        self.version = 1
        self.changes: list[tuple[int, dict]] = []
        self.expired: set[str] = set()
        self.fail = False
        self.calls: list[dict] = []

    def change(self, event):
        self.version += 1
        self.events[event["id"]] = event
        self.changes.append((self.version, event))

    def remove(self, eid, *, tombstone=True):
        self.version += 1
        self.events.pop(eid, None)
        if tombstone:
            self.changes.append((self.version, {"id": eid, "status": "cancelled"}))

    def list_events(self, calendar_id, *, sync_token=None, time_min=None, time_max=None, page_token=None):
        self.calls.append({"sync_token": sync_token, "time_min": time_min, "time_max": time_max})
        if self.fail:
            raise CalendarError("unreachable")
        if sync_token:
            assert time_min is None and time_max is None  # Google refuses both together
            if sync_token in self.expired:
                raise SyncTokenExpired("410")
            since = int(sync_token.removeprefix("tok"))
            return EventsPage([e for v, e in self.changes if v > since], None, f"tok{self.version}")
        items = list(self.events.values())
        return EventsPage(items[:2], "p2", None) if page_token is None and len(items) > 2 else (
            EventsPage(items[2:], None, f"tok{self.version}") if page_token else EventsPage(items, None, f"tok{self.version}"))

    def get_event(self, calendar_id, event_id):
        return self.events.get(event_id)


@pytest.fixture
def cal_id():
    cid = f"cal-{uuid.uuid4()}@example.com"
    yield cid
    with SessionLocal() as s, s.begin():
        s.execute(delete(CalendarEvent).where(CalendarEvent.calendar_id == cid))
        s.execute(delete(CalendarSyncState).where(CalendarSyncState.calendar_id == cid))


def rows(cal_id):
    with SessionLocal() as s:
        return {r.google_event_id: r for r in s.scalars(select(CalendarEvent).where(CalendarEvent.calendar_id == cal_id))}


def test_full_sync_mirrors_events_including_recurring_instances_and_all_day(cal_id):
    g = FakeGoogle(
        ev("a"), ev("b"),  # typed by hand
        ev("rec_20261105T150000Z", recurringEventId="rec"), ev("rec_20261112T150000Z", recurringEventId="rec"),
        {"id": "day", "status": "confirmed", "start": {"date": "2026-11-08"}, "end": {"date": "2026-11-09"},
         "transparency": "transparent"},
    )
    r = cs.sync_pass(g, cal_id, NOW)
    got = rows(cal_id)
    assert r.full and r.changed == 5 and set(got) == {"a", "b", "rec_20261105T150000Z", "rec_20261112T150000Z", "day"}
    assert got["a"].starts_at == datetime(2026, 11, 5, 15, tzinfo=timezone.utc) and got["a"].business_id is None
    assert got["day"].transparency == "transparent" and got["day"].ends_at - got["day"].starts_at == timedelta(days=1)
    window = g.calls[0]
    assert window["time_min"] == NOW - timedelta(days=7) and window["time_max"] == NOW + timedelta(days=90)
    with SessionLocal() as s:
        assert s.get(CalendarSyncState, cal_id).sync_token == f"tok{g.version}"


def test_second_pass_is_incremental_and_applies_move_and_delete(cal_id):
    g = FakeGoogle(ev("a"), ev("b"))
    cs.sync_pass(g, cal_id, NOW)
    g.change(ev("a", start="2026-11-05T17:00:00Z", end="2026-11-05T18:00:00Z", summary="Moved"))
    g.change(ev("c"))
    g.remove("b")
    r = cs.sync_pass(g, cal_id, NOW + timedelta(seconds=60))
    got = rows(cal_id)
    assert not r.full and g.calls[-1]["sync_token"] == "tok1"
    assert got["a"].starts_at.hour == 17 and got["a"].summary == "Moved"
    assert got["b"].status == "cancelled" and got["c"].status == "confirmed"
    assert cs.sync_pass(g, cal_id, NOW).changed == 0  # nothing new


def test_410_drops_the_token_and_full_sync_finds_events_deleted_in_the_gap(cal_id):
    g = FakeGoogle(ev("a"), ev("b"))
    cs.sync_pass(g, cal_id, NOW)
    g.expired.add("tok1")
    g.remove("b", tombstone=False)  # deletion Google will not report any more
    r = cs.sync_pass(g, cal_id, NOW)
    assert r.full and rows(cal_id)["b"].status == "cancelled" and rows(cal_id)["a"].status == "confirmed"


def test_deleted_event_in_a_full_listing_without_times_creates_no_row(cal_id):
    g = FakeGoogle({"id": "ghost", "status": "cancelled"}, ev("a"))
    cs.sync_pass(g, cal_id, NOW)
    assert set(rows(cal_id)) == {"a"}


def test_business_id_is_read_from_private_properties_and_unknown_ids_are_dropped(cal_id):
    bid = uuid.uuid4()
    with tenant_session(bid) as s:
        s.add(Business(id=bid, name="Spa"))
    try:
        g = FakeGoogle(
            ev("mine", extendedProperties={"private": {"business_id": str(bid)}}),
            ev("stranger", extendedProperties={"private": {"business_id": str(uuid.uuid4())}}),
            ev("junk", extendedProperties={"private": {"business_id": "not-a-uuid"}}),
        )
        cs.sync_pass(g, cal_id, NOW)
        got = rows(cal_id)
        assert got["mine"].business_id == bid and got["stranger"].business_id is None and got["junk"].business_id is None
    finally:
        with SessionLocal() as s, s.begin():
            s.execute(delete(CalendarEvent).where(CalendarEvent.calendar_id == cal_id))
            s.execute(delete(Business).where(Business.id == bid))


@pytest.fixture
def booking_for():
    made = []

    def make(event_id, bid):
        with tenant_session(bid) as s:
            s.add(Business(id=bid, name="Spa"))
            s.flush()
            b = Booking(business_id=bid, type="appointment", name="Ann", phone="+15550100100", external_ref=event_id,
                        starts_at=datetime(2026, 11, 5, 15, tzinfo=timezone.utc),
                        ends_at=datetime(2026, 11, 5, 16, tzinfo=timezone.utc))
            s.add(b)
            s.flush()
            made.append(bid)
            return b.id

    yield make
    with SessionLocal() as s, s.begin():
        s.execute(delete(Business).where(Business.id.in_(made)))


def booking(bid):
    with tenant_session(bid) as s:
        return s.scalars(select(Booking).where(Booking.business_id == bid)).one()


def test_event_deleted_in_google_cancels_its_booking_and_a_moved_one_moves_it(cal_id, booking_for):
    bid = uuid.uuid4()
    booking_for("x1", bid)
    g = FakeGoogle(ev("x1"))
    cs.sync_pass(g, cal_id, NOW)
    assert booking(bid).status == "confirmed"

    g.change(ev("x1", start="2026-11-06T09:00:00Z", end="2026-11-06T10:30:00Z"))
    r = cs.sync_pass(g, cal_id, NOW)
    b = booking(bid)
    assert r.bookings_moved == 1 and (b.starts_at.day, b.starts_at.hour, b.ends_at.minute) == (6, 9, 30)

    g.remove("x1")
    r = cs.sync_pass(g, cal_id, NOW)
    b = booking(bid)
    assert r.bookings_cancelled == 1 and b.status == "cancelled" and b.cancelled_at == NOW


def test_google_failure_raises_records_the_error_and_a_later_pass_clears_it(cal_id):
    g = FakeGoogle(ev("a"))
    g.fail = True
    with pytest.raises(CalendarError):
        cs.sync_pass(g, cal_id, NOW)
    with SessionLocal() as s:
        assert s.get(CalendarSyncState, cal_id).last_error == "unreachable"
    g.fail = False
    cs.sync_pass(g, cal_id, NOW)
    with SessionLocal() as s:
        assert s.get(CalendarSyncState, cal_id).last_error is None


def test_post_sync_hooks_run_after_a_pass_and_a_failing_hook_does_not_break_it(cal_id, monkeypatch):
    seen = []
    monkeypatch.setattr(cs, "post_sync_hooks", [lambda c: seen.append(c), lambda c: 1 / 0])
    cs.sync_pass(FakeGoogle(ev("a")), cal_id, NOW)
    assert seen == [cal_id]


def test_sync_now_uses_the_given_client(cal_id):
    assert cs.sync_now(cal_id, FakeGoogle(ev("a"))).full


# ---- wire format of the new client calls --------------------------------------------------

from tests.test_google_calendar import _Resp, _client  # noqa: E402


def test_client_list_events_params_pages_and_410():
    c, http = _client(_Resp(200, {"items": [{"id": "a"}], "nextPageToken": "p2"}),
                      _Resp(200, {"items": [{"id": "b"}], "nextSyncToken": "tok"}), _Resp(410), _Resp(500))
    lo = datetime(2026, 11, 1, tzinfo=timezone.utc)
    page = c.list_events("c@x", time_min=lo, time_max=lo + timedelta(days=90))
    params = http.requests[0][2]["params"]
    assert page.next_page_token == "p2" and params["singleEvents"] == "true" and params["showDeleted"] == "true"
    assert params["eventTypes"] == "default" and params["timeMin"].startswith("2026-11-01")
    last = c.list_events("c@x", time_min=lo, page_token="p2")
    assert last.next_sync_token == "tok" and http.requests[1][2]["params"]["pageToken"] == "p2"
    with pytest.raises(SyncTokenExpired):
        c.list_events("c@x", sync_token="old")
    assert "timeMin" not in http.requests[2][2]["params"] and http.requests[2][2]["params"]["syncToken"] == "old"
    with pytest.raises(CalendarError):
        c.list_events("c@x", sync_token="x")


def test_client_get_patch_insert_status_codes():
    c, http = _client(_Resp(200, {"id": "a"}), _Resp(404), _Resp(200, {"etag": '"2"'}), _Resp(412), _Resp(409))
    assert c.get_event("c@x", "a") == {"id": "a"} and c.get_event("c@x", "gone") is None
    assert c.patch_event("c@x", "a", {"summary": "s"}, etag='"1"') == '"2"'
    assert http.requests[2][0] == "PATCH" and http.requests[2][2]["headers"] == {"If-Match": '"1"'}
    with pytest.raises(PreconditionFailed):
        c.patch_event("c@x", "a", {}, etag='"1"')
    with pytest.raises(EventExists):
        c.insert_event("c@x", {"id": "mine"})


def test_configured_calendars_skips_businesses_without_a_calendar_id(monkeypatch):
    monkeypatch.setenv("GOOGLE_CALENDAR_ID", "env-cal@example.com")
    cs.get_settings.cache_clear()
    try:
        ids = cs.configured_calendars()
    finally:
        cs.get_settings.cache_clear()
    assert "env-cal@example.com" in ids and "None" not in ids
