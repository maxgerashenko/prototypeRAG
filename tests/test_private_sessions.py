"""A private session is never listed or offered, but a caller who says its whole name (or its code) can join it.

Postgres only, no LLM, no Google. Names are unique to this file: the dev DB holds other sessions and list_open has no
business filter (demo rule #300)."""

import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import delete, select

from app.actions import executor
from app.actions.booking import sessions as sessions_
from app.actions.booking import tools
from app.actions.registry import ActionContext
from app.db import tenant_session
from app.db.models import Booking, Business, Conversation, GroupSession, Message
from app.rag import answer
from app.voice import tools as voice_tools  # noqa: F401  (registers every tool)

ALL = ["list_sessions", "join_session", "check_availability", "create_booking", "cancel_booking"]
NOW = datetime(2029, 12, 31, tzinfo=timezone.utc)
PRIVATE, PUBLIC = "Zorblax Invite Tasting", "Zorblax Open House"


def _add(bid, name, hour, private):
    with tenant_session(bid) as s:
        row = GroupSession(business_id=bid, name=name, status="active", private=private, max_attendees=4,
                           starts_at=datetime(2030, 1, 1, hour, tzinfo=timezone.utc),
                           ends_at=datetime(2030, 1, 1, hour + 1, tzinfo=timezone.utc))
        s.add(row)
        s.flush()
        return sessions_.session_ref(row.id)


@pytest.fixture
def world(monkeypatch):
    monkeypatch.setattr(sessions_.G, "get_client", lambda: None)
    bid, cid = uuid.uuid4(), uuid.uuid4()
    with tenant_session(bid) as s:
        s.add(Business(id=bid, name="Spa", timezone="UTC", settings={"enabled_tools": ALL}))
        s.flush()
        s.add(Conversation(id=cid, business_id=bid, channel="chat"))
        s.flush()
        s.add(Message(business_id=bid, conversation_id=cid, role="assistant", content="Shall I book it under that name?"))
    refs = {"private": _add(bid, PRIVATE, 10, True), "public": _add(bid, PUBLIC, 12, False)}
    yield ActionContext(business_id=bid, conversation_id=cid), refs
    with tenant_session(bid) as s:
        s.execute(delete(Business).where(Business.id == bid))  # cascades to its sessions, bookings and messages
        s.execute(delete(GroupSession).where(GroupSession.business_id == bid))


def _names(rows):
    return {r["name"] for r in rows}


def test_a_private_session_is_never_listed(world):
    ctx, _ = world
    rows = sessions_.list_open(ctx.business_id, now=NOW, limit=500)
    assert PUBLIC in _names(rows) and PRIVATE not in _names(rows)
    assert PRIVATE not in executor.execute(ctx, "list_sessions", {})
    assert PRIVATE not in answer.open_sessions_note(ctx.business_id, frozenset({"join_session"}))


def test_a_private_session_is_not_in_a_refusal_or_an_offer(world):
    ctx, _ = world
    out = executor.execute(ctx, "join_session", {"session": "Steam Bath", "first_name": "Jane", "last_name": "Miller"})
    assert "no session called" in out and PRIVATE not in out
    rows = sessions_.list_open(ctx.business_id, limit=500)
    offer = answer.offer_open_sessions("There is no such session.", "the steam bath", rows, ctx.business_id)
    assert PRIVATE not in offer


def test_the_whole_name_resolves_a_private_session(world):
    ctx, refs = world
    for said in (PRIVATE, "I was invited to the zorblax invite tasting, sign me up", "ZORBLAX INVITE TASTING"):
        assert tools.resolve_session(ctx.business_id, said) == (refs["private"], "")


@pytest.mark.parametrize("said", ["Zorblax", "the tasting", "Invite Tasting", "Zorblax Tasting", "Invite"])
def test_part_of_a_private_name_resolves_nothing(world, said):
    ctx, refs = world
    code, message = tools.resolve_session(ctx.business_id, said)
    assert code is None and PRIVATE not in message and refs["private"] not in message


def test_the_code_of_a_private_session_passes_the_check(world):
    ctx, refs = world
    assert tools._join_check(ctx, {"session": refs["private"], "first_name": "Jane", "last_name": "Miller"}) == (
        refs["private"], "Jane", "Miller")


def test_the_precheck_passes_for_the_whole_name_and_refuses_a_part(world):
    ctx, _ = world
    assert tools.join_session_precheck(ctx, {"session": PRIVATE, "first_name": "Jane", "last_name": "Miller"}) is None
    refused = tools.join_session_precheck(ctx, {"session": "Zorblax", "first_name": "Jane", "last_name": "Miller"})
    assert refused and PRIVATE not in refused


