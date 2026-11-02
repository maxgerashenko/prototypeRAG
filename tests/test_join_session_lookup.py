"""join_session accepts a session's name and time when the model has lost the code."""

import uuid

import pytest

from app.actions.booking import tools

ROWS = [
    {"ref": "3C785323", "name": "Sauna Polish", "when": "Mon 02 Nov, 10:00-11:00"},
    {"ref": "AAAA1111", "name": "Sauna German", "when": "Mon 02 Nov, 15:00-16:00"},
    {"ref": "BBBB2222", "name": "Sauna Polish", "when": "Mon 02 Nov, 15:00-16:00"},
]


@pytest.fixture(autouse=True)
def rows(monkeypatch):
    monkeypatch.setattr(tools.sessions_, "list_open", lambda business_id, day=None: ROWS)


@pytest.mark.parametrize("text, ref", [
    ("Sauna Polish session at 10:00 AM", "3C785323"),
    ("sauna polish 10 am", "3C785323"),
    ("Sauna German", "AAAA1111"),
    ("the Sauna German session at 3 pm", "AAAA1111"),
    ("Sauna Polish at 15:00", "BBBB2222"),
])
def test_one_match_gives_its_code(text, ref):
    assert tools.resolve_session(uuid.uuid4(), text) == (ref, "")


@pytest.mark.parametrize("text", ["Sauna Polish", "Sauna Polish at 9 am", "yoga", ""])
def test_no_or_several_matches_list_the_options(text):
    ref, message = tools.resolve_session(uuid.uuid4(), text)
    assert ref is None and message.startswith("Error: not booked.") and "·" in message or "none are open" in message


def test_a_code_is_passed_through(monkeypatch):
    seen = {}

    def join(business_id, ref, conversation_id, name, phone):
        seen["ref"] = ref
        return {"name": "Sauna Polish", "when": "x", "booked": 1, "max_attendees": 7, "booking_ref": "R1"}

    monkeypatch.setattr(tools.sessions_, "join_session", join)
    ctx = type("Ctx", (), {"business_id": uuid.uuid4(), "conversation_id": None})()
    monkeypatch.setattr(tools, "_phone", lambda ctx, args: None)
    assert "Booked John" in tools.join_session(ctx, {"session": "3c785323", "name": "John"})
    assert seen["ref"] == "3c785323"
    assert "Booked John" in tools.join_session(ctx, {"session": "Sauna Polish at 10 am", "name": "John"})
    assert seen["ref"] == "3C785323"
