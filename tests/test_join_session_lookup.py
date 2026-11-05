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


@pytest.mark.parametrize("text", ["yoga", ""])
def test_no_match_lists_the_options(text):
    ref, message = tools.resolve_session(uuid.uuid4(), text)
    assert ref is None and message.startswith("Error: not booked.") and "·" in message


@pytest.mark.parametrize("text, ref", [
    ("Sauna Polish", "3C785323"),  # same name twice, no time: the soonest, asked as yes/no
    ("Sound of Polish session from 10:00 a.m.", "3C785323"),  # speech-to-text garble
    ("sound a polish session", "3C785323"),
])
def test_close_words_give_one_yes_no_proposal(text, ref):
    code, message = tools.resolve_session(uuid.uuid4(), text)
    assert code is None and message.startswith("Error: not booked.")
    if ref:
        assert f"session={ref}" in message and "shall I book it" in message and "BBBB2222" not in message


def test_a_code_is_passed_through(monkeypatch):
    seen = {}

    def join(business_id, ref, conversation_id, name, phone, last_name=""):
        seen["ref"] = ref
        return {"name": "Sauna Polish", "when": "x", "booked": 1, "max_attendees": 7, "booking_ref": "R1",
                "guest": name}

    monkeypatch.setattr(tools.sessions_, "join_session", join)
    ctx = type("Ctx", (), {"business_id": uuid.uuid4(), "conversation_id": None})()
    monkeypatch.setattr(tools, "_phone", lambda ctx, args: None)
    assert "Booked John" in tools.join_session(ctx, {"session": "3c785323", "name": "John", "last_name": "Smith"})
    assert seen["ref"] == "3c785323"
    assert "Booked John" in tools.join_session(ctx, {"session": "Sauna Polish at 10 am", "name": "John", "last_name": "Smith"})
    assert seen["ref"] == "3C785323"


def test_a_first_name_alone_is_not_booked():
    ctx = type("Ctx", (), {"business_id": uuid.uuid4(), "conversation_id": None})()
    assert tools.join_session(ctx, {"session": "3C785323", "name": "Marta"}) == tools.LAST_NAME_NEEDED


def _conversation(said):
    from datetime import UTC, datetime, timedelta

    from app.db import tenant_session
    from app.db.models import Business, Conversation, Message

    bid = uuid.uuid4()
    t0 = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
    with tenant_session(bid) as s:
        s.add(Business(id=bid, name="Readback Spa"))
        c = Conversation(business_id=bid, channel="chat", started_at=t0)
        s.add(c)
        s.flush()
        for i, (role, text) in enumerate(said):
            s.add(Message(business_id=bid, conversation_id=c.id, role=role, content=text,
                          created_at=t0 + timedelta(seconds=i)))
        return bid, c.id


def _forget(bid):
    from sqlalchemy import delete

    from app.db import tenant_session
    from app.db.models import Business

    with tenant_session(bid) as s:
        s.execute(delete(Business).where(Business.id == bid))


@pytest.mark.parametrize("said, asked", [
    # the name was never read back: "My name is Tom Baker" is not a yes
    ([("user", "I'd like to book"), ("assistant", "Banya Evening on Thursday. Please give me your first and last name.")], False),
    ([("assistant", "Would you like to book a spot? Could you give me your first and last name?")], False),
    # "Your name is …" in a read-back is not a request for the name
    ([("assistant", "Sauna Polish on Thursday at 10:00. Your name is Tom Baker. Shall I book it?")], True),
    ([("assistant", "I have your name as Tom Baker. Shall I book the Sauna Polish session?")], True),
    # the caller gave the name earlier; the read-back need not repeat it
    ([("user", "Thomas Edison"), ("assistant", "Sauna Polish on Thursday at 10:00, shall I book it?")], True),
    # a read-back with the name, ending in the question
    ([("assistant", "Tom Baker for Banya Evening on Thursday at 18:00, shall I book it?")], True),
])
def test_booking_needs_a_read_back_with_the_name(said, asked):
    bid, cid = _conversation(said)
    try:
        ctx = type("Ctx", (), {"business_id": bid, "conversation_id": cid})()
        assert tools._read_back_asked(ctx, "Tom") is asked
    finally:
        _forget(bid)


@pytest.mark.parametrize("service, expected", [("table", None), ("Table Reservation", None), ("Massage", "Massage"), (None, None)])
def test_table_is_not_a_service(service, expected):
    from app.actions.booking.tools import _service

    assert _service({"service": service}) == expected


def test_readback_that_mentions_the_name_or_phone_is_a_read_back():
    for text in [
        "Table for 2 on Friday at 19:00 under Tom Baker, phone number +441234567890. Shall I book it?",
        "Is your first and last name Tom Baker, T-O-M B-A-K-E-R? Shall I book it?",
    ]:
        bid, cid = _conversation([("assistant", text)])
        try:
            ctx = type("Ctx", (), {"business_id": bid, "conversation_id": cid})()
            assert tools._read_back_asked(ctx, "Tom", tools._DETAIL_QUESTION)
        finally:
            _forget(bid)
