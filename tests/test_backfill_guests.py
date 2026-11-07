"""Guest backfill for old bookings (needs Postgres)."""

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import delete, select

from app.actions.booking.backfill_guests import backfill_business, mask_phone
from app.db import tenant_session
from app.db.models import Booking, Business, SessionGuest


@pytest.fixture
def bid():
    b = uuid.uuid4()
    with tenant_session(b) as s:
        s.add(Business(id=b, name="Spa"))
    yield b
    with tenant_session(b) as s:
        s.execute(delete(Booking).where(Booking.business_id == b))
        s.execute(delete(SessionGuest).where(SessionGuest.business_id == b))
        s.execute(delete(Business).where(Business.id == b))


def _book(b, name, phone, hours=24, status="confirmed"):
    start = datetime.now(timezone.utc) + timedelta(hours=hours)
    with tenant_session(b) as s:
        row = Booking(business_id=b, type="appointment", starts_at=start, ends_at=start + timedelta(hours=1),
                      name=name, phone=phone, status=status)
        s.add(row)
        s.flush()
        return row.id


def _state(b):
    with tenant_session(b) as s:
        gs = s.scalars(select(SessionGuest).where(SessionGuest.business_id == b)).all()
        bs = s.scalars(select(Booking).where(Booking.business_id == b)).all()
        return len(gs), {x.id: x.guest_id for x in bs}


def test_dry_run_changes_nothing(bid):
    _book(bid, "Anna Kowalska", "+19294892284")
    assert backfill_business(bid, apply=False, out=lambda *_: None) == (1, 1)
    n, links = _state(bid)
    assert n == 0 and set(links.values()) == {None}


def test_apply_sets_guest_and_second_run_is_a_noop(bid):
    bk = _book(bid, "Anna Kowalska", "+19294892284")
    assert backfill_business(bid, apply=True, out=lambda *_: None) == (1, 1)
    n, links = _state(bid)
    assert n == 1 and links[bk] is not None
    assert backfill_business(bid, apply=True, out=lambda *_: None) == (0, 0)
    assert _state(bid) == (n, links)


def test_same_name_and_phone_share_one_guest(bid):
    a = _book(bid, "Anna Kowalska", "+1 929 489 2284")
    b = _book(bid, "anna kowalska", "+19294892284", hours=48)
    assert backfill_business(bid, apply=True, out=lambda *_: None) == (2, 1)
    n, links = _state(bid)
    assert n == 1 and links[a] == links[b]


def test_cancelled_future_booking_is_skipped_and_phone_is_masked(bid):
    _book(bid, "Bob Stone", None, status="cancelled")
    assert backfill_business(bid, apply=True, out=lambda *_: None) == (0, 0)
    assert mask_phone("+19294892284") == "***2284" and mask_phone(None) == "-"
