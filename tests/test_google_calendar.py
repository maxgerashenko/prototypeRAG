"""Google Calendar booking backend against a fake Google client (DEC-48, #105).

Rules/availability/create/cancel are checked with `FakeCalendar`; the wire format of
`GoogleCalendarClient` is checked with a fake HTTP session. Needs Postgres only. The live
check with a real service account is on the "Test on Mac" list (plan/03-voice-channel.md)."""

import threading
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import delete, select

from app.actions.booking import rules as R
from app.actions.booking.google_calendar import GoogleCalendarBackend
from app.actions.booking.google_client import CalendarError, GoogleCalendarClient
from app.actions.booking.internal import BookingRequest
from app.db import tenant_session
from app.db.models import Booking, BookingRule, Business, Conversation

NOW = datetime(2026, 11, 1, 12, 0, tzinfo=timezone.utc)
NY = "America/New_York"
OPEN_9_21 = {d: [["09:00", "21:00"]] for d in R.DAYS if d != "mon"}
CAL = "owner@example.com"


class FakeCalendar:
    def __init__(self):
        self.events: dict[str, dict] = {}
        self.external_busy: list[tuple[datetime, datetime]] = []  # owner's own entries
        self.fail: set[str] = set()
        self.calls: list[str] = []

    def _check(self, op):
        self.calls.append(op)
        if op in self.fail:
            raise CalendarError(f"{op} failed")

    def busy(self, calendar_id, start, end):
        self._check("busy")
        own = [(datetime.fromisoformat(e["start"]["dateTime"]), datetime.fromisoformat(e["end"]["dateTime"]))
               for e in self.events.values()]
        return [(a, b) for a, b in own + self.external_busy if a < end and b > start]

    def insert_event(self, calendar_id, body):
        self._check("insert")
        assert calendar_id == CAL
        eid = f"ev{len(self.events) + 1}"
        self.events[eid] = body
        return eid

    def delete_event(self, calendar_id, event_id):
        self._check("delete")
        self.events.pop(event_id, None)


@pytest.fixture
def cal():
    return FakeCalendar()


@pytest.fixture
def backend(cal):
    return GoogleCalendarBackend(cal, CAL)


@pytest.fixture
def business_id():
    bid = uuid.uuid4()
    with tenant_session(bid) as s:
        s.add(Business(id=bid, name="Spa", timezone=NY, settings={"enabled_tools": ["check_availability", "create_booking", "cancel_booking"]}))
        s.flush()
        s.add(BookingRule(business_id=bid, slot_minutes=60, capacity=1, max_party_size=6, hours=OPEN_9_21,
                          service_durations={"massage": 90}))
    yield bid
    with tenant_session(bid) as s:
        s.execute(delete(Business).where(Business.id == bid))


def _req(**kw):
    base = dict(day="2026-11-05", at="14:00", party_size=2, name="Anna", phone="+15550100100")
    return BookingRequest(**{**base, **kw})


def _local(day, at):
    return R.to_utc(R.local_start(R.Rules(timezone=NY), day, at))


def _row(bid):
    with tenant_session(bid) as s:
        return s.scalars(select(Booking).where(Booking.business_id == bid)).one()


def test_create_inserts_event_and_stores_its_id(backend, cal, business_id):
    cid = uuid.uuid4()
    with tenant_session(business_id) as s:
        s.add(Conversation(id=cid, business_id=business_id, channel="chat"))
    res = backend.create(business_id, _req(conversation_id=cid, notes="window seat"), NOW)
    assert res.ok and res.when == "Thu 05 Nov, 14:00" and res.reference
    (eid, event), = cal.events.items()
    assert _row(business_id).external_ref == eid and _row(business_id).status == "confirmed"
    assert event["start"] == {"dateTime": "2026-11-05T14:00:00-05:00", "timeZone": NY}
    assert event["end"]["dateTime"] == "2026-11-05T15:00:00-05:00"
    assert "Anna" in event["summary"] and "table for 2" in event["summary"]
    for want in ("Name: Anna", "Phone: +15550100100", "People: 2", "Notes: window seat", f"Reference: {res.reference}"):
        assert want in event["description"]
    assert event["extendedProperties"]["private"] == {
        "business_id": str(business_id), "booking_id": str(res.booking_id), "conversation_id": str(cid)}
    assert "attendees" not in event  # a service account can't invite without delegation


