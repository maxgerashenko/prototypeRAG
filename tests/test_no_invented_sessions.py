"""Only real sessions can be offered or read back, and the bot can never create one.

Postgres only (a business with every optional tool on); no LLM, no Google. The calendar backend is
the Postgres one, so `sync_before_booking` is a no-op here."""

import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import delete, func, select

from app.actions import executor
from app.actions.booking import sessions as sessions_
from app.actions.booking import tools
from app.actions.registry import _REGISTRY, ActionContext, tools_for
from app.db import tenant_session
from app.db.models import ActionLog, Business, CalendarEvent, GroupSession
from app.voice import tools as voice_tools  # noqa: F401  (registers every tool)

ALL = ["list_sessions", "join_session", "check_availability", "create_booking", "cancel_booking"]
ROWS = [{"ref": "AAAA1111", "name": "Sauna German", "when": "Mon 02 Nov, 3:00 PM to 4:00 PM",
         "booked": 1, "max_attendees": 8, "full": False}]


@pytest.fixture
def ctx():
    bid = uuid.uuid4()
    with tenant_session(bid) as s:
        s.add(Business(id=bid, name="Spa", timezone="America/New_York", settings={"enabled_tools": ALL}))
    yield ActionContext(business_id=bid, conversation_id=None)
    with tenant_session(bid) as s:
        s.execute(delete(Business).where(Business.id == bid))


@pytest.fixture
def open_rows(monkeypatch):
    monkeypatch.setattr(sessions_, "list_open", lambda business_id, day=None, now=None, limit=10, seats=True: ROWS)


def _logged(ctx, tool):
    with tenant_session(ctx.business_id) as s:
        return s.scalar(select(func.count()).select_from(ActionLog).where(
            ActionLog.business_id == ctx.business_id, ActionLog.tool == tool))


def test_unknown_session_is_refused_before_the_read_back(ctx, open_rows):
    for ref in ("Steam Bath", "the Steam Bath session at 2 PM", "deadbeef", "deadbe", "dead-beef-01", "STEAMBATH"):
        out = executor.execute(ctx, "join_session", {"session": ref, "first_name": "Tom", "last_name": "Baker"})
        assert out.startswith('Error: not booked. Say exactly: "There is no session '), out
        assert "on the schedule. We have Sauna German on Monday 2 November at 3:00 PM." in out and "Not done" not in out
        assert "AAAA1111" not in out and "to 4:00" not in out  # no codes, no end times
    code = executor.execute(ctx, "join_session", {"session": "deadbeef", "first_name": "Tom", "last_name": "Baker"})
    assert "no session with that code on the schedule" in code and "deadbeef" not in code


def test_unknown_session_with_nothing_open_says_so(ctx, monkeypatch):
    monkeypatch.setattr(sessions_, "list_open", lambda *a, **k: [])
    out = executor.execute(ctx, "join_session", {"session": "Steam Bath", "first_name": "Tom", "last_name": "Baker"})
    assert "There are no open sessions right now." in out


def test_real_session_with_a_real_name_still_asks_for_the_read_back(ctx, monkeypatch):
    monkeypatch.setattr(sessions_, "find_open", lambda business_id, ref, now=None: ROWS[0])
    out = executor.execute(ctx, "join_session", {"session": "AAAA1111", "first_name": "Tom", "last_name": "Baker"})
    assert out.startswith("Not done")


@pytest.mark.parametrize("first, last", [("Caller", "Unknown"), ("John", "Doe"), ("", "")])
def test_placeholder_name_is_refused_before_the_read_back(ctx, monkeypatch, first, last):
    monkeypatch.setattr(sessions_, "find_open", lambda business_id, ref, now=None: ROWS[0])
    out = executor.execute(ctx, "join_session", {"session": "AAAA1111", "first_name": first, "last_name": last})
    assert out == tools.NAME_NEEDED


def test_create_booking_placeholder_is_refused_before_the_read_back(ctx):
    out = executor.execute(ctx, "create_booking", {
        "date": "2030-01-01", "time": "19:00", "first_name": "Caller", "last_name": "Unknown", "phone": "+15550100100"})
    assert out == tools.NAME_NEEDED


def test_optional_read_only_tools_are_logged_but_search_is_not(ctx, open_rows):
    executor.execute(ctx, "list_sessions", {})
    assert _logged(ctx, "list_sessions") == 1
    executor.execute(ctx, "search_business_info", {"query": "hours"})
    assert _logged(ctx, "search_business_info") == 0


def _count(model):
    with tenant_session(uuid.uuid4()) as s:
        return s.scalar(select(func.count()).select_from(model))


