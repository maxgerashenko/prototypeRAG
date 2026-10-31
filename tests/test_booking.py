"""Internal booking backend and rules (plan/04-actions.md, #52).

Rules are pure and need nothing; the backend/tool tests need Postgres only ."""

import threading
import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import delete, select

from app.actions import executor
from app.actions.booking import rules as R
from app.actions.booking import tools as booking_tools  # registers the tools
from app.actions.booking.internal import BookingRequest, InternalBackend
from app.actions.registry import ActionContext
from app.db import tenant_session
from app.db.models import Booking, BookingRule, Business, Conversation

NOW = datetime(2026, 11, 1, 12, 0, tzinfo=timezone.utc)
OPEN_9_21 = {d: [["09:00", "21:00"]] for d in R.DAYS if d != "mon"}  # closed Mondays
NY = "America/New_York"


# ---- pure rules ----------------------------------------------------------------------

def _rules(**kw):
    return R.Rules(timezone=NY, hours=OPEN_9_21, **kw)


def _slot(rules, day, at, minutes=60):
    start = R.local_start(rules, day, at)
    from datetime import timedelta
    return start, start + timedelta(minutes=minutes)


def test_local_start_uses_business_timezone_and_rejects_garbage():
    start = R.local_start(_rules(), "2026-11-05", "19:30")
    assert R.to_utc(start).hour == 0 and R.to_utc(start).day == 6  # 19:30 EST = 00:30 UTC next day
    for bad in (("05/11/2026", "19:30"), ("2026-11-05", "7pm"), ("2026-02-31", "10:00")):
        with pytest.raises(ValueError):
            R.local_start(_rules(), *bad)


def test_violation_rules():
    r = _rules(capacity=1, max_party_size=6, lead_time_minutes=60)
    ok = _slot(r, "2026-11-05", "14:00")
    assert R.violation(r, *ok, 2, NOW, 0) is None
    assert "closed" in R.violation(r, *_slot(r, "2026-11-02", "14:00"), 2, NOW, 0)  # Monday
    assert "opening hours" in R.violation(r, *_slot(r, "2026-11-05", "08:00"), 2, NOW, 0)
    assert "opening hours" in R.violation(r, *_slot(r, "2026-11-05", "20:30"), 2, NOW, 0)  # ends 21:30
    assert "largest party" in R.violation(r, *ok, 7, NOW, 0)
    assert "fully booked" in R.violation(r, *ok, 2, NOW, 1)
    assert "past or too soon" in R.violation(r, *_slot(r, "2026-11-01", "07:30"), 2, NOW, 0)  # 07:30 EDT/EST < now+1h
    assert R.violation(_rules(capacity=2), *ok, 2, NOW, 1) is None


def test_no_hours_configured_skips_hours_rule_and_gives_no_alternatives():
    r = R.Rules(timezone=NY)
    s, e = _slot(r, "2026-11-02", "03:00")
    assert R.violation(r, s, e, 1, NOW, 0) is None
    assert R.candidate_starts(r, s) == []


def test_candidates_are_on_the_opening_grid_nearest_first():
    r = _rules(slot_minutes=60)
    start = R.local_start(r, "2026-11-05", "19:00")
    c = [x.strftime("%H:%M") for x in R.candidate_starts(r, start)]
    assert c[:3] == ["18:00", "20:00", "17:00"] and "19:00" not in c


def test_service_duration_falls_back_to_slot_length():
    r = R.Rules(slot_minutes=45, service_durations={"massage": 90})
    assert r.duration("Massage") == 90 and r.duration("sauna") == 45 and r.duration(None) == 45


# ---- backend + tools (Postgres) -------------------------------------------------------

@pytest.fixture
def business_id():
    bid = uuid.uuid4()
    with tenant_session(bid) as s:
        s.add(Business(id=bid, name="Spa", timezone=NY))
        s.flush()
        s.add(BookingRule(business_id=bid, slot_minutes=60, capacity=1, max_party_size=6, hours=OPEN_9_21,
                          service_durations={"massage": 90}))
    yield bid
    with tenant_session(bid) as s:
        s.execute(delete(Business).where(Business.id == bid))


def _req(**kw):
    base = dict(day="2026-11-05", at="14:00", party_size=2, name="Anna", phone="+15550100100")
    return BookingRequest(**{**base, **kw})


def test_create_stores_utc_and_blocks_a_second_booking_in_the_slot(business_id):
    b = InternalBackend()
    first = b.create(business_id, _req(), NOW)
    assert first.ok and len(first.reference) == 8 and first.when == "Thu 05 Nov, 14:00"
    with tenant_session(business_id) as s:
        row = s.scalar(select(Booking))
        assert row.starts_at == datetime(2026, 11, 5, 19, 0, tzinfo=timezone.utc) and row.type == "table"
    second = b.create(business_id, _req(name="Bob", at="14:30"), NOW)
    assert not second.ok and "fully booked" in second.message
    assert second.alternatives[:2] == ["15:00", "13:00"] or "15:00" in second.alternatives
    assert b.availability(business_id, _req(at="15:00"), NOW).ok  # back-to-back is fine