def test_service_makes_an_appointment_with_its_duration(backend, cal, business_id):
    assert backend.create(business_id, _req(service="Massage", party_size=None), NOW).ok
    (event,) = cal.events.values()
    assert event["end"]["dateTime"] == "2026-11-05T15:30:00-05:00" and event["summary"] == "Anna - Massage"
    assert _row(business_id).type == "appointment"


def test_owners_own_calendar_entry_blocks_the_slot_and_gives_alternatives(backend, cal, business_id):
    cal.external_busy = [(_local("2026-11-05", "14:30"), _local("2026-11-05", "15:30"))]
    av = backend.availability(business_id, _req(), NOW)
    assert not av.ok and "fully booked" in av.reason and "16:00" in av.alternatives and "14:00" not in av.alternatives
    res = backend.create(business_id, _req(), NOW)
    assert not res.ok and cal.events == {}  # no event inserted, no row
    with tenant_session(business_id) as s:
        assert s.scalars(select(Booking)).all() == []


def test_free_busy_is_re_checked_right_before_insert(backend, cal, business_id):
    """availability said yes, then the owner blocked the slot: create must not insert."""
    assert backend.availability(business_id, _req(), NOW).ok
    cal.external_busy = [(_local("2026-11-05", "14:00"), _local("2026-11-05", "15:00"))]
    cal.calls.clear()
    assert not backend.create(business_id, _req(), NOW).ok
    assert cal.calls == ["busy"]


def test_rules_still_apply(backend, business_id):
    assert "closed" in backend.availability(business_id, _req(day="2026-11-02"), NOW).reason
    assert "largest party" in backend.availability(business_id, _req(party_size=9), NOW).reason
    assert not backend.availability(business_id, _req(day="05.11.2026"), NOW).ok


def test_second_booking_in_the_slot_is_refused_even_if_calendar_is_empty(backend, cal, business_id):
    assert backend.create(business_id, _req(), NOW).ok
    cal.events.clear()  # e.g. someone deleted the event by hand: the bookings row still holds the slot
    res = backend.create(business_id, _req(name="Ben"), NOW)
    assert not res.ok and "fully booked" in res.message


def test_capacity_above_one_counts_rows_not_merged_busy_blocks(business_id, cal):
    backend = GoogleCalendarBackend(cal, CAL)
    with tenant_session(business_id) as s:
        s.get(BookingRule, business_id).capacity = 2
    assert backend.create(business_id, _req(), NOW).ok
    assert backend.create(business_id, _req(name="Ben"), NOW).ok
    assert "fully booked" in backend.create(business_id, _req(name="Cy"), NOW).message