def test_a_booking_in_a_private_session_by_its_name(world):
    ctx, refs = world
    out = executor.execute(ctx, "join_session", {"session": PRIVATE, "first_name": "Jane", "last_name": "Miller", "confirmed": True})
    assert out.startswith("Booked") and PRIVATE in out, out
    assert "Jane Miller into " + PRIVATE in out
    with tenant_session(ctx.business_id) as s:
        row = s.scalars(select(Booking).where(Booking.business_id == ctx.business_id)).all()
    assert len(row) == 1 and row[0].status == "confirmed"


def test_a_booking_in_a_private_session_by_its_code(world):
    ctx, refs = world
    out = executor.execute(ctx, "join_session", {"session": refs["private"], "first_name": "Jane", "last_name": "Miller", "confirmed": True})
    assert out.startswith("Booked Jane Miller into " + PRIVATE), out


def test_a_public_sibling_of_the_same_name_still_resolves(world, monkeypatch):
    ctx, refs = world
    real = sessions_.list_open  # the dev DB holds more sessions than a look-up reads: read from 2029 on, keep only this file's
    monkeypatch.setattr(sessions_, "list_open", lambda *a, **k: [r for r in real(*a, **{**k, "limit": 1000, "now": NOW}) if "Zorblax" in r["name"]])
    twin = _add(ctx.business_id, PRIVATE, 14, False)  # same name as the private one, but public and listed
    assert tools.resolve_session(ctx.business_id, PRIVATE) == (twin, "")
    assert refs["private"] not in {r["ref"] for r in sessions_.list_open(ctx.business_id, limit=500)}


def test_a_private_session_that_ended_is_not_found(world):
    ctx, _ = world
    assert sessions_.find_private_by_name(ctx.business_id, PRIVATE, now=datetime(2030, 1, 3, tzinfo=timezone.utc)) == []


def test_best_guess_never_names_a_private_session(world):
    ctx, _ = world
    code, message = tools.resolve_session(ctx.business_id, "Zorblax Invite Tastin")
    assert code is None and PRIVATE not in message


def test_a_private_session_gives_its_time_for_the_read_back_but_a_public_one_does_not(world):
    ctx, refs = world
    asked = {"session": PRIVATE}
    out = tools.join_session_precheck(ctx, asked)
    assert out.startswith(tools.NAME_NEEDED) and f"{PRIVATE} on Tue 01 Jan, 10:00 AM to 11:00 AM; once you have their full name, read that back with it." in out
    full = tools.join_session_precheck(ctx, {"session": PRIVATE, "first_name": "Jane", "last_name": "Miller"})
    assert full is None  # a complete name: no refusal, the read-back note comes only with READBACK_NEEDED
    assert tools.join_session_precheck(ctx, {"session": refs["public"]}) == tools.NAME_NEEDED


def test_the_name_must_be_one_consecutive_run_of_words(world):
    ctx, refs = world
    for said in ("Tasting Invite Zorblax", "Zorblax the Invite Tasting", "Invite Zorblax Tasting"):
        assert tools.resolve_session(ctx.business_id, said)[0] is None, said
    assert tools.resolve_session(ctx.business_id, "sign me up for the zorblax invite tasting please") == (refs["private"], "")


def test_a_one_word_private_name_is_joined_by_that_word(world):
    ctx, _ = world
    one = _add(ctx.business_id, "Quixotic", 16, True)
    assert tools.resolve_session(ctx.business_id, "I was invited to the quixotic") == (one, "")
    assert tools.resolve_session(ctx.business_id, "quixot")[0] is None


def test_two_private_sessions_of_the_same_name_pick_by_time_else_the_soonest(world):
    ctx, refs = world
    later = _add(ctx.business_id, PRIVATE, 15, True)  # same name, 3:00 PM (the other is 10:00 AM)
    assert tools.resolve_session(ctx.business_id, PRIVATE) == (refs["private"], "")  # soonest
    assert tools.resolve_session(ctx.business_id, PRIVATE + " at 3 PM") == (later, "")
    assert tools.resolve_session(ctx.business_id, PRIVATE + " at 10 AM") == (refs["private"], "")
    note = tools.join_session_precheck(ctx, {"session": PRIVATE})
    assert "10:00 AM to 11:00 AM" in note