def test_concurrent_creates_for_one_slot_book_exactly_one(business_id):
    b, results = InternalBackend(), []
    threads = [threading.Thread(target=lambda n=n: results.append(b.create(business_id, _req(name=f"G{n}"), NOW)))
               for n in range(5)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert sum(r.ok for r in results) == 1


def test_service_makes_an_appointment_with_its_duration(business_id):
    b = InternalBackend()
    assert b.create(business_id, _req(service="Massage", party_size=None), NOW).ok
    with tenant_session(business_id) as s:
        row = s.scalar(select(Booking))
        assert row.type == "appointment" and (row.ends_at - row.starts_at).total_seconds() == 90 * 60
    assert not b.availability(business_id, _req(at="15:00"), NOW).ok  # inside the 90 minutes


def test_cancel_needs_the_matching_phone_and_frees_the_slot(business_id):
    b = InternalBackend()
    ref = b.create(business_id, _req(), NOW).reference
    assert b.cancel(business_id, ref, "+15559999999", NOW).startswith("No upcoming")
    assert b.cancel(business_id, ref.lower(), "+15550100100", NOW).startswith("Cancelled")
    assert b.cancel(business_id, ref, "+15550100100", NOW).startswith("No upcoming")
    assert b.availability(business_id, _req(), NOW).ok


def _ctx(bid, caller=None):
    cid = None
    if caller is not None:
        with tenant_session(bid) as s:
            c = Conversation(business_id=bid, channel="voice", caller=caller)
            s.add(c)
            s.flush()
            cid = c.id
    return ActionContext(bid, cid)


def test_tool_booking_needs_confirmation_then_books(business_id):
    ctx = _ctx(business_id, caller="+1 555 010 0100")
    args = {"date": "2099-11-05", "time": "14:00", "party_size": 2, "name": "Anna"}
    assert executor.execute(ctx, "create_booking", dict(args)).startswith("Not done")
    out = executor.execute(ctx, "create_booking", {**args, "confirmed": True})
    assert out.startswith("Booked table for 2") and "Reference " in out and "Thu 05 Nov, 14:00" in out
    with tenant_session(business_id) as s:
        booking = s.scalar(select(Booking))
        assert booking.phone == "+15550100100" and booking.conversation_id == ctx.conversation_id


def test_tool_ignores_model_phone_when_caller_id_is_known(business_id):
    ctx = _ctx(business_id, caller="+15550100100")
    executor.execute(ctx, "create_booking", {"date": "2099-11-05", "time": "14:00", "name": "A",
                                             "phone": "+19998887777", "confirmed": True})
    with tenant_session(business_id) as s:
        assert s.scalar(select(Booking.phone)) == "+15550100100"


def test_tool_without_caller_id_needs_a_valid_number(business_id):
    ctx = _ctx(business_id)
    args = {"date": "2099-11-05", "time": "14:00", "name": "A", "confirmed": True}
    assert "phone number is needed" in executor.execute(ctx, "create_booking", dict(args))
    assert "phone number is needed" in executor.execute(ctx, "create_booking", {**args, "phone": "12345"})
    assert executor.execute(ctx, "create_booking", {**args, "phone": "+1 (555) 010-0200"}).startswith("Booked")


def test_tool_reports_rule_violations_with_alternatives(business_id):
    ctx = _ctx(business_id, "+15550100100")
    out = executor.execute(ctx, "create_booking", {"date": "2099-11-05", "time": "22:00", "name": "A", "confirmed": True})
    assert out.startswith("Error: not booked") and "opening hours" in out and "Free times" in out
    with tenant_session(business_id) as s:
        assert s.scalar(select(Booking.id)) is None
    assert "Not available" in executor.execute(ctx, "check_availability", {"date": "2099-11-05", "time": "07:00"})
    assert executor.execute(ctx, "check_availability", {"date": "nope", "time": "07:00"}).startswith("Not available: date")
    assert executor.execute(ctx, "check_availability", {"time": "07:00"}).startswith("Error")


def test_tool_cancel_uses_caller_id(business_id):
    ctx = _ctx(business_id, "+15550100100")
    out = executor.execute(ctx, "create_booking", {"date": "2099-11-05", "time": "14:00", "name": "A", "confirmed": True})
    ref = out.split("Reference ")[1].split(".")[0]
    assert executor.execute(_ctx(business_id, "+15550777777"), "cancel_booking", {"reference": ref, "confirmed": True}).startswith("No upcoming")
    assert executor.execute(ctx, "cancel_booking", {"reference": ref, "confirmed": True}).startswith("Cancelled")


def test_normalize_phone():
    n = booking_tools.normalize_phone
    assert n(" +1 (555) 010-0100 ") == "+15550100100"
    assert all(n(x) is None for x in (None, "", "5550100100", "+0123", "+1555"))
