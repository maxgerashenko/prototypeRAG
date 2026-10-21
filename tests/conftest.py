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