def test_no_tool_offered_to_the_bot_creates_a_session(ctx):
    offered = {t["function"]["name"] for t in tools_for(ctx.business_id) if not t["function"]["name"].startswith("test_")}
    real = {n for n in _REGISTRY if not n.startswith("test_")}  # other test files register fakes
    # a new tool must be added here on purpose, after checking it cannot create a session or calendar event
    assert offered == real == {
        "search_business_info", "take_message", "transfer_to_human",
        "list_sessions", "join_session", "check_availability", "create_booking", "cancel_booking",
    }
    for session_ref in ("Steam Bath", "deadbeef", "deadbe", "dead-beef-01"):
        for name in sorted(offered):
            before = (_count(GroupSession), _count(CalendarEvent))
            out = executor.execute(ctx, name, {
                "session": session_ref, "first_name": "Tom", "last_name": "Baker", "date": "not-a-date", "confirmed": True,
            })
            assert (_count(GroupSession), _count(CalendarEvent)) == before, name
            assert out.startswith("Error"), (name, out)


def test_voice_prompt_lists_the_open_sessions_with_their_codes(ctx, monkeypatch):
    from app.rag import answer
    from tests.test_enabled_tools import _start

    monkeypatch.setattr(answer, "list_open", lambda business_id, *a, **k: ROWS)
    prompt = _start(ctx.business_id, monkeypatch)._system_prompt
    assert "Sauna German" in prompt and "AAAA1111" in prompt


def test_voice_prompt_has_no_session_list_without_join_session(monkeypatch):
    from app.rag import answer

    monkeypatch.setattr(answer, "list_open", lambda *a, **k: ROWS)
    assert answer.open_sessions_note(uuid.uuid4(), frozenset({"list_sessions"})) == ""


def test_create_booking_blank_name_is_refused_before_the_read_back(ctx):
    out = executor.execute(ctx, "create_booking", {
        "date": "2030-01-01", "time": "19:00", "first_name": "  ", "phone": "+15550100100"})
    assert out == tools.NAME_NEEDED


def test_find_open_finds_not_ended_sessions_by_code(ctx, monkeypatch):
    monkeypatch.setattr(sessions_.G, "get_client", lambda: None)  # never the real Google Calendar, whatever .env says
    public = sessions_.create_session(ctx.business_id, "2030-01-01", 10, "Public", 4)
    private = sessions_.create_session(ctx.business_id, "2030-01-01", 12, "Private", 4, private=True)
    try:
        _find_open_checks(ctx, public, private)
    finally:  # create_session may write to the demo business, not ctx's: remove exactly these two rows
        with tenant_session(ctx.business_id) as s:
            s.execute(delete(GroupSession).where(GroupSession.id.in_([uuid.UUID(public["id"]), uuid.UUID(private["id"])])))


def _find_open_checks(ctx, public, private):
    from datetime import datetime, timedelta, timezone

    ref = sessions_.session_ref(uuid.UUID(public["id"]))
    found = sessions_.find_open(ctx.business_id, ref)
    assert found and found["name"] == "Public" and found["ref"] == ref and "AM" in found["when"]
    pf = sessions_.find_open(ctx.business_id, sessions_.session_ref(uuid.UUID(private["id"])))
    assert pf and pf["name"] == "Private"  # a private session is found by its code (never listed)
    later = datetime(2030, 1, 1, tzinfo=timezone.utc) + timedelta(days=2)
    assert sessions_.find_open(ctx.business_id, ref, now=later) is None  # ended
    assert sessions_.find_open(ctx.business_id, "zz") is None


@pytest.mark.parametrize("booked, max_attendees, full, expected", [
    (0, 8, False, "all 8 seats free"), (0, 1, False, "the 1 seat is free"), (3, 8, False, "5 seats left"),
    (7, 8, False, "1 seat left"), (8, 8, True, "FULL"),
])
def test_seats_text(booked, max_attendees, full, expected):
    assert tools.seats_text({"booked": booked, "max_attendees": max_attendees, "full": full}) == expected


# --- a service the business does not offer is not booked as an appointment ----------------------------------

def _bookings(ctx):
    from app.db.models import Booking

    with tenant_session(ctx.business_id) as s:
        return s.scalar(select(func.count()).select_from(Booking).where(Booking.business_id == ctx.business_id))


def _rules(ctx, durations):
    from app.db.models import BookingRule

    with tenant_session(ctx.business_id) as s:
        s.add(BookingRule(business_id=ctx.business_id, service_durations=durations))


