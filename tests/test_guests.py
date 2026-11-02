"""Guest identity: name keys, fake emails, matching by name and phone (needs Postgres)."""

import uuid

import pytest
from sqlalchemy import delete

from app.actions.booking import guests
from app.db import tenant_session
from app.db.models import Business

ID = uuid.UUID("12345678-1234-5678-1234-567812345678")


@pytest.mark.parametrize("name, key, local", [
    ("Anna Kowalska", "anna kowalska", "anna_kowalska"),
    ("Zoë Łukasiewicz-Nowak", "zoe lukasiewicz nowak", "zoe_lukasiewicz_nowak"),
    ("Straße Øster", "strasse oster", "strasse_oster"),
    ("  John   O'Neil ", "john o neil", "john_o_neil"),
    ("Иван Петров", "", "guest_12345678"),
])
def test_name_key_and_email_local_part(name, key, local):
    assert guests.name_key(name) == key
    assert guests.email_base(name, ID) == local


def test_email_local_part_is_cut_to_50_characters():
    assert len(guests.email_base("a" * 80, ID)) == 50


def test_missing_last_name_uses_the_last_four_phone_digits():
    assert guests.display_name("Anna", "", "+1 (929) 489-2284") == "Anna 2284"
    assert guests.display_name("Anna", "", None) == "Anna"
    assert guests.display_name("Anna", " Kowalska ", "+19294892284") == "Anna Kowalska"


@pytest.fixture
def business_id():
    bid = uuid.uuid4()
    with tenant_session(bid) as s:
        s.add(Business(id=bid, name="Spa"))
    yield bid
    with tenant_session(bid) as s:
        s.execute(delete(Business).where(Business.id == bid))


def _guest(bid, first, last="", phone=None):
    with tenant_session(bid) as s:
        g = guests.find_or_create(s, bid, first, last, phone)
        return g.id, g.email, g.display_name


def test_same_name_and_phone_is_the_same_guest(business_id):
    a = _guest(business_id, "Anna", "Kowalska", "+19294892284")
    assert a[1] == "anna_kowalska@guest.invalid"
    assert _guest(business_id, "anna", "KOWALSKA", "+1 929 489 2284") == a
    assert _guest(business_id, "Zoë", "Łukasiewicz")[2] == "Zoë Łukasiewicz"


def test_a_missing_phone_on_either_side_still_matches(business_id):
    a = _guest(business_id, "Anna", "Kowalska", "+19294892284")
    assert _guest(business_id, "Anna", "Kowalska")[0] == a[0]
    b = _guest(business_id, "Ben", "Lee")
    assert _guest(business_id, "Ben", "Lee", "+16468911315")[0] == b[0]


def test_same_name_with_a_different_phone_is_a_new_guest_with_a_numbered_email(business_id):
    a = _guest(business_id, "John", "Smith", "+19294892284")
    b = _guest(business_id, "John", "Smith", "+16468911315")
    c = _guest(business_id, "John", "Smith", "+12678022560")
    assert a[0] != b[0] != c[0]
    assert [a[1], b[1], c[1]] == ["john_smith@guest.invalid", "john_smith_2@guest.invalid", "john_smith_3@guest.invalid"]
    assert _guest(business_id, "John", "Smith", "+16468911315")[0] == b[0]


def test_a_near_match_is_a_new_guest(business_id):
    assert _guest(business_id, "Katherine", "Smith")[0] != _guest(business_id, "Catherine", "Smith")[0]


def test_guests_are_per_business(business_id):
    other = uuid.uuid4()
    with tenant_session(other) as s:
        s.add(Business(id=other, name="Other"))
    try:
        assert _guest(business_id, "Anna", "K")[0] != _guest(other, "Anna", "K")[0]
        assert _guest(other, "Anna", "K")[1] == "anna_k@guest.invalid"
    finally:
        with tenant_session(other) as s:
            s.execute(delete(Business).where(Business.id == other))


def test_a_merged_away_guest_is_not_matched(business_id):
    a = _guest(business_id, "Anna", "K")
    b = _guest(business_id, "Anna", "K", "+19294892284")
    assert a[0] == b[0]
    with tenant_session(business_id) as s:
        keep = guests.find_or_create(s, business_id, "Anna", "Kowalska")
        old = s.get(guests.SessionGuest, a[0])
        old.merged_into = keep.id
    assert _guest(business_id, "Anna", "K")[0] != a[0]


def test_a_name_with_no_latin_letters_is_always_a_new_guest(business_id):
    a, b = _guest(business_id, "Иван", "Петров"), _guest(business_id, "Иван", "Петров")
    assert a[0] != b[0] and a[1].startswith("guest_") and a[1] != b[1]


def test_join_session_gives_the_seat_to_a_guest(business_id):
    from datetime import datetime, timedelta, timezone

    from sqlalchemy import select

    from app.actions.booking import sessions
    from app.db.models import Booking, Conversation, GroupSession

    start = (datetime.now(timezone.utc) + timedelta(days=2)).replace(minute=0, second=0, microsecond=0)
    with tenant_session(business_id) as s:
        row = GroupSession(business_id=business_id, starts_at=start, ends_at=start + timedelta(hours=1),
                           name="Banya", max_attendees=2)
        s.add(row)
        s.flush()
        ref = sessions.session_ref(row.id)
        conversation = Conversation(business_id=business_id, channel="chat")
        s.add(conversation)
        s.flush()
        conv = conversation.id
    r = sessions.join_session(business_id, ref, conv, "Anna", "+19294892284", last_name="Kowalska")
    assert r["guest"] == "Anna Kowalska"
    with pytest.raises(sessions.SessionError, match="already has a seat"):
        sessions.join_session(business_id, ref, conv, "Anna Kowalska", "+19294892284")
    with tenant_session(business_id) as s:
        b = s.scalars(select(Booking).where(Booking.business_id == business_id)).one()
        g = s.get(guests.SessionGuest, b.guest_id)
        assert (b.name, b.caller_conversation_id, g.email) == ("Anna Kowalska", conv, "anna_kowalska@guest.invalid")
