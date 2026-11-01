"""Session JSON shape for the day view (no database): imported Google events can be any length."""

import uuid
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from app.actions.booking.sessions import _item
from app.db.models import GroupSession

NY = ZoneInfo("America/New_York")


def _row(minutes=60, **extra):
    start = datetime(2026, 11, 5, 19, 0, tzinfo=timezone.utc)  # 14:00 New York
    return GroupSession(
        id=uuid.uuid4(), business_id=uuid.uuid4(), starts_at=start, ends_at=start + timedelta(minutes=minutes),
        name="Yoga", max_attendees=8, private=False, **extra,
    )


def test_item_defaults_to_app_source_and_keeps_business_id():
    row = _row()
    item = _item(row, [], NY)
    assert item["source"] in (None, "app")  # the column default applies on insert
    assert item["business_id"] == str(row.business_id)
    assert (item["start_min"], item["end_min"]) == (14 * 60, 15 * 60)


def test_item_uses_the_event_length():
    assert _item(_row(90), [], NY)["end_min"] == 14 * 60 + 90


def test_item_is_cut_at_midnight():
    row = _row(24 * 60)
    assert _item(row, [], NY)["end_min"] == 24 * 60