def test_concurrent_creates_for_one_slot_book_exactly_one(backend, cal, business_id):
    results = []
    threads = [threading.Thread(target=lambda: results.append(backend.create(business_id, _req(), NOW))) for _ in range(4)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert sum(r.ok for r in results) == 1 and len(cal.events) == 1


def test_google_failure_books_nothing(backend, cal, business_id):
    for op in ("busy", "insert"):
        cal.fail = {op}
        res = backend.create(business_id, _req(), NOW)
        assert not res.ok and "calendar" in res.message
    cal.fail = {"busy"}
    av = backend.availability(business_id, _req(), NOW)
    assert not av.ok and "calendar" in av.reason
    with tenant_session(business_id) as s:
        assert s.scalars(select(Booking)).all() == []


def test_event_is_deleted_again_if_the_row_cannot_be_written(backend, cal, business_id):
    # a conversation id that doesn't exist violates the composite FK at flush
    with pytest.raises(Exception):
        backend.create(business_id, _req(conversation_id=uuid.uuid4()), NOW)
    assert cal.events == {}


def test_missing_calendar_id_is_reported_not_booked(cal, business_id):
    res = GoogleCalendarBackend(cal, "").create(business_id, _req(), NOW)
    assert not res.ok and cal.calls == []


def test_per_business_calendar_id_overrides_the_default(cal, business_id):
    with tenant_session(business_id) as s:
        s.get(Business, business_id).settings = {"google_calendar_id": CAL}
    assert GoogleCalendarBackend(cal, "").create(business_id, _req(), NOW).ok


def test_cancel_deletes_the_event_and_frees_the_slot(backend, cal, business_id):
    ref = backend.create(business_id, _req(), NOW).reference
    assert "No upcoming" in backend.cancel(business_id, ref, "+15559999999", NOW)  # someone else's phone
    assert len(cal.events) == 1
    assert backend.cancel(business_id, ref, "+15550100100", NOW).startswith("Cancelled")
    assert cal.events == {} and _row(business_id).status == "cancelled"
    assert backend.create(business_id, _req(name="Ben"), NOW).ok


def test_cancel_keeps_the_booking_if_google_cannot_delete(backend, cal, business_id):
    ref = backend.create(business_id, _req(), NOW).reference
    cal.fail = {"delete"}
    assert backend.cancel(business_id, ref, "+15550100100", NOW).startswith("Error")
    assert _row(business_id).status == "confirmed" and len(cal.events) == 1


# ---- wire format of the real client (fake HTTP session) --------------------------------

class _Resp:
    def __init__(self, status=200, body=None):
        self.status_code, self._body, self.text = status, body or {}, str(body)

    def json(self):
        return self._body


class _Http:
    def __init__(self, *responses):
        self.responses, self.requests = list(responses), []

    def request(self, method, url, **kw):
        self.requests.append((method, url, kw))
        return self.responses.pop(0)


def _client(*responses):
    c = GoogleCalendarClient("key.json")
    c._session = _Http(*responses)
    return c, c._session


def test_client_busy_parses_free_busy_response():
    c, http = _client(_Resp(200, {"calendars": {CAL: {"busy": [{"start": "2026-11-05T19:00:00Z", "end": "2026-11-05T20:00:00Z"}]}}}))
    start = datetime(2026, 11, 5, tzinfo=timezone.utc)
    assert c.busy(CAL, start, start + timedelta(days=1)) == [
        (datetime(2026, 11, 5, 19, tzinfo=timezone.utc), datetime(2026, 11, 5, 20, tzinfo=timezone.utc))]
    method, url, kw = http.requests[0]
    assert (method, url.endswith("/freeBusy"), kw["json"]["items"], kw["timeout"]) == ("POST", True, [{"id": CAL}], 10)


def test_client_errors_become_calendar_error():
    start = datetime(2026, 11, 5, tzinfo=timezone.utc)
    with pytest.raises(CalendarError):
        _client(_Resp(403, {"error": "x"}))[0].busy(CAL, start, start)
    with pytest.raises(CalendarError, match="notFound"):  # calendar not shared with the service account
        _client(_Resp(200, {"calendars": {CAL: {"errors": [{"reason": "notFound"}]}}}))[0].busy(CAL, start, start)
    with pytest.raises(CalendarError):
        _client(_Resp(500))[0].insert_event(CAL, {})
    with pytest.raises(CalendarError):
        GoogleCalendarClient("").busy(CAL, start, start)  # no key file configured


def test_client_insert_and_delete():
    c, http = _client(_Resp(200, {"id": "abc"}), _Resp(204), _Resp(410))
    assert c.insert_event("a@b.c", {"summary": "x"}) == "abc"
    c.delete_event("a@b.c", "abc")
    c.delete_event("a@b.c", "gone")  # already deleted: fine
    assert http.requests[0][1].endswith("/calendars/a%40b.c/events")
    assert http.requests[1][:2] == ("DELETE", http.requests[1][1]) and http.requests[1][1].endswith("/events/abc")


def test_booking_backend_setting_picks_the_backend(monkeypatch):
    from app.actions.booking import tools
    from app.actions.booking.internal import InternalBackend
    from app.config import get_settings

    for value, kind in (("internal", InternalBackend), ("google_calendar", GoogleCalendarBackend)):
        monkeypatch.setenv("BOOKING_BACKEND", value)
        get_settings.cache_clear()
        try:
            assert isinstance(tools.make_backend(), kind)
        finally:
            monkeypatch.delenv("BOOKING_BACKEND")
            get_settings.cache_clear()
