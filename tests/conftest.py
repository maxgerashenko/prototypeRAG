"""Safety net for the shared dev DB: tests create businesses in the same database the app
uses, so a run killed before fixture teardown would leave rows in the app's business picker.
Businesses created during the run and without a domain (test businesses have none; real
crawled ones do) are deleted when the session ends."""

import pytest
from sqlalchemy import delete, select

from app.db.session import SessionLocal
from app.db.models import Business


def _ids() -> set:
    with SessionLocal() as s:
        return set(s.scalars(select(Business.id)))


@pytest.fixture(scope="session", autouse=True)
def _remove_leftover_test_businesses():
    try:
        before = _ids()
    except Exception:  # no database: tests that need it fail on their own
        yield
        return
    yield
    with SessionLocal() as s, s.begin():
        new = set(s.scalars(select(Business.id).where(Business.domain.is_(None)))) - before
        if new:
            s.execute(delete(Business).where(Business.id.in_(new)))  # cascades to tenant rows


@pytest.fixture(scope="session", autouse=True)
def _never_write_to_the_real_calendar():
    """.env turns the Google Calendar backend on for the dev app. A test that creates a session
    would then insert a real event into the shared calendar (hundreds of duplicate sessions
    piled up that way). Tests that need a calendar inject a fake with `set_client`."""
    from app.config import get_settings
    from app.actions.booking import session_events
    from app.actions.booking import tools as booking_tools
    from app.actions.booking.internal import InternalBackend

    settings = get_settings()
    saved = settings.booking_backend, booking_tools.backend
    settings.booking_backend = "internal"
    booking_tools.backend = InternalBackend()  # made at import time, with its own Google client
    session_events.set_client(None)
    yield
    settings.booking_backend, booking_tools.backend = saved