STEAM = {"date": "2030-01-01", "time": "14:00", "service": "Steam Bath", "first_name": "Tom", "last_name": "Baker",
         "phone": "+15550100100"}


def test_unknown_service_is_refused_when_the_business_has_sessions(ctx, open_rows):
    for tool in ("check_availability", "create_booking"):
        out = executor.execute(ctx, tool, {**STEAM, "confirmed": True})
        assert out.startswith('Error: not booked. Say exactly: "We don\'t offer Steam Bath. We have Sauna German '), out
        assert "AAAA1111" not in out and "to 4:00" not in out
    assert _bookings(ctx) == 0


def test_unknown_service_names_the_configured_services_too(ctx, open_rows):
    _rules(ctx, {"massage": 60, "facial": 45})
    out = executor.execute(ctx, "create_booking", {**STEAM, "confirmed": True})
    assert "We don't offer Steam Bath. We offer facial, massage. We have Sauna German " in out
    assert _bookings(ctx) == 0


def test_known_service_is_not_refused(ctx, open_rows):
    _rules(ctx, {"massage": 60})
    for name in ("massage", "Massage", "Swedish massage"):
        assert tools._unknown_service(ctx.business_id, name) is None
    out = executor.execute(ctx, "check_availability", {**STEAM, "service": "Massage"})
    assert "don't offer" not in out


def test_any_service_is_fine_for_a_business_without_services_or_sessions(ctx, monkeypatch):
    monkeypatch.setattr(sessions_, "list_open", lambda *a, **k: [])
    assert tools._unknown_service(ctx.business_id, "Steam Bath") is None
    assert tools._unknown_service(ctx.business_id, None) is None


def test_unknown_service_ignores_sessions_without_join_session(open_rows):
    bid = uuid.uuid4()
    with tenant_session(bid) as s:
        s.add(Business(id=bid, name="Salon", timezone="America/New_York", settings={"enabled_tools": ["create_booking"]}))
    try:
        # another business's sessions (demo rule) must neither refuse the service nor be named to this business
        assert tools._unknown_service(bid, "Steam Bath") is None
    finally:
        with tenant_session(bid) as s:
            s.execute(delete(Business).where(Business.id == bid))


def test_the_service_is_checked_once_per_create_booking(ctx, open_rows, monkeypatch):
    calls = []
    real = tools._unknown_service
    monkeypatch.setattr(tools, "_unknown_service", lambda *a: calls.append(a) or real(*a))
    executor.execute(ctx, "create_booking", {**STEAM, "service": "Massage", "confirmed": True})
    assert len(calls) == 1


def test_name_lookups_skip_the_seat_counts(ctx, monkeypatch):
    asked = []
    monkeypatch.setattr(sessions_, "list_open", lambda business_id, day=None, now=None, limit=10, seats=True:
                        asked.append(seats) or ROWS)
    tools.resolve_session(ctx.business_id, "Steam Bath")
    tools._join_check(ctx, {"session": "DEADBEEF"})
    assert asked == [False, False]


def test_light_list_open_has_no_seat_count(ctx, monkeypatch):
    counted = []
    monkeypatch.setattr(sessions_.G, "_booked", lambda *a: counted.append(a) or 0)
    with tenant_session(ctx.business_id) as s:
        s.add(GroupSession(business_id=ctx.business_id, name="Light", starts_at=datetime(2030, 1, 1, 15, tzinfo=timezone.utc),
                           ends_at=datetime(2030, 1, 1, 16, tzinfo=timezone.utc), max_attendees=4, status="active", private=False))
    now = datetime(2029, 12, 31, tzinfo=timezone.utc)
    light = sessions_.list_open(ctx.business_id, now=now, limit=500, seats=False)  # other sessions in the dev DB count too
    assert "Light" in {r["name"] for r in light} and counted == []
    rows = sessions_.list_open(ctx.business_id, now=now, limit=500)
    assert rows and len(counted) == len(rows)  # one count per row, only with seats


def test_quotes_in_names_cannot_end_the_say_exactly_sentence():
    import re

    rows = [{**ROWS[0], "name": 'Sauna "German" Night'}]
    out = tools._no_such_session(uuid.uuid4(), 'the "Steam" Bath', rows)
    sentence = re.search(r'Say exactly: "([^"]+)"', out)[1]
    assert sentence.startswith("There is no session called 'the Steam Bath' on the schedule. We have Sauna German Night ")
    assert sentence.endswith(".") and out.endswith("technical problem.")
    assert tools._quotable('Steam "Bath"') == "Steam Bath"
